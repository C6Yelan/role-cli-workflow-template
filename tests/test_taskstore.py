from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.bridge.models import RoleConfig
from role_cli_workflow.project import git


class Pane:
    pane_id = "%1"


class FakeTmux:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.pastes = 0

    def fixed_pane(self, role):
        if self.fail:
            from role_cli_workflow.bridge.tmux_client import TmuxError
            raise TmuxError("transport down")
        return Pane()

    def paste(self, pane_id, text, buffer_name):
        self.pastes += 1
        if self.fail:
            from role_cli_workflow.bridge.tmux_client import TmuxError
            raise TmuxError("transport down")

    def probe(self, role): return ("UNKNOWN", None)


class FakeDocuments:
    fail_projection = False
    def refresh(self, *args, **kwargs): pass
    def project_result(self, *args, **kwargs):
        if self.fail_projection:
            from role_cli_workflow.bridge.documents import DocumentError
            raise DocumentError("projection failed")
        return []


def stores(project_root: Path, monkeypatch):
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    for name in list(sys.modules):
        if name.startswith("role_cli_workflow.bridge."):
            sys.modules.pop(name)
    state = importlib.import_module("role_cli_workflow.bridge.state")
    supervisor = state.TaskStore("supervisor")
    supervisor.tmux = FakeTmux()
    supervisor.documents = FakeDocuments()
    return state, supervisor


def worker(state, supervisor, role: str):
    value = state.TaskStore(role)
    value.tmux = supervisor.tmux
    value.documents = supervisor.documents
    return value


def test_identity_fields_are_hard_gates(project_root: Path, monkeypatch) -> None:
    state, supervisor = stores(project_root, monkeypatch)
    assigned = supervisor.assign_task("wf-one", "explorer", "task-one", "inspect", ["report"], ["clear"], [], [])
    explorer = worker(state, supervisor, "explorer")
    current = explorer.get_current_task()
    cases = [
        ("wf-other", "task-one", 0, current["nonce"], "wrong workflow"),
        ("wf-one", "missing", 0, current["nonce"], "task record"),
        ("wf-one", "task-one", 1, current["nonce"], "wrong round"),
        ("wf-one", "task-one", 0, "f" * 32, "wrong nonce"),
    ]
    for workflow_id, task_id, round_number, nonce, message in cases:
        with pytest.raises(state.StateError, match=message):
            explorer.report_blocked(workflow_id, task_id, round_number, nonce, "blocked")
    implementer = worker(state, supervisor, "implementer")
    with pytest.raises(state.StateError, match="wrong role"):
        implementer.report_blocked("wf-one", "task-one", 0, current["nonce"], "blocked")


def test_wakeup_and_restart_reuse_same_task_identity(project_root: Path, monkeypatch) -> None:
    state, supervisor = stores(project_root, monkeypatch)
    supervisor.tmux.fail = True
    assigned = supervisor.assign_task("wf-recovery", "explorer", "task-recovery", "inspect", ["report"], ["clear"], [], [])
    assert assigned["warning"] == "WAKEUP_PENDING"
    original_nonce = assigned["nonce"]
    supervisor.tmux.fail = False
    retried = supervisor.retry_dispatch("task-recovery")
    assert retried["task_id"] == "task-recovery" and retried["nonce"] == original_nonce
    restarted = worker(state, supervisor, "explorer")
    assert restarted.get_current_task()["nonce"] == original_nonce


def test_callback_retry_and_projection_failure_do_not_invalidate_result(project_root: Path, monkeypatch) -> None:
    state, supervisor = stores(project_root, monkeypatch)
    assigned = supervisor.assign_task("wf-result", "explorer", "task-result", "inspect", ["report"], ["clear"], [], [])
    explorer = worker(state, supervisor, "explorer")
    explorer.get_current_task()
    explorer.tmux.fail = True
    explorer.documents.fail_projection = True
    result = explorer.submit_result("wf-result", "task-result", 0, assigned["nonce"], "done", [], [], [], {}, [])
    assert result["status"] == "RESULT_READY" and result["warning"] == "PROJECTION_WARNING"
    supervisor.tmux.fail = False
    assert supervisor.retry_callback("task-result")["result_notice_status"] == "DELIVERED"


def test_multiple_authorized_context_refs_work(project_root: Path, monkeypatch) -> None:
    state, supervisor = stores(project_root, monkeypatch)
    supervisor.record_decision("one", "One", "first", "needed", "workflow", [])
    supervisor.record_decision("two", "Two", "second", "needed", "workflow", [])
    supervisor.assign_task(
        "wf-context", "explorer", "task-context", "inspect", ["report"], ["clear"], [],
        ["decision:one", "decision:two"],
    )
    explorer = worker(state, supervisor, "explorer")
    explorer.get_current_task()
    assert explorer.get_context("decision:one")["content"]["decision"] == "first"
    assert explorer.get_context("decision:two", "full")["content"]["decision"] == "second"
    with pytest.raises(state.StateError, match="not authorized"):
        explorer.get_context("decision:three")


def test_single_writer_rejects_overlap(project_root: Path, monkeypatch) -> None:
    state, supervisor = stores(project_root, monkeypatch)
    base = git(supervisor.project.repo("supervisor"), "rev-parse", "refs/heads/dev").stdout.strip()
    supervisor.assign_task("wf-write", "implementer", "task-write", "change", ["commit"], ["works"], [], [], base)
    with pytest.raises(state.StateError, match="writer"):
        supervisor.assign_task("wf-doc", "doc-curator", "task-doc", "document", ["docs"], ["clear"], [], [], base)
