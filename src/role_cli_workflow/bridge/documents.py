"""Best-effort Markdown projections; TaskStore remains authoritative."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Iterable

from .config import WORKFLOW_ROOT
from .contracts import SUGGESTED_ROLE_SECTIONS, canonical_json
from .models import TaskRecord
from .security import validate_workflow_id

ROLE_DOCUMENTS = {
    "explorer": "plan.md",
    "implementer": "implementer_report.md",
    "evaluator": "evaluation_report.md",
    "reviewer": "review_report.md",
    "doc-curator": "documentation_report.md",
}


class DocumentError(RuntimeError):
    """A projection failed; durable task validity is unchanged."""


def _display(value: object) -> str:
    return value if isinstance(value, str) else "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


class WorkflowDocuments:
    def _atomic_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise DocumentError("document projection failed") from exc

    def workflow_dir(self, workflow_id: str) -> Path:
        root = WORKFLOW_ROOT / "tasks" / validate_workflow_id(workflow_id)
        if root.parent != WORKFLOW_ROOT / "tasks":
            raise DocumentError("workflow projection path is invalid")
        return root

    def ensure_workflow(self, workflow_id: str) -> Path:
        root = self.workflow_dir(workflow_id)
        (root / "data" / "task-contracts").mkdir(mode=0o700, parents=True, exist_ok=True)
        (root / "data" / "results").mkdir(mode=0o700, parents=True, exist_ok=True)
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
        sections = envelope.get("sections", {})
        assert isinstance(sections, dict)
        lines = [
            f"# {record.role.replace('-', ' ').title()} Result", "",
            f"- Workflow: `{record.workflow_id}`", f"- Task: `{record.task_id}`",
            f"- Round: `{record.round}`", f"- Candidate: `{record.produced_candidate_sha or 'none'}`",
            "", "## Summary", "", _display(envelope["summary"]),
        ]
        ordered = [name for name in SUGGESTED_ROLE_SECTIONS[record.role] if name in sections]
        ordered.extend(name for name in sections if name not in ordered)
        for name in ordered:
            lines.extend(["", f"## {name}", "", _display(sections[name])])
        lines.append("")
        document = root / ROLE_DOCUMENTS[record.role]
        self._atomic_text(document, "\n".join(lines))
        return [str(document.relative_to(WORKFLOW_ROOT)), str(json_path.relative_to(WORKFLOW_ROOT))]

    def refresh(
        self,
        records: Iterable[TaskRecord],
        decisions: Iterable[dict[str, object]] = (),
        role_statuses: Iterable[dict[str, object]] = (),
    ) -> None:
        rows = list(records)
        if not rows:
            self._atomic_text(WORKFLOW_ROOT / "current_task.md", "# Current Project Workflow\n\nNo active workflow.\n")
            return
        current = max(rows, key=lambda row: row.updated_at)
        workflow_rows = [row for row in rows if row.workflow_id == current.workflow_id]
        active = [row.task_id for row in workflow_rows if row.status in {"DISPATCHED", "RUNNING"}]
        lines = [
            "# Project Workflow Status", "", "TaskStore is authoritative; this file is a projection.", "",
            f"- Workflow ID: `{current.workflow_id}`", f"- Active tasks: {', '.join(active) if active else 'None'}", "",
            "| Role | Process | Task | Status |", "| --- | --- | --- | --- |",
        ]
        statuses = {str(row["role"]): row for row in role_statuses}
        for role in ("explorer", "implementer", "evaluator", "reviewer", "doc-curator"):
            status = statuses.get(role, {})
            lines.append(f"| {role} | {status.get('process', 'UNKNOWN')} | {status.get('task_id', '-') or '-'} | {status.get('status', 'IDLE')} |")
        lines.append("")
        self._atomic_text(WORKFLOW_ROOT / "current_task.md", "\n".join(lines))
