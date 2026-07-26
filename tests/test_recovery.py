from __future__ import annotations

import importlib
from pathlib import Path

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.bridge.models import RoleConfig
from role_cli_workflow.config import load_project


class Pane:
    def __init__(self, pane_id: str) -> None:
        self.pane_id = pane_id
        self.pane_pid = 1
        self.dead = False


class FakeTmux:
    def __init__(self, *, fail_paste: bool = False, pane_id: str = "%1") -> None:
        self.fail_paste = fail_paste
        self.pane_id = pane_id
        self.pastes = 0
        self.pasted_texts: list[str] = []

    def fixed_pane(self, role: RoleConfig) -> Pane:
        return Pane(self.pane_id)

    def paste(self, pane_id: str, text: str, buffer_name: str) -> None:
        self.pastes += 1
        self.pasted_texts.append(text)
        if self.fail_paste:
            from role_cli_workflow.bridge.tmux_client import TmuxError
            raise TmuxError("temporary failure")

    def probe(self, role: RoleConfig):
        return "ALIVE", Pane(self.pane_id)


class FakeDocuments:
    def project_contract(self, contract):
        return "contract.json"

    def refresh(self, records, decisions=(), role_statuses=()):
        list(records)
        list(decisions)
        list(role_statuses)


def store_for(project_root: Path, monkeypatch, *, caller: str, tmux: FakeTmux):
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    state = importlib.import_module("role_cli_workflow.bridge.state")
    config = load_project(project_root)
    runtime = config.runtime_root
    metadata = runtime / "metadata"
    tasks = runtime / "tasks"
    decisions = runtime / "decisions"
    logs = runtime / "logs"
    for path in (metadata, tasks, decisions, logs):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    monkeypatch.setattr(state, "METADATA_DIR", metadata)
    monkeypatch.setattr(state, "ACTIVE_TASKS_PATH", metadata / "active-tasks.json")
    monkeypatch.setattr(state, "TASKS_DIR", tasks)
    monkeypatch.setattr(state, "DECISIONS_DIR", decisions)
    monkeypatch.setattr(state, "LOG_DIR", logs)
    monkeypatch.setattr(state, "WORKFLOW_ROOT", config.workflow_root)
    store = state.TaskStore.__new__(state.TaskStore)
    store.caller_role = caller
    store.roles = {
        "supervisor": RoleConfig("supervisor", "session", "supervisor", str(config.repo("supervisor"))),
        "explorer": RoleConfig("explorer", "session", "explorer", str(config.repo("explorer"))),
    }
    store.tmux = tmux
    store.documents = FakeDocuments()
    return store


def test_wakeup_failure_is_retryable_and_running_task_can_be_cancelled(
    project_root: Path, monkeypatch
) -> None:
    init_project(project_root, assume_yes=True)
    tmux = FakeTmux(fail_paste=True)
    store = store_for(project_root, monkeypatch, caller="supervisor", tmux=tmux)
    monkeypatch.setattr(store, "_effective_authority", lambda workflow_id: {"status": "LEGACY_AUTHORITY_WARNING"})
    result = store.assign_task(
        "wf-recovery", "explorer", "task-recovery", "Inspect recovery",
        ["report"], ["task remains durable"], [], [],
    )
    assert result["status"] == "DISPATCHED"
    assert result["warnings"] == ["LEGACY_AUTHORITY_WARNING", "WAKEUP_PENDING"]
    assert store._read_record("task-recovery").status == "RUNNING"

    tmux.fail_paste = False
    tmux.pane_id = "%2"
    retried = store.retry_dispatch("task-recovery")
    assert retried["status"] == "DISPATCHED"
    record = store._read_record("task-recovery")
    assert record.error_code == ""
    assert record.pane_id == "%2"

    cancelled = store.cancel_task("task-recovery", "User replaced the task.")
    assert cancelled["status"] == "IDLE"
    assert store._read_record("task-recovery").status == "CANCELLED"
    assert store._active_for_role("explorer") is None


def test_repeated_blocked_report_retries_pending_callback(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    tmux = FakeTmux()
    store = store_for(project_root, monkeypatch, caller="explorer", tmux=tmux)
    from role_cli_workflow.bridge.models import TaskRecord
    record = TaskRecord(
        workflow_id="wf-blocked", task_id="task-blocked", role="explorer",
        nonce="0" * 32, round=0, rework_count=0,
        created_at="2026-07-26T00:00:00Z", updated_at="2026-07-26T00:00:00Z",
        status="BLOCKED", pane_id="%1", contract_length=1, contract_sha256="a" * 64,
        blocked_reason="Need an authorized local file.", callback_status="PENDING",
        execution_status="BLOCKED",
    )
    store._write_record(record)
    result = store.report_blocked(
        "wf-blocked", "task-blocked", 0, "0" * 32,
        "Need an authorized local file.",
    )
    assert result["already_submitted"] is True
    assert store._read_record("task-blocked").callback_status == "DELIVERED"
    assert tmux.pastes == 1
    callback = tmux.pasted_texts[0]
    assert "Workflow: wf-blocked" in callback
    assert "Task: task-blocked" in callback
    assert "Role: explorer" in callback
    assert "Status: BLOCKED" in callback
    assert "get_task_result for task task-blocked" in callback


def test_terminal_task_id_remains_visible_in_dashboard(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    documents = importlib.import_module("role_cli_workflow.bridge.documents")
    config = load_project(project_root)
    monkeypatch.setattr(documents, "WORKFLOW_ROOT", config.workflow_root)
    from role_cli_workflow.bridge.models import TaskRecord
    record = TaskRecord(
        workflow_id="wf-dashboard", task_id="task-terminal", role="explorer",
        nonce="0" * 32, round=0, rework_count=0,
        created_at="2026-07-26T00:00:00Z", updated_at="2026-07-26T00:00:00Z",
        status="BLOCKED", pane_id="%1", contract_length=1, contract_sha256="a" * 64,
        blocked_reason="Need user input.", callback_status="PENDING",
        execution_status="BLOCKED",
    )
    documents.WorkflowDocuments().refresh(
        [record],
        role_statuses=[{
            "role": "explorer",
            "active_task_id": None,
            "latest_task_id": "task-terminal",
            "derived_display_status": "BLOCKED",
            "callback_status": "PENDING",
        }],
    )
    dashboard = (config.workflow_root / "current_task.md").read_text(encoding="utf-8")
    assert "| Role | Process | Task |" in dashboard
    assert "| explorer | UNKNOWN | `task-terminal` | BLOCKED | PENDING |" in dashboard


def test_standard_rework_skips_contract_sensitive_semantic_gate(
    project_root: Path, monkeypatch
) -> None:
    init_project(project_root, assume_yes=True)
    store = store_for(project_root, monkeypatch, caller="supervisor", tmux=FakeTmux())
    preflight = store._refinement_path("wf-standard", "preflight.json")
    store._write_json(preflight, {"workflow_id": "wf-standard", "profile": "STANDARD"})
    assert store._semantic_repair_checkpoint(
        "wf-standard", "IMPLEMENTATION_REVISION", ""
    ) == ""
