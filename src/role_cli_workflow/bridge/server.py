"""Role-scoped stdio MCP entry point."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..config import ESCALATION_CONTROLLER
from ..control import ControlError
from .config import WORKER_ROLES
from .security import ValidationError, validate_caller_role
from .state import StateError, TaskStore
from .tmux_client import TmuxError

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def _safe(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"success": True, **call()}
    except (ControlError, ValidationError, StateError, TmuxError) as exc:
        return {"success": False, "error": str(exc)}
    except Exception:
        return {"success": False, "error": "internal bridge error"}


def create_mcp(caller_role: str, store_factory: Callable[[str], TaskStore] = TaskStore) -> FastMCP:
    caller_role = validate_caller_role(caller_role)
    bridge = FastMCP("role-cli-workflow", instructions=f"Fixed coordination tools for role {caller_role}.")
    store: TaskStore | None = None
    def get_store() -> TaskStore:
        nonlocal store
        if store is None:
            store = store_factory(caller_role)
        return store
    if caller_role == "supervisor":
        @bridge.tool(annotations=READ_ONLY)
        def list_roles() -> dict[str, Any]: return _safe(get_store().list_roles)
        @bridge.tool(annotations=READ_ONLY)
        def get_control_state() -> dict[str, Any]: return _safe(get_store().get_control_state)
        @bridge.tool(annotations=LOCAL_WRITE)
        def assign_task(workflow_id: str, role: str, task_id: str, objective: str, deliverables: list[str], acceptance_criteria: list[str], constraints: list[str], context_refs: list[dict[str, Any]]) -> dict[str, Any]:
            return _safe(lambda: get_store().assign_task(workflow_id, role, task_id, objective, deliverables, acceptance_criteria, constraints, context_refs))
        @bridge.tool(annotations=LOCAL_WRITE)
        def cancel_task(task_id: str, reason: str) -> dict[str, Any]:
            return _safe(lambda: get_store().cancel_task(task_id, reason))
        @bridge.tool(annotations=LOCAL_WRITE)
        def retry_dispatch(task_id: str) -> dict[str, Any]:
            return _safe(lambda: get_store().retry_dispatch(task_id))
        @bridge.tool(annotations=LOCAL_WRITE)
        def retry_callback(task_id: str) -> dict[str, Any]:
            return _safe(lambda: get_store().retry_callback(task_id))
        @bridge.tool(annotations=READ_ONLY)
        def get_task_result(task_id: str, section: str | None = None) -> dict[str, Any]: return _safe(lambda: get_store().get_task_result(task_id, section))
        @bridge.tool(annotations=LOCAL_WRITE)
        def send_rework(task_id: str, missing_items: list[str] | None = None, failed_acceptance_criteria: list[str] | None = None, required_changes: list[str] | None = None, clarified_information: str = "", added_context_refs: list[dict[str, Any]] | None = None, required_next_action: str = "", event_kind: str = "", invariant_type: str = "") -> dict[str, Any]:
            return _safe(lambda: get_store().send_rework(task_id, missing_items, failed_acceptance_criteria, required_changes, clarified_information, added_context_refs, required_next_action, event_kind, invariant_type))
        @bridge.tool(annotations=LOCAL_WRITE)
        def record_decision(decision_id: str, title: str, decision: str, reason: str, scope: str, affected_refs: list[str], supersedes: list[str] | None = None, replacement: str = "", authority_ref: str = "") -> dict[str, Any]:
            return _safe(lambda: get_store().record_decision(decision_id, title, decision, reason, scope, affected_refs, supersedes, replacement, authority_ref))
    elif caller_role == ESCALATION_CONTROLLER:
        @bridge.tool(annotations=READ_ONLY)
        def list_roles() -> dict[str, Any]: return _safe(get_store().list_roles)
        @bridge.tool(annotations=READ_ONLY)
        def get_control_state() -> dict[str, Any]: return _safe(get_store().get_control_state)
        @bridge.tool(annotations=READ_ONLY)
        def get_task_result(task_id: str, section: str | None = None) -> dict[str, Any]: return _safe(lambda: get_store().get_task_result(task_id, section))
        @bridge.tool(annotations=LOCAL_WRITE)
        def record_decision(decision_id: str, title: str, decision: str, reason: str, scope: str, affected_refs: list[str], supersedes: list[str] | None = None, replacement: str = "", authority_ref: str = "") -> dict[str, Any]:
            return _safe(lambda: get_store().record_decision(decision_id, title, decision, reason, scope, affected_refs, supersedes, replacement, authority_ref))
        @bridge.tool(annotations=LOCAL_WRITE)
        def release_control(intervention_id: str, summary: str) -> dict[str, Any]:
            return _safe(lambda: get_store().release_control(intervention_id, summary))
    elif caller_role in WORKER_ROLES:
        @bridge.tool(annotations=LOCAL_WRITE)
        def get_current_task() -> dict[str, Any]: return _safe(get_store().get_current_task)
        @bridge.tool(annotations=LOCAL_WRITE)
        def get_context(ref_id: str, section: str) -> dict[str, Any]: return _safe(lambda: get_store().get_context(ref_id, section))
        @bridge.tool(annotations=LOCAL_WRITE)
        def submit_result(workflow_id: str, task_id: str, round: int, nonce: str, summary: str, decisions: list[Any], open_issues: list[Any], evidence: list[Any], sections: dict[str, Any], source_refs: list[dict[str, str]]) -> dict[str, Any]:
            return _safe(lambda: get_store().submit_result(workflow_id, task_id, round, nonce, summary, decisions, open_issues, evidence, sections, source_refs))
        @bridge.tool(annotations=LOCAL_WRITE)
        def report_blocked(workflow_id: str, task_id: str, round: int, nonce: str, reason: str) -> dict[str, Any]:
            return _safe(lambda: get_store().report_blocked(workflow_id, task_id, round, nonce, reason))
    return bridge


def main() -> None:
    try:
        role = validate_caller_role(os.environ.get("ROLE_CLI_WORKFLOW_ROLE"))
        create_mcp(role).run(transport="stdio")
    except ValidationError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        print(f"role-cli-workflow initialization failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__": main()
