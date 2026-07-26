from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project


def test_role_tool_matrix(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    server = importlib.import_module("role_cli_workflow.bridge.server")
    expected_worker = {"get_current_task", "get_context", "submit_result", "report_blocked"}
    expected_supervisor = {
        "list_roles", "assign_task", "cancel_task", "retry_dispatch",
        "retry_callback", "get_task_result", "send_rework", "record_decision",
    }
    for role in ("supervisor", "explorer", "implementer", "evaluator", "reviewer", "doc-curator"):
        names = {tool.name for tool in server.create_mcp(role)._tool_manager.list_tools()}
        assert names == (expected_supervisor if role == "supervisor" else expected_worker)


def test_bridge_uses_generic_trigger_and_task_contract(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    state = importlib.import_module("role_cli_workflow.bridge.state")
    assert "ROLE_CLI_WORKFLOW_TASK_AVAILABLE" in state.TASK_TRIGGER
    assert len(state.TASK_TRIGGER) < 256
    assert "objective" not in state.TASK_TRIGGER


@pytest.mark.parametrize(
    ("role", "sections"),
    [
        ("evaluator", {}),
        ("evaluator", {"validation_verdict": "UNKNOWN"}),
        ("reviewer", {"proportionality_verdict": "PROPORTIONATE"}),
        ("reviewer", {"correctness_verdict": "PASS"}),
        (
            "reviewer",
            {
                "correctness_verdict": "PASS",
                "proportionality_verdict": "TOO_COMPLEX",
            },
        ),
    ],
)
def test_result_envelope_rejects_invalid_role_verdicts(
    project_root: Path,
    monkeypatch,
    role: str,
    sections: dict[str, object],
) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    contracts = importlib.import_module("role_cli_workflow.bridge.contracts")
    security = importlib.import_module("role_cli_workflow.bridge.security")
    with pytest.raises(security.ValidationError, match="verdict is invalid"):
        contracts.validate_result_envelope(
            "wf-verdict", role, f"task-{role}", 0, "0" * 32, "Result",
            [], [], [], sections, [], {},
        )


@pytest.mark.parametrize(
    ("role", "sections"),
    [
        ("evaluator", {"validation_verdict": "NOT_VERIFIED"}),
        (
            "reviewer",
            {
                "review_mode": "DESIGN_PRECHECK",
                "correctness_verdict": "PASS",
                "proportionality_verdict": "UNCERTAIN",
            },
        ),
    ],
)
def test_result_envelope_accepts_valid_role_verdicts(
    project_root: Path,
    monkeypatch,
    role: str,
    sections: dict[str, object],
) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    contracts = importlib.import_module("role_cli_workflow.bridge.contracts")
    envelope = contracts.validate_result_envelope(
        "wf-verdict", role, f"task-{role}", 0, "0" * 32, "Result",
        [], [], [], sections, [], {},
    )
    assert envelope["sections"] == sections


def test_send_rework_exposes_existing_semantic_checkpoint_field(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    server = importlib.import_module("role_cli_workflow.bridge.server")
    tool = next(
        item for item in server.create_mcp("supervisor")._tool_manager.list_tools()
        if item.name == "send_rework"
    )
    assert "invariant_type" in tool.parameters["properties"]


def test_long_command_protocol_is_single_sourced_and_deployed(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    source = Path(__file__).parents[1] / "templates/roles/long-command-protocol.md"
    marker = "preserve that exact ID"
    assert marker in source.read_text(encoding="utf-8")
    for role in ("supervisor", "explorer", "implementer", "evaluator", "reviewer", "doc-curator"):
        deployed = project_root / "shared_workspace/roles" / f"{role}.md"
        assert deployed.read_text(encoding="utf-8").count(marker) == 1
