"""Durable minimal TaskStore; panes and callbacks are retryable transport only."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..candidate import CandidateError, prepare_validation_worktree, prepare_writer_base, validate_candidate
from ..config import load_project
from .config import (
    DECISIONS_DIR,
    LOG_DIR,
    METADATA_DIR,
    PROJECT_ROOT,
    TASKS_DIR,
    WORKER_ROLES,
    ensure_runtime_permissions,
    load_roles,
)
from .contracts import (
    build_task_contract,
    canonical_json,
    result_view,
    validate_result_envelope,
)
from .documents import DocumentError, WorkflowDocuments
from .models import TaskRecord
from .security import (
    MAX_BLOCKED_REASON_CHARS,
    ValidationError,
    sha256_text,
    validate_decision_id,
    validate_ref_id,
    validate_task_id,
    validate_text,
    validate_workflow_id,
)
from .tmux_client import TmuxClient, TmuxError

TASK_TRIGGER = "ROLE_CLI_WORKFLOW_TASK_AVAILABLE: call get_current_task"
RESULT_TRIGGER = "ROLE_CLI_WORKFLOW_RESULT_READY: call get_task_result"
ACTIVE_STATUSES = {"DISPATCHED", "RUNNING"}
WRITER_ROLES = {"implementer", "doc-curator"}


class StateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TaskStore:
    def __init__(self, caller_role: str) -> None:
        ensure_runtime_permissions()
        self.caller_role = caller_role
        self.roles = load_roles()
        self.tmux = TmuxClient()
        self.documents = WorkflowDocuments()
        self.project = load_project(PROJECT_ROOT)

    @contextmanager
    def _lock(self, name: str = "taskstore") -> Iterator[None]:
        path = METADATA_DIR / f".{name}.lock"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _require_supervisor(self) -> None:
        if self.caller_role != "supervisor":
            raise StateError("only Supervisor may perform this operation")

    def _require_worker(self) -> str:
        if self.caller_role not in WORKER_ROLES:
            raise StateError("only a Worker may perform this operation")
        return self.caller_role

    def _task_dir(self, task_id: str, create: bool = False) -> Path:
        path = TASKS_DIR / validate_task_id(task_id)
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def _metadata_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "task.json"

    def _contract_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "contract.json"

    def _result_path(self, task_id: str, round_number: int) -> Path:
        return self._task_dir(task_id) / f"round-{round_number}.result.json"

    def _rework_path(self, task_id: str, round_number: int) -> Path:
        return self._task_dir(task_id) / f"round-{round_number}.rework.json"

    def _decision_path(self, decision_id: str) -> Path:
        return DECISIONS_DIR / f"{validate_decision_id(decision_id)}.json"

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        encoded = (canonical_json(value) + "\n").encode("utf-8")
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_json(self, path: Path, label: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"{label} is unavailable") from exc
        if not isinstance(value, dict):
            raise StateError(f"{label} is invalid")
        return value

    def _write_record(self, record: TaskRecord) -> None:
        self._write_json(self._metadata_path(record.task_id), record.to_dict())

    def _read_record(self, task_id: str) -> TaskRecord:
        try:
            return TaskRecord.from_dict(self._read_json(self._metadata_path(task_id), "task record"))
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("active TaskStore record is invalid") from exc

    def _read_contract(self, task_id: str) -> dict[str, Any]:
        return self._read_json(self._contract_path(task_id), "task contract")

    def _read_result(self, task_id: str, round_number: int) -> dict[str, Any]:
        return self._read_json(self._result_path(task_id, round_number), "task result")

    def _records(self) -> Iterator[TaskRecord]:
        if not TASKS_DIR.is_dir():
            return
        for path in sorted(TASKS_DIR.glob("*/task.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    yield TaskRecord.from_dict(value)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue

    def _decisions(self) -> Iterator[dict[str, Any]]:
        if not DECISIONS_DIR.is_dir():
            return
        for path in sorted(DECISIONS_DIR.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("schema_version") == 2:
                    yield value
            except (OSError, json.JSONDecodeError):
                continue

    def _active_for_role(self, role: str) -> TaskRecord | None:
        return max(
            (record for record in self._records() if record.role == role and record.status in ACTIVE_STATUSES),
            key=lambda record: record.updated_at,
            default=None,
        )

    def _active_writer(self) -> TaskRecord | None:
        return max(
            (record for record in self._records() if record.role in WRITER_ROLES and record.status in ACTIVE_STATUSES),
            key=lambda record: record.updated_at,
            default=None,
        )

    @staticmethod
    def _public(record: TaskRecord, already_submitted: bool = False) -> dict[str, object]:
        return {
            "schema_version": 2,
            "workflow_id": record.workflow_id,
            "task_id": record.task_id,
            "role": record.role,
            "round": record.round,
            "nonce": record.nonce,
            "status": record.status,
            "input_candidate_sha": record.input_candidate_sha,
            "produced_candidate_sha": record.produced_candidate_sha,
            "dispatch_notice_status": record.dispatch_notice_status,
            "result_notice_status": record.result_notice_status,
            "warning": record.warning,
            "already_submitted": already_submitted,
        }

    def _refresh_documents(self) -> str:
        try:
            self.documents.refresh(self._records(), self._decisions(), self._role_rows())
            return ""
        except DocumentError:
            return "PROJECTION_WARNING"

    def _role_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for role in WORKER_ROLES:
            active = self._active_for_role(role)
            process, _ = self.tmux.probe(self.roles[role])
            rows.append({
                "role": role,
                "process": process,
                "task_id": active.task_id if active else "",
                "status": active.status if active else "IDLE",
            })
        return rows

    def list_roles(self) -> dict[str, object]:
        self._require_supervisor()
        return {"roles": self._role_rows()}

    def _notify(self, role: str, text: str) -> None:
        pane = self.tmux.fixed_pane(self.roles[role])
        self.tmux.paste(pane.pane_id, text, f"crw-{secrets.token_hex(8)}")

    def assign_task(
        self,
        workflow_id: str,
        role: str,
        task_id: str,
        objective: str,
        deliverables: list[str],
        acceptance_criteria: list[str],
        constraints: list[str],
        authorized_refs: list[str],
        input_candidate_sha: str = "",
    ) -> dict[str, object]:
        self._require_supervisor()
        contract, warnings = build_task_contract(
            workflow_id, role, task_id, objective, deliverables, acceptance_criteria,
            constraints, authorized_refs, input_candidate_sha,
        )
        with self._lock():
            if self._metadata_path(task_id).exists() or self._contract_path(task_id).exists():
                raise StateError("task_id already exists")
            if self._active_for_role(role):
                raise StateError("role already has an active task")
            if role in WRITER_ROLES and self._active_writer():
                raise StateError("another writer task is active")
            if role in WRITER_ROLES:
                try:
                    prepare_writer_base(self.project, role=role, base_sha=input_candidate_sha)
                except CandidateError as exc:
                    raise StateError(str(exc)) from exc
            if role in {"evaluator", "reviewer"}:
                try:
                    prepare_validation_worktree(self.project, role=role, candidate_sha=input_candidate_sha)
                except CandidateError as exc:
                    raise StateError(str(exc)) from exc
            now = _now()
            record = TaskRecord(
                schema_version=2,
                workflow_id=str(contract["workflow_id"]),
                task_id=str(contract["task_id"]),
                role=str(contract["role"]),
                round=0,
                nonce=secrets.token_hex(16),
                status="DISPATCHED",
                task_contract_hash=sha256_text(canonical_json(contract)),
                input_candidate_sha=input_candidate_sha.lower(),
                produced_candidate_sha="",
                result_hash="",
                dispatch_notice_status="PENDING",
                result_notice_status="NONE",
                created_at=now,
                updated_at=now,
            )
            self._task_dir(task_id, create=True)
            self._write_json(self._contract_path(task_id), contract)
            self._write_record(record)
        try:
            self._notify(role, TASK_TRIGGER)
            record.dispatch_notice_status = "DELIVERED"
            record.warning = ""
        except TmuxError:
            record.warning = "WAKEUP_PENDING"
            warnings.append("WAKEUP_PENDING")
        record.updated_at = _now()
        self._write_record(record)
        projection = self._refresh_documents()
        if projection:
            warnings.append(projection)
        return {**self._public(record), "warnings": warnings}

    def retry_dispatch(self, task_id: str) -> dict[str, object]:
        self._require_supervisor()
        record = self._read_record(task_id)
        if record.status not in ACTIVE_STATUSES:
            raise StateError("task is not dispatchable")
        try:
            self._notify(record.role, TASK_TRIGGER)
        except TmuxError as exc:
            record.dispatch_notice_status = "PENDING"
            record.warning = "WAKEUP_PENDING"
            record.updated_at = _now()
            self._write_record(record)
            raise StateError("worker wakeup remains pending") from exc
        record.dispatch_notice_status = "DELIVERED"
        record.warning = ""
        record.updated_at = _now()
        self._write_record(record)
        return self._public(record)

    def cancel_task(self, task_id: str, reason: str) -> dict[str, object]:
        self._require_supervisor()
        validate_text(reason, field="reason", maximum=MAX_BLOCKED_REASON_CHARS)
        with self._lock():
            record = self._read_record(task_id)
            if record.status in {"ACCEPTED", "CANCELLED"}:
                raise StateError("task is already terminal")
            record.status = "CANCELLED"
            record.blocked_reason = reason
            record.completed_at = record.updated_at = _now()
            self._write_record(record)
        self._refresh_documents()
        return self._public(record)

    def get_current_task(self) -> dict[str, object]:
        role = self._require_worker()
        with self._lock():
            record = self._active_for_role(role)
            if record is None:
                return {"status": "IDLE"}
            contract = self._read_contract(record.task_id)
            if sha256_text(canonical_json(contract)) != record.task_contract_hash:
                raise StateError("task contract hash does not match the active record")
            if record.status == "DISPATCHED":
                record.status = "RUNNING"
                record.started_at = record.started_at or _now()
                record.updated_at = _now()
                self._write_record(record)
        return {**self._public(record), "contract": contract}

    def _authorized_refs(self, record: TaskRecord) -> set[str]:
        contract = self._read_contract(record.task_id)
        value = contract.get("authorized_refs", [])
        return set(value) if isinstance(value, list) else set()

    def get_context(self, ref_id: str, view: str = "summary") -> dict[str, object]:
        role = self._require_worker()
        record = self._active_for_role(role)
        if record is None:
            raise StateError("role has no active task")
        ref = validate_ref_id(ref_id)
        if ref not in self._authorized_refs(record):
            raise StateError("context ref is not authorized by the active task")
        kind, identifier = ref.split(":", 1)
        if kind == "task":
            source = self._read_record(identifier)
            if source.status not in {"RESULT_READY", "ACCEPTED"}:
                raise StateError("referenced task has no usable result")
            content = result_view(self._read_result(identifier, source.round), view)
        else:
            decision = self._read_json(self._decision_path(identifier), "decision")
            content = decision if view == "full" else {"title": decision.get("title"), "decision": decision.get("decision")}
        return {"ref_id": ref, "view": view, "content": content}

    def _validate_submission(self, workflow_id: str, task_id: str, round_number: int, nonce: str) -> TaskRecord:
        role = self._require_worker()
        record = self._read_record(task_id)
        if record.workflow_id != validate_workflow_id(workflow_id):
            raise StateError("wrong workflow")
        if record.task_id != validate_task_id(task_id):
            raise StateError("wrong task")
        if record.role != role:
            raise StateError("wrong role")
        if record.round != round_number:
            raise StateError("wrong round")
        if record.nonce != nonce:
            raise StateError("wrong nonce")
        return record

    def _deliver_callback(self, record: TaskRecord) -> None:
        self._notify(
            "supervisor",
            f"{RESULT_TRIGGER}\nWorkflow: {record.workflow_id}\nTask: {record.task_id}\nRole: {record.role}\nStatus: {record.status}",
        )

    def submit_result(
        self,
        workflow_id: str,
        task_id: str,
        round: int,
        nonce: str,
        summary: str,
        decisions: list[Any],
        open_issues: list[Any],
        evidence: list[Any],
        sections: dict[str, Any],
        source_refs: list[str],
        produced_candidate_sha: str = "",
    ) -> dict[str, object]:
        with self._lock():
            record = self._validate_submission(workflow_id, task_id, round, nonce)
            if record.status == "RESULT_READY":
                return self._public(record, already_submitted=True)
            if record.status not in ACTIVE_STATUSES:
                raise StateError("task is not accepting results")
            envelope = validate_result_envelope(
                workflow_id, record.role, task_id, round, nonce, summary, decisions,
                open_issues, evidence, sections, source_refs, self._authorized_refs(record),
                record.input_candidate_sha, produced_candidate_sha,
            )
            if record.role in WRITER_ROLES:
                contract = self._read_contract(task_id)
                base_sha = str(contract.get("base_sha", "") or contract.get("input_candidate_sha", ""))
                if not base_sha:
                    raise StateError("Implementer Task Contract requires an input base SHA")
                try:
                    validate_candidate(
                        self.project,
                        repo=self.project.repo(record.role),
                        expected_role=record.role,
                        base_sha=base_sha,
                        candidate_sha=produced_candidate_sha,
                    )
                except CandidateError as exc:
                    raise StateError(str(exc)) from exc
            self._write_json(self._result_path(task_id, round), envelope)
            record.status = "RESULT_READY"
            record.produced_candidate_sha = produced_candidate_sha.lower()
            record.result_hash = sha256_text(canonical_json(envelope))
            record.result_notice_status = "PENDING"
            record.completed_at = record.updated_at = _now()
            self._write_record(record)
        try:
            self._deliver_callback(record)
            record.result_notice_status = "DELIVERED"
            record.warning = ""
        except TmuxError:
            record.warning = "CALLBACK_PENDING"
        record.updated_at = _now()
        self._write_record(record)
        try:
            self.documents.project_result(record, envelope)
        except DocumentError:
            record.warning = "PROJECTION_WARNING"
            self._write_record(record)
        self._refresh_documents()
        return self._public(record)

    def report_blocked(self, workflow_id: str, task_id: str, round: int, nonce: str, reason: str) -> dict[str, object]:
        validate_text(reason, field="reason", maximum=MAX_BLOCKED_REASON_CHARS)
        with self._lock():
            record = self._validate_submission(workflow_id, task_id, round, nonce)
            if record.status == "BLOCKED":
                return self._public(record, already_submitted=True)
            if record.status not in ACTIVE_STATUSES:
                raise StateError("task is not accepting a blocked report")
            record.status = "BLOCKED"
            record.blocked_reason = reason
            record.result_notice_status = "PENDING"
            record.completed_at = record.updated_at = _now()
            self._write_record(record)
        try:
            self._deliver_callback(record)
            record.result_notice_status = "DELIVERED"
        except TmuxError:
            record.warning = "CALLBACK_PENDING"
        self._write_record(record)
        self._refresh_documents()
        return self._public(record)

    def retry_callback(self, task_id: str) -> dict[str, object]:
        self._require_supervisor()
        record = self._read_record(task_id)
        if record.status not in {"RESULT_READY", "BLOCKED"}:
            raise StateError("task has no retryable result callback")
        try:
            self._deliver_callback(record)
        except TmuxError as exc:
            record.result_notice_status = "PENDING"
            record.warning = "CALLBACK_PENDING"
            self._write_record(record)
            raise StateError("result callback remains pending") from exc
        record.result_notice_status = "DELIVERED"
        record.warning = ""
        record.updated_at = _now()
        self._write_record(record)
        return self._public(record)

    def get_task_result(self, task_id: str, view: str = "full") -> dict[str, object]:
        self._require_supervisor()
        record = self._read_record(task_id)
        if record.status == "BLOCKED":
            return {**self._public(record), "blocked_reason": record.blocked_reason}
        if record.status not in {"RESULT_READY", "ACCEPTED"}:
            raise StateError("task result is not ready")
        envelope = self._read_result(task_id, record.round)
        if sha256_text(canonical_json(envelope)) != record.result_hash:
            raise StateError("task result hash does not match")
        if record.role in {"evaluator", "reviewer"} and envelope.get("produced_candidate_sha") != record.input_candidate_sha:
            raise StateError("task evidence is stale for the current candidate SHA")
        return {**self._public(record), "result": result_view(envelope, view)}

    def accept_task(self, task_id: str, candidate_sha: str = "") -> dict[str, object]:
        self._require_supervisor()
        with self._lock():
            record = self._read_record(task_id)
            if record.status != "RESULT_READY":
                raise StateError("only a ready result can be accepted")
            expected = record.input_candidate_sha if record.role in {"evaluator", "reviewer"} else record.produced_candidate_sha
            if expected and candidate_sha.lower() != expected:
                raise StateError("acceptance candidate SHA is stale or incorrect")
            record.status = "ACCEPTED"
            record.updated_at = _now()
            self._write_record(record)
        self._refresh_documents()
        return self._public(record)

    def send_rework(self, task_id: str, required_changes: list[str], clarified_information: str = "") -> dict[str, object]:
        self._require_supervisor()
        if not required_changes or any(not isinstance(item, str) or not item.strip() for item in required_changes):
            raise ValidationError("required_changes must be non-empty strings")
        with self._lock():
            record = self._read_record(task_id)
            if record.status not in {"RESULT_READY", "BLOCKED"}:
                raise StateError("task is not eligible for rework")
            record.round += 1
            record.nonce = secrets.token_hex(16)
            record.status = "DISPATCHED"
            record.produced_candidate_sha = ""
            record.result_hash = ""
            record.dispatch_notice_status = "PENDING"
            record.result_notice_status = "NONE"
            record.blocked_reason = ""
            record.completed_at = ""
            record.updated_at = _now()
            self._write_json(self._rework_path(task_id, record.round), {
                "task_id": task_id,
                "round": record.round,
                "required_changes": required_changes,
                "clarified_information": clarified_information,
            })
            self._write_record(record)
        try:
            self._notify(record.role, TASK_TRIGGER)
            record.dispatch_notice_status = "DELIVERED"
            record.warning = ""
        except TmuxError:
            record.warning = "WAKEUP_PENDING"
        self._write_record(record)
        self._refresh_documents()
        return self._public(record)

    def record_decision(
        self,
        decision_id: str,
        title: str,
        decision: str,
        reason: str,
        scope: str,
        affected_refs: list[str],
    ) -> dict[str, object]:
        self._require_supervisor()
        path = self._decision_path(decision_id)
        if path.exists():
            raise StateError("decision_id already exists")
        payload = {
            "schema_version": 2,
            "decision_id": decision_id,
            "title": validate_text(title, field="title", maximum=MAX_BLOCKED_REASON_CHARS),
            "decision": validate_text(decision, field="decision", maximum=MAX_BLOCKED_REASON_CHARS),
            "reason": validate_text(reason, field="reason", maximum=MAX_BLOCKED_REASON_CHARS),
            "scope": validate_text(scope, field="scope", maximum=MAX_BLOCKED_REASON_CHARS),
            "affected_refs": [validate_ref_id(ref) for ref in affected_refs],
            "recorded_at": _now(),
        }
        self._write_json(path, payload)
        self._refresh_documents()
        return payload
