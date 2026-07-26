"""Deterministic human-readable projections of TaskStore state."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Iterable

from .contracts import SUGGESTED_ROLE_SECTIONS, canonical_json
from .models import TaskRecord
from .security import validate_decision_id, validate_workflow_id
from .config import WORKFLOW_ROOT

ROLE_DOCUMENTS = {
    "explorer": "plan.md",
    "implementer": "implementer_report.md",
    "evaluator": "evaluation_report.md",
    "reviewer": "review_report.md",
    "doc-curator": "final_summary.md",
}


class DocumentError(RuntimeError):
    """A non-fatal document projection failure."""


def _display(value: object) -> str:
    if isinstance(value, str):
        return value
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


class WorkflowDocuments:
    def _atomic_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise DocumentError("document projection failed") from exc

    def workflow_dir(self, workflow_id: str) -> Path:
        validate_workflow_id(workflow_id)
        path = WORKFLOW_ROOT / "tasks" / workflow_id
        if path.parent != WORKFLOW_ROOT / "tasks":
            raise DocumentError("workflow document path is invalid")
        return path

    def ensure_workflow(self, workflow_id: str) -> Path:
        root = self.workflow_dir(workflow_id)
        for path in (
            root,
            root / "data",
            root / "data" / "task-contracts",
            root / "data" / "results",
        ):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)
        for role, name in ROLE_DOCUMENTS.items():
            document = root / name
            if not document.exists():
                self._atomic_text(
                    document,
                    f"# {role.replace('-', ' ').title()} Result\n\nNo validated result.\n",
                )
        return root

    def project_contract(self, contract: dict[str, object]) -> str:
        root = self.ensure_workflow(str(contract["workflow_id"]))
        path = root / "data" / "task-contracts" / f"{contract['task_id']}.json"
        self._atomic_text(path, canonical_json(contract) + "\n")
        return str(path.relative_to(WORKFLOW_ROOT))

    def project_result(self, record: TaskRecord, envelope: dict[str, object]) -> list[str]:
        root = self.ensure_workflow(record.workflow_id)
        json_path = root / "data" / "results" / f"{record.task_id}-round-{record.round}.json"
        self._atomic_text(json_path, canonical_json(envelope) + "\n")
        document_path = root / ROLE_DOCUMENTS[record.role]
        sections = envelope.get("sections", {})
        assert isinstance(sections, dict)
        lines = [
            f"# {record.role.replace('-', ' ').title()} Result",
            "",
            f"- Workflow ID: `{record.workflow_id}`",
            f"- Task ID: `{record.task_id}`",
            f"- Role: `{record.role}`",
            f"- Round: `{record.round}`",
            f"- Status: `{record.status}`",
            f"- Updated at: `{record.updated_at}`",
            "",
            "## Summary",
            "",
            _display(envelope["summary"]),
            "",
            "## Decisions",
            "",
            _display(envelope["decisions"]),
            "",
            "## Open issues",
            "",
            _display(envelope["open_issues"]),
        ]
        ordered = [name for name in SUGGESTED_ROLE_SECTIONS[record.role] if name in sections]
        ordered.extend(name for name in sections if name not in ordered)
        for name in ordered:
            lines.extend(["", f"## {name}", "", _display(sections[name])])
        lines.extend([
            "",
            "## Source refs",
            "",
            _display(envelope["source_refs"]),
            "",
            "## Revision history",
            "",
        ])
        revisions = sorted((root / "data" / "results").glob(f"{record.task_id}-round-*.json"))
        for revision in revisions:
            round_label = revision.stem.rsplit("-round-", 1)[-1]
            lines.append(f"- Round {round_label}: saved result projection")
        lines.append("")
        self._atomic_text(document_path, "\n".join(lines))
        return [
            str(document_path.relative_to(WORKFLOW_ROOT)),
            str(json_path.relative_to(WORKFLOW_ROOT)),
        ]

    def refresh(
        self,
        records: Iterable[TaskRecord],
        decisions: Iterable[dict[str, object]] = (),
        role_statuses: Iterable[dict[str, object]] = (),
    ) -> None:
        rows = list(records)
        decision_rows = list(decisions)
        status_by_role = {str(row["role"]): row for row in role_statuses}
        current = max(rows, key=lambda row: row.updated_at, default=None)
        if current is None:
            self._atomic_text(
                WORKFLOW_ROOT / "current_task.md",
                "# Current Project Workflow\n\nNo active workflow.\n",
            )
            return
        workflow_rows = [row for row in rows if row.workflow_id == current.workflow_id]
        latest_by_role: dict[str, TaskRecord] = {}
        for row in workflow_rows:
            if row.role not in latest_by_role or row.updated_at > latest_by_role[row.role].updated_at:
                latest_by_role[row.role] = row
        role_order = ("explorer", "implementer", "evaluator", "reviewer", "doc-curator")
        running = [
            latest_by_role[role]
            for role in role_order
            if role in latest_by_role and latest_by_role[role].status == "RUNNING"
        ]
        completed = [
            role
            for role in role_order
            if role in latest_by_role and latest_by_role[role].status == "RESULT_READY"
        ]
        parallel_validation = (
            all(role in latest_by_role for role in ("evaluator", "reviewer"))
            and any(
                latest_by_role[role].status == "RUNNING"
                for role in ("evaluator", "reviewer")
            )
        )
        if current.status == "BLOCKED":
            current_state = "BLOCKED"
            last_transfer = f"{current.role} → Supervisor"
            waiting_for = "User decision"
        elif current.status == "CANCELLED":
            current_state = "CANCELLED"
            last_transfer = f"Supervisor cancelled {current.role}"
            waiting_for = "Supervisor next task"
        elif current.status == "FAILED":
            current_state = "FAILED"
            last_transfer = f"{current.role} → Supervisor"
            waiting_for = "Supervisor recovery"
        elif running:
            dispatched = [row for row in running if row.execution_status == "DISPATCHED"]
            current_state = (
                "WAITING_WORKER_START"
                if dispatched
                else "WAITING_WORKER_RESULTS" if parallel_validation else "WAITING_WORKER_RESULT"
            )
            if current.status == "RESULT_READY":
                last_transfer = f"{current.role} → Supervisor"
            else:
                last_transfer = "Supervisor → " + ", ".join(row.role for row in running)
            waiting_for = ", ".join(
                f"{row.role} acknowledgement"
                if row.execution_status == "DISPATCHED"
                else f"{row.role} result"
                for row in running
            )
        elif current.status == "RESULT_READY":
            current_state = "RESULT_RECEIVED"
            last_transfer = f"{current.role} → Supervisor"
            waiting_for = "Supervisor acceptance"
        else:
            current_state = "BLOCKED"
            last_transfer = f"{current.role} → Supervisor"
            waiting_for = "Supervisor action"
        approval = "Pending Supervisor review" if current.status == "RESULT_READY" else "In progress"
        if current.status == "BLOCKED":
            approval = "User decision required"
        elif current.status == "CANCELLED":
            approval = "Cancelled"
        elif current.status == "FAILED":
            approval = "Recovery required"
        dashboard = [
            "# Current Project Workflow",
            "",
            f"- Current state: `{current_state}`",
            f"- Last transfer: {last_transfer}",
            f"- Waiting for: {waiting_for}",
            f"- Workflow ID: `{current.workflow_id}`",
            "- Active task IDs: " + (
                ", ".join(f"`{row.task_id}`" for row in running) if running else "None"
            ),
            f"- Completed roles: {', '.join(completed) or 'None'}",
            f"- Pending roles: {', '.join(row.role for row in running) or 'None'}",
            f"- Updated at: `{current.updated_at}`",
            "",
            "## Roles",
            "",
            "| Role | Process | Task | Execution status | Callback | Started at | Last activity | Waiting state |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for role in role_order:
            status = status_by_role.get(role, {})
            process = str(status.get("process_status", "UNKNOWN"))
            task_id = status.get("active_task_id") or status.get("latest_task_id") or "-"
            task_display = f"`{task_id}`" if task_id != "-" else "-"
            execution = str(status.get("derived_display_status", "IDLE"))
            callback = str(status.get("callback_status", "NONE"))
            error_code = str(status.get("error_code", ""))
            started_at = str(status.get("started_at") or "-")
            activity_at = str(status.get("last_activity_at") or "-")
            activity_kind = str(status.get("last_activity_kind") or "")
            activity = f"{activity_kind} {activity_at}".strip() if activity_kind else activity_at
            waiting = {
                "DISPATCHED": "Awaiting acknowledgement",
                "STARTED": "Awaiting result",
                "RUNNING": "Awaiting result",
                "STARTED_NO_RECENT_ACTIVITY": "Awaiting result",
                "RUNNING_NO_RECENT_ACTIVITY": "Awaiting result",
                "RESULT_READY": "Supervisor acceptance",
                "BLOCKED": "Supervisor or user action",
                "REWORK_REQUIRED": "Supervisor rework dispatch",
                "IDLE": "None",
            }.get(execution, "None")
            if error_code == "WAKEUP_PENDING":
                waiting = "Supervisor retry_dispatch"
            dashboard.append(
                f"| {role} | {process} | {task_display} | {execution} | {callback} | "
                f"{started_at} | {activity} | {waiting} |"
            )
        dashboard.extend([
            "",
            "## Pending actions",
            "",
            f"- {approval}",
            "",
            "## Documents",
            "",
            f"- `tasks/{current.workflow_id}/index.md`",
            f"- `tasks/{current.workflow_id}/handoff.md`",
            "",
        ])
        self._atomic_text(WORKFLOW_ROOT / "current_task.md", "\n".join(dashboard))
        workflow_decisions = [
            row for row in decision_rows
            if row.get("scope") == current.workflow_id
            or any(
                ref == f"task:{task.task_id}"
                for ref in row.get("affected_refs", [])
                for task in workflow_rows
            )
        ]
        self._render_index_and_handoff(
            current.workflow_id, workflow_rows, workflow_decisions, approval, current.updated_at
        )
        self._render_root_pointers(current.workflow_id)

    def _render_root_pointers(self, workflow_id: str) -> None:
        for root_name, target_name, title in (
            ("plan.md", "plan.md", "Current plan"),
            ("implementer_report.md", "implementer_report.md", "Current implementation report"),
            ("evaluation_report.md", "evaluation_report.md", "Current evaluation report"),
            ("handoff.md", "handoff.md", "Current handoff"),
        ):
            self._atomic_text(
                WORKFLOW_ROOT / root_name,
                f"# {title}\n\nSee:\n`tasks/{workflow_id}/{target_name}`\n\n"
                "Runtime source of truth: TaskStore / Result Envelope.\n",
            )

    def _render_index_and_handoff(
        self,
        workflow_id: str,
        records: list[TaskRecord],
        decisions: list[dict[str, object]],
        approval: str,
        updated_at: str,
    ) -> None:
        root = self.ensure_workflow(workflow_id)
        ordered = sorted(records, key=lambda row: (row.created_at, row.task_id))
        index = [
            "# Workflow Index", "", f"- Workflow ID: `{workflow_id}`",
            f"- Stage: `{ordered[-1].role}`", f"- Approval state: {approval}",
            f"- Updated at: `{updated_at}`", "", "## Tasks", "",
        ]
        preflight_path = root / "data" / "preflight.json"
        if preflight_path.is_file():
            try:
                preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                preflight = {}
            index[6:6] = [
                f"- Task profile: `{preflight.get('profile', preflight.get('task_profile', 'LEGACY'))}`",
                f"- Profile reason: {preflight.get('profile_reason', 'Legacy workflow; authority metadata unavailable.')}",
                "- Profile triggers: " + (", ".join(preflight.get("profile_triggers", [])) or "None"),
                f"- Effective PLAN revision: `{preflight.get('effective_plan_revision') or 'LEGACY'}`",
                f"- Effective contract revision: `{preflight.get('effective_contract_revision') or 'None'}`",
                "- Active decision refs: " + (", ".join(preflight.get("effective_decision_refs", [])) or "None"),
                f"- Semantic repair count: `{preflight.get('semantic_repair_count', 0)}`",
                f"- Proportionality verdict: `{preflight.get('proportionality_verdict', 'UNCERTAIN')}`",
            ]
        for row in ordered:
            index.append(f"- `{row.task_id}` — {row.role} — {row.status} — `{ROLE_DOCUMENTS[row.role]}`")
        index.extend(["", "## Decisions", ""])
        index.extend(
            [f"- `decision:{row['decision_id']}`" for row in decisions]
            or ["- No workflow decision refs recorded."]
        )
        index.append("")
        self._atomic_text(root / "index.md", "\n".join(index))
        completed = [row.role for row in ordered if row.status == "RESULT_READY"]
        handoff = [
            "# Workflow Handoff", "", f"- Completed roles: {', '.join(completed) or 'None'}",
            f"- Next role: Supervisor decides after approval.",
            "- Task refs:",
        ]
        handoff.extend(f"  - `task:{row.task_id}`" for row in ordered)
        handoff.extend([
            "- Suggested context refs: use only the task sections needed by the next role.",
            f"- Pending approval: {approval}", "",
        ])
        self._atomic_text(root / "handoff.md", "\n".join(handoff))

    def append_decision(self, decision: dict[str, object]) -> str:
        validate_decision_id(str(decision["decision_id"]))
        path = WORKFLOW_ROOT / "decision_log.md"
        block = (
            f"\n\n## {decision['decision_id']}：{decision['title']}\n\n"
            "Status: Approved\n"
            f"Date: {decision['date']}\n"
            f"Scope: {decision['scope']}\n"
            f"Affected: {', '.join(decision['affected_refs'])}\n\n"
            "### Decision\n\n"
            f"{decision['decision']}\n\n"
            "### Reason\n\n"
            f"{decision['reason']}\n"
        )
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, block.encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise DocumentError("decision log append failed") from exc
        return str(path.relative_to(WORKFLOW_ROOT))
