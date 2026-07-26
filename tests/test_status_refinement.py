from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.bridge.models import RoleConfig, TaskRecord


class Probe:
    def __init__(self, status: str) -> None:
        self.status = status

    def probe(self, role: RoleConfig):
        return self.status, None


def record(activity: str, execution: str = "RUNNING") -> TaskRecord:
    return TaskRecord(
        workflow_id="wf-1", task_id="task-1", role="explorer", nonce="0" * 32,
        round=0, rework_count=0, created_at=activity, updated_at=activity,
        status="RUNNING", pane_id="%1", contract_length=1,
        contract_sha256="a" * 64, execution_status=execution,  # type: ignore[arg-type]
        dispatched_at=activity, started_at=activity, last_activity_at=activity,
        last_activity_kind="GET_CONTEXT",
    )


def store_for(module, status: str, latest: TaskRecord):
    store = module.TaskStore.__new__(module.TaskStore)
    store.roles = {"explorer": RoleConfig("explorer", "s", "explorer", "/tmp/explorer")}
    store.tmux = Probe(status)
    store._latest_for_role = lambda role: latest  # type: ignore[method-assign]
    store._active_for_role = lambda role: latest  # type: ignore[method-assign]
    return store


def test_recent_mcp_activity_prevents_false_down_and_keeps_execution_separate(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    module = importlib.import_module("role_cli_workflow.bridge.state")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    row = store_for(module, "DOWN", record(now))._role_rows()[0]
    assert row["process_status"] == "UNKNOWN"
    assert row["execution_status"] == "RUNNING"
    assert row["last_activity_kind"] == "GET_CONTEXT"


def test_uncertain_probe_is_unknown_not_down(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    module = importlib.import_module("role_cli_workflow.bridge.state")
    row = store_for(module, "UNKNOWN", record("", "DISPATCHED"))._role_rows()[0]
    assert row["process_status"] == "UNKNOWN"
    assert row["execution_status"] == "DISPATCHED"


def test_supervisor_prompt_uses_compact_callbacks_and_separate_telemetry() -> None:
    path = Path(__file__).parents[1] / "templates/roles/supervisor.md"
    text = path.read_text(encoding="utf-8")
    assert "Keep callback output compact" in text
    assert "Pane/process" in text
    assert "Show the full role table only" in text
