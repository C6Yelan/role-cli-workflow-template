"""Role-scoped stdio MCP entry point."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import WORKER_ROLES
from .security import ValidationError, validate_caller_role
from .state import StateError, TaskStore
from .tmux_client import TmuxError

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def _safe(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"success": True, **call()}
    except (ValidationError, StateError, TmuxError) as exc:
        return {"success": False, "error": str(exc)}
    except Exception:
        return {"success": False, "error": "internal bridge error"}


def create_mcp(caller_role: str, store_factory: Callable[[str], TaskStore] = TaskStore) -> FastMCP:
    caller_role = validate_caller_role(caller_role)
    bridge = FastMCP("role-cli-workflow", instructions=f"Coordination tools for role {caller_role}.")
    store: TaskStore | None = None

    def get_store() -> TaskStore:
        nonlocal store
        if store is None:
            store = store_factory(caller_role)
        return store

    if caller_role == "supervisor":
        @bridge.tool(annotations=READ_ONLY)
        def list_roles() -> dict[str, Any]: return _safe(get_store().list_roles)

        @bridge.tool(annotations=LOCAL_WRITE)
        def assign_task(workflow_id: str, role: str, task_id: str, objective: str, deliverables: list[str], acceptance_criteria: list[str], constraints: list[str], authorized_refs: list[str], input_candidate_sha: str = "") -> dict[str, Any]:
            return _safe(lambda: get_store().assign_task(workflow_id, role, task_id, objective, deliverables, acceptance_criteria, constraints, authorized_refs, input_candidate_sha))

        @bridge.tool(annotations=LOCAL_WRITE)
        def cancel_task(task_id: str, reason: str) -> dict[str, Any]: return _safe(lambda: get_store().cancel_task(task_id, reason))

        @bridge.tool(annotations=LOCAL_WRITE)
        def retry_dispatch(task_id: str) -> dict[str, Any]: return _safe(lambda: get_store().retry_dispatch(task_id))

        @bridge.tool(annotations=LOCAL_WRITE)
        def retry_callback(task_id: str) -> dict[str, Any]: return _safe(lambda: get_store().retry_callback(task_id))

        @bridge.tool(annotations=READ_ONLY)
        def get_task_result(task_id: str, view: str = "full") -> dict[str, Any]: return _safe(lambda: get_store().get_task_result(task_id, view))

        @bridge.tool(annotations=LOCAL_WRITE)
        def accept_task(task_id: str, candidate_sha: str = "") -> dict[str, Any]: return _safe(lambda: get_store().accept_task(task_id, candidate_sha))

        @bridge.tool(annotations=LOCAL_WRITE)
        def send_rework(task_id: str, required_changes: list[str], clarified_information: str = "") -> dict[str, Any]:
            return _safe(lambda: get_store().send_rework(task_id, required_changes, clarified_information))

        @bridge.tool(annotations=LOCAL_WRITE)
        def record_decision(decision_id: str, title: str, decision: str, reason: str, scope: str, affected_refs: list[str]) -> dict[str, Any]:
            return _safe(lambda: get_store().record_decision(decision_id, title, decision, reason, scope, affected_refs))
    elif caller_role in WORKER_ROLES:
        @bridge.tool(annotations=LOCAL_WRITE)
        def get_current_task() -> dict[str, Any]: return _safe(get_store().get_current_task)

        @bridge.tool(annotations=READ_ONLY)
        def get_context(ref_id: str, view: str = "summary") -> dict[str, Any]: return _safe(lambda: get_store().get_context(ref_id, view))

        @bridge.tool(annotations=LOCAL_WRITE)
        def submit_result(workflow_id: str, task_id: str, round: int, nonce: str, summary: str, decisions: list[Any], open_issues: list[Any], evidence: list[Any], sections: dict[str, Any], source_refs: list[str], produced_candidate_sha: str = "") -> dict[str, Any]:
            return _safe(lambda: get_store().submit_result(workflow_id, task_id, round, nonce, summary, decisions, open_issues, evidence, sections, source_refs, produced_candidate_sha))

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


if __name__ == "__main__":
    main()
