"""Atomic TaskStore state with controlled context and document projections."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import RUNTIME_ROOT, WORKER_ROLES, ensure_runtime_permissions, load_roles
from .contracts import (
    canonical_json,
    json_size,
    ref_type,
    result_section,
    validate_context_refs,
    validate_result_envelope,
    build_task_contract,
)
from .documents import ROLE_DOCUMENTS, WORKFLOW_ROOT, DocumentError, WorkflowDocuments
from .models import TaskRecord
from .security import (
    LARGE_TASK_WARNING_BYTES,
    MAX_BLOCKED_REASON_CHARS,
    MAX_PAYLOAD_BYTES,
    ValidationError,
    sha256_text,
    validate_caller_role,
    validate_decision_id,
    validate_nonce,
    validate_ref_id,
    validate_task_id,
    validate_text,
    validate_worker_role,
    validate_workflow_id,
)
from .tmux_client import TmuxClient, TmuxError
from ..refinement import (
    EVENT_KINDS,
    SEMANTIC_REPAIR_EVENTS,
    event_metric,
    resolve_execution_profile,
)

METADATA_DIR = RUNTIME_ROOT / "metadata"
ACTIVE_TASKS_PATH = METADATA_DIR / "active-tasks.json"
TASKS_DIR = RUNTIME_ROOT / "tasks"
DECISIONS_DIR = RUNTIME_ROOT / "decisions"
LOG_DIR = RUNTIME_ROOT / "logs"

TASK_TRIGGER = (
    "[ROLE_CLI_WORKFLOW_TASK_AVAILABLE]\n"
    "Call get_current_task.\n"
    "Read only the context you need.\n"
    "Complete the task and submit the result.\n"
    "[/ROLE_CLI_WORKFLOW_TASK_AVAILABLE]"
)
RESULT_TRIGGER = (
    "[ROLE_CLI_WORKFLOW_RESULT_AVAILABLE]\n"
    "Workflow: {workflow_id}\n"
    "Task: {task_id}\n"
    "Role: {role}\n"
    "Status: {status}\n"
    "Call list_roles and inspect this task's callback status.\n"
    "Call get_task_result for task {task_id}.\n"
    "Analyze the result or blocker and report it to the user.\n"
    "[/ROLE_CLI_WORKFLOW_RESULT_AVAILABLE]"
)
WORKER_STARTED_TRIGGER = (
    "[ROLE_CLI_WORKFLOW_WORKER_STARTED]\n"
    "Call list_roles and show compact workflow status.\n"
    "[/ROLE_CLI_WORKFLOW_WORKER_STARTED]"
)
RECENT_ACTIVITY_WINDOW = timedelta(minutes=5)
LOCK_TIMEOUT_SECONDS = 5.0


class StateError(RuntimeError):
    """Sanitized bridge state failure."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _activity_is_stale(value: str) -> bool:
    if not value:
        return False
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - observed > RECENT_ACTIVITY_WINDOW


def _activity_is_recent(value: str) -> bool:
    return bool(value) and not _activity_is_stale(value)


class TaskStore:
    def __init__(
        self,
        caller_role: str,
        tmux: TmuxClient | None = None,
        documents: WorkflowDocuments | None = None,
    ) -> None:
        self.caller_role = validate_caller_role(caller_role)
        ensure_runtime_permissions()
        DECISIONS_DIR.mkdir(mode=0o700, exist_ok=True)
        os.chmod(DECISIONS_DIR, 0o700)
        self.roles = load_roles()
        self.tmux = tmux or TmuxClient()
        self.documents = documents or WorkflowDocuments()

    @contextmanager
    def _lock(self, name: str) -> Iterator[None]:
        if name not in WORKER_ROLES and name not in {"all-tasks", "decisions"}:
            raise StateError("task lock is invalid")
        descriptor = os.open(METADATA_DIR / f"{name}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise StateError("task state is busy; retry") from None
                    time.sleep(0.05)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _require_supervisor(self) -> None:
        if self.caller_role != "supervisor":
            raise StateError("tool is not allowed for this role")

    def _require_worker(self) -> str:
        if self.caller_role not in WORKER_ROLES:
            raise StateError("tool is not allowed for this role")
        return self.caller_role

    def _task_dir(self, task_id: str, create: bool = False) -> Path:
        path = TASKS_DIR / validate_task_id(task_id)
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)
        return path

    def _metadata_path(self, task_id: str) -> Path:
        return METADATA_DIR / f"{validate_task_id(task_id)}.json"

    def _contract_path(self, task_id: str) -> Path:
        return self._task_dir(task_id, True) / "contract.json"

    def _result_path(self, task_id: str, round_number: int) -> Path:
        if not isinstance(round_number, int) or not 0 <= round_number <= 100:
            raise ValidationError("round is invalid")
        return self._task_dir(task_id, True) / f"round-{round_number}.result.json"

    def _rework_path(self, task_id: str, round_number: int) -> Path:
        if not isinstance(round_number, int) or not 1 <= round_number <= 100:
            raise ValidationError("round is invalid")
        return self._task_dir(task_id, True) / f"round-{round_number}.rework.json"

    def _blocked_path(self, task_id: str, round_number: int) -> Path:
        if not isinstance(round_number, int) or not 0 <= round_number <= 100:
            raise ValidationError("round is invalid")
        return self._task_dir(task_id, True) / f"round-{round_number}.blocked.json"

    def _refinement_path(self, workflow_id: str, name: str) -> Path:
        workflow = validate_workflow_id(workflow_id)
        if name not in {
            "preflight.json", "retrospective-metrics.json",
            "refinement-events.json", "semantic-repair-assessment.json",
        }:
            raise ValidationError("refinement metadata name is invalid")
        path = WORKFLOW_ROOT / "tasks" / workflow / "data" / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def _semantic_repair_checkpoint(
        self, workflow_id: str, event_kind: str, invariant_type: str
    ) -> str:
        if event_kind not in SEMANTIC_REPAIR_EVENTS:
            return ""
        invariant = invariant_type.strip()
        preflight_path = self._refinement_path(workflow_id, "preflight.json")
        if not preflight_path.exists():
            return invariant
        preflight = self._read_json(preflight_path, "preflight metadata")
        if resolve_execution_profile(preflight) != "FULL":
            return invariant
        if not invariant:
            raise ValidationError("FULL semantic rework requires invariant_type")

        metrics_path = self._refinement_path(workflow_id, "retrospective-metrics.json")
        metrics = self._read_json(metrics_path, "retrospective metrics") if metrics_path.exists() else {}
        semantic_count = int(metrics.get("semantic_revision_count", 0))
        assessment_path = self._refinement_path(workflow_id, "semantic-repair-assessment.json")
        if semantic_count == 0:
            self._write_json(assessment_path, {
                "workflow_id": workflow_id, "status": "REPAIR_ALLOWED",
                "semantic_repair_count": 0, "invariant_type": invariant,
                "new_invariant_type": True, "suggested_classification": "required",
                "invariant_types": [invariant],
                "source": "BRIDGE_INITIAL_SEMANTIC_REPAIR", "updated_at": _now(),
            })
            return invariant
        if not assessment_path.exists():
            raise StateError("PROPORTIONALITY_REASSESSMENT_REQUIRED")
        assessment = self._read_json(assessment_path, "semantic repair assessment")
        if assessment.get("semantic_repair_count") != semantic_count or assessment.get("invariant_type") != invariant:
            raise StateError("PROPORTIONALITY_REASSESSMENT_REQUIRED")
        status = assessment.get("status")
        if status == "PROPORTIONALITY_REASSESSMENT_REQUIRED":
            raise StateError("PROPORTIONALITY_REASSESSMENT_REQUIRED")
        if status == "NO_REWORK_REQUIRED":
            raise StateError("semantic hardening is advisory or deferred")
        if status != "REPAIR_ALLOWED":
            raise StateError("semantic repair assessment is invalid")
        return invariant

    def _record_refinement_event(self, record: TaskRecord, event_kind: str, invariant_type: str) -> None:
        events_path = self._refinement_path(record.workflow_id, "refinement-events.json")
        payload = self._read_json(events_path, "refinement events") if events_path.exists() else {"workflow_id": record.workflow_id, "events": []}
        events = payload.get("events")
        if not isinstance(events, list):
            raise StateError("refinement events are invalid")
        event: dict[str, object] = {
            "workflow_id": record.workflow_id, "task_id": record.task_id,
            "event_kind": event_kind, "round": record.round,
            "nonce": record.nonce, "at": _now(),
        }
        if invariant_type:
            event["invariant_type"] = invariant_type
        events.append(event)
        self._write_json(events_path, payload)

        metrics_path = self._refinement_path(record.workflow_id, "retrospective-metrics.json")
        metrics = self._read_json(metrics_path, "retrospective metrics") if metrics_path.exists() else {
            "workflow_id": record.workflow_id,
            **{name: 0 for name in (
                "semantic_revision_count", "mechanical_repair_count",
                "permission_recovery_count", "context_recovery_count",
                "verification_rerun_count", "stale_sha_verification_count",
                "git_approval_interruption_count", "approved_git_transaction_count",
                "invalidated_git_transaction_count",
            )},
        }
        metric = event_metric(event_kind)
        if metric:
            metrics[metric] = int(metrics.get(metric, 0)) + 1
        metrics["last_updated_at"] = _now()
        self._write_json(metrics_path, metrics)
        preflight_path = self._refinement_path(record.workflow_id, "preflight.json")
        if preflight_path.exists() and metric == "semantic_revision_count":
            preflight = self._read_json(preflight_path, "preflight metadata")
            preflight[metric] = metrics[metric]
            preflight["last_updated_at"] = _now()
            self._write_json(preflight_path, preflight)

    def _decision_path(self, decision_id: str) -> Path:
        return DECISIONS_DIR / f"{validate_decision_id(decision_id)}.json"

    def _atomic_bytes(self, path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _write_json(self, path: Path, value: object) -> None:
        data = canonical_json(value).encode("utf-8")
        if len(data) > MAX_PAYLOAD_BYTES:
            raise ValidationError("payload exceeds the technical limit")
        self._atomic_bytes(path, data)

    def _read_json(self, path: Path, label: str) -> dict[str, Any]:
        try:
            if path.stat().st_size > MAX_PAYLOAD_BYTES:
                raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise StateError(f"{label} is invalid") from exc

    def _write_record(self, record: TaskRecord) -> None:
        self._write_json(self._metadata_path(record.task_id), record.to_dict())

    def _read_record(self, task_id: str) -> TaskRecord:
        try:
            return TaskRecord.from_dict(self._read_json(self._metadata_path(task_id), "task metadata"))
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("task metadata is invalid") from exc

    def _read_contract(self, task_id: str) -> dict[str, Any]:
        return self._read_json(self._contract_path(task_id), "task contract")

    def _read_result(self, task_id: str, round_number: int) -> dict[str, Any]:
        return self._read_json(self._result_path(task_id, round_number), "task result")

    def _records(self) -> Iterator[TaskRecord]:
        for path in sorted(METADATA_DIR.glob("*.json")):
            if path == ACTIVE_TASKS_PATH:
                continue
            try:
                yield self._read_record(path.stem)
            except (StateError, ValidationError):
                continue

    def _legacy_authority_warning_was_reported(self, workflow_id: str) -> bool:
        for path in sorted(TASKS_DIR.glob("*/contract.json")):
            try:
                contract = self._read_json(path, "task contract")
            except StateError:
                continue
            authority = contract.get("effective_authority")
            if (
                contract.get("workflow_id") == workflow_id
                and isinstance(authority, dict)
                and authority.get("status") == "LEGACY_AUTHORITY_WARNING"
            ):
                return True
        return False

    def _decisions(self) -> Iterator[dict[str, Any]]:
        for path in sorted(DECISIONS_DIR.glob("*.json")):
            try:
                yield self._read_json(path, "decision")
            except StateError:
                continue

    def _log(self, record: TaskRecord, event: str) -> None:
        entry = {
            "at": _now(), "event": event, "workflow_id": record.workflow_id,
            "task_id": record.task_id, "role": record.role, "round": record.round,
            "status": record.status, "callback_status": record.callback_status,
            "execution_status": record.execution_status,
            "dispatched_at": record.dispatched_at, "started_at": record.started_at,
            "last_activity_at": record.last_activity_at,
            "last_activity_kind": record.last_activity_kind,
            "completed_at": record.completed_at,
            "event_kind": record.event_kind,
            "result_length": record.result_length, "result_sha256": record.result_sha256,
            "document_warning": record.document_warning,
        }
        descriptor = os.open(LOG_DIR / "events.jsonl", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, (canonical_json(entry) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _active_for_role(self, role: str) -> TaskRecord | None:
        rows = [row for row in self._records() if row.role == role and row.status == "RUNNING"]
        return max(rows, key=lambda row: row.updated_at, default=None)

    def _latest_for_role(self, role: str) -> TaskRecord | None:
        rows = [row for row in self._records() if row.role == role]
        return max(rows, key=lambda row: row.updated_at, default=None)

    @staticmethod
    def _public(record: TaskRecord, already_submitted: bool = False) -> dict[str, object]:
        return {
            "workflow_id": record.workflow_id, "task_id": record.task_id,
            "role": record.role, "round": record.round, "status": record.execution_status,
            "execution_status": record.execution_status,
            "callback_status": record.callback_status, "result_length": record.result_length,
            "result_sha256": record.result_sha256, "blocked_reason": record.blocked_reason,
            "error_code": record.error_code,
            "document_warning": record.document_warning,
            "already_submitted": already_submitted,
        }

    def _refresh_documents(self) -> str:
        try:
            self.documents.refresh(
                self._records(), self._decisions(), self._role_rows()
            )
        except DocumentError:
            return "DOCUMENT_RENDER_FAILED"
        return ""

    def _role_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for role_name, role in self.roles.items():
            latest = self._latest_for_role(role_name)
            if hasattr(self.tmux, "probe"):
                process_status, pane = self.tmux.probe(role)
            else:
                try:
                    pane = self.tmux.fixed_pane(role)
                except TmuxError:
                    process_status, pane = "UNKNOWN", None
                else:
                    process_status = "ALIVE" if pane.pane_pid > 0 and not pane.dead else "DOWN"
            if latest is not None and _activity_is_recent(latest.last_activity_at) and process_status != "ALIVE":
                process_status = "UNKNOWN"
            pane_alive = True if pane is not None and not pane.dead else False if process_status == "DOWN" else None
            process_alive = True if process_status == "ALIVE" else False if process_status == "DOWN" else None
            pane_id = pane.pane_id if pane is not None else None
            active = self._active_for_role(role_name)
            execution_status = latest.execution_status if latest else "IDLE"
            derived = execution_status
            if (
                execution_status in {"STARTED", "RUNNING"}
                and latest is not None
                and _activity_is_stale(latest.last_activity_at)
            ):
                derived = f"{execution_status}_NO_RECENT_ACTIVITY"
            rows.append({
                "role": role_name, "pane_exists": pane_alive, "pane_id": pane_id,
                "pane_alive": pane_alive, "process_alive": process_alive,
                "process_status": process_status,
                "active_task_id": active.task_id if active else None,
                "latest_task_id": latest.task_id if latest else None,
                "workflow_id": latest.workflow_id if latest else None,
                "task_status": latest.status if latest else "IDLE",
                "callback_status": latest.callback_status if latest else "NONE",
                "error_code": latest.error_code if latest else "",
                "execution_status": execution_status,
                "derived_display_status": derived,
                "dispatched_at": latest.dispatched_at if latest else "",
                "started_at": latest.started_at if latest else "",
                "last_activity_at": latest.last_activity_at if latest else "",
                "last_activity_kind": latest.last_activity_kind if latest else "",
                "completed_at": latest.completed_at if latest else "",
                "event_kind": latest.event_kind if latest else "",
            })
        return rows

    def list_roles(self) -> dict[str, object]:
        self._require_supervisor()
        return {"roles": self._role_rows()}

    def _workflow_metadata_sections(self, ref_id: str) -> tuple[dict[str, Any], dict[str, object]]:
        kind, workflow_id = ref_id.split(":", 1)
        validate_workflow_id(workflow_id)
        filename = {
            "preflight": "preflight.json",
            "contract": "contract-freeze.json",
            "candidate": "final-candidate-sha.json",
            "authority": "authority.json",
        }[kind]
        payload = self._read_json(
            WORKFLOW_ROOT / "tasks" / workflow_id / "data" / filename,
            f"{kind} context",
        )
        if kind == "contract" and payload.get("status") != "CONTRACT_FROZEN":
            raise StateError("frozen contract context is unavailable")
        if kind == "candidate" and payload.get("status") != "FINAL_CANDIDATE_SHA_FROZEN":
            raise StateError("frozen candidate context is unavailable")
        if kind == "preflight":
            sections = {
                "summary": {
                    "execution_profile": resolve_execution_profile(payload),
                    "execution_reason": payload.get("execution_reason"),
                    "execution_triggers": payload.get("execution_triggers", []),
                    "unresolved_preflight_items": payload.get("unresolved_preflight_items", []),
                },
                "repository": {name: payload.get(name) for name in ("target_repo", "target_worktree", "base_branch", "task_branch", "base_sha", "current_target_sha", "working_tree_status")},
                "paths": {name: payload.get(name) for name in ("canonical_input_paths", "canonical_output_paths")},
                "toolchain": payload.get("dependency_toolchain", []),
                "access": {name: payload.get(name) for name in ("shared_workspace_access", "shared_workspace_root")},
                "publication_handoff": {name: payload.get(name) for name in ("private_artifact_owner", "private_artifact_publisher", "private_artifact_handoff_path", "candidate_artifact_path", "stable_artifact_path", "expected_git_handoff")},
                "unresolved_items": payload.get("unresolved_preflight_items", []),
            }
        elif kind == "contract":
            contract = payload.get("contract", {})
            if not isinstance(contract, dict):
                raise StateError("frozen contract context is invalid")
            sections = {"summary": {"status": payload.get("status"), "revision": payload.get("revision")}, **contract}
        elif kind == "candidate":
            sections = {
                "summary": {"status": payload.get("status"), "final_candidate_sha": payload.get("final_candidate_sha")},
                "identity": {name: payload.get(name) for name in ("repo", "repo_path", "branch", "base_sha", "final_candidate_sha")},
                "git_handoff": {name: payload.get(name) for name in ("commit_message", "remote_ref", "working_tree_clean", "frozen_at")},
            }
        else:
            if payload.get("status") == "AUTHORITY_CONFLICT":
                raise StateError("AUTHORITY_CONFLICT")
            active = payload.get("active")
            if payload.get("status") != "ACTIVE_AUTHORITY" or not isinstance(active, dict):
                raise StateError("effective authority is unavailable")
            sections = {
                "summary": {
                    "effective_plan_revision": active.get("effective_plan_revision"),
                    "effective_contract_revision": active.get("effective_contract_revision"),
                    "effective_decision_refs": active.get("effective_decision_refs", []),
                },
                "effective_plan": active.get("effective_plan_projection", {}),
                "effective_contract": active.get("effective_contract_projection", {}),
                "effective_decisions": active.get("effective_decisions", []),
            }
        return payload, sections

    def _effective_authority(self, workflow_id: str) -> dict[str, Any]:
        path = WORKFLOW_ROOT / "tasks" / workflow_id / "data" / "authority.json"
        if not path.exists():
            return {
                "status": "LEGACY_AUTHORITY_WARNING",
                "effective_plan_revision": None,
                "effective_contract_revision": None,
                "effective_decision_refs": [],
            }
        payload = self._read_json(path, "effective authority")
        if payload.get("status") == "AUTHORITY_CONFLICT":
            raise StateError("AUTHORITY_CONFLICT")
        active = payload.get("active")
        if payload.get("status") != "ACTIVE_AUTHORITY" or not isinstance(active, dict):
            raise StateError("AUTHORITY_CONFLICT")
        return {
            "status": "ACTIVE_AUTHORITY",
            "effective_plan_revision": active.get("effective_plan_revision"),
            "effective_contract_revision": active.get("effective_contract_revision"),
            "effective_decision_refs": active.get("effective_decision_refs", []),
            "superseded_refs": [
                ref
                for decision in active.get("effective_decisions", [])
                if isinstance(decision, dict)
                for ref in decision.get("supersedes", [])
                if isinstance(ref, str)
            ],
        }

    def _context_descriptor(self, ref: dict[str, Any]) -> dict[str, object]:
        ref_id = str(ref["ref_id"])
        kind = ref_type(ref_id)
        if kind == "task":
            task_id = ref_id.split(":", 1)[1]
            record = self._read_record(task_id)
            if record.status != "RESULT_READY":
                raise StateError("context task does not have an available result")
            envelope = self._read_result(task_id, record.round)
            for section in ref["allowed_sections"]:
                result_section(envelope, section)
            return {
                "ref_id": ref_id, "type": kind, "label": ref["label"],
                "role": record.role, "summary": envelope["summary"],
                "available_sections": ref["allowed_sections"], "hash": record.result_sha256,
                "authority_class": "ACTIVE_AUTHORITY",
            }
        if kind in {"preflight", "contract", "candidate", "authority"}:
            payload, sections = self._workflow_metadata_sections(ref_id)
            if any(section not in sections for section in ref["allowed_sections"]):
                raise ValidationError(f"{kind} section is unavailable")
            return {
                "ref_id": ref_id, "type": kind, "label": ref["label"], "role": "supervisor",
                "summary": sections["summary"], "available_sections": ref["allowed_sections"],
                "hash": sha256_text(canonical_json(payload)), "authority_class": "ACTIVE_AUTHORITY",
            }
        decision_id = ref_id.split(":", 1)[1]
        decision = self._read_json(self._decision_path(decision_id), "decision context")
        allowed = {"summary", "decision", "reason", "scope", "affected_refs"}
        if any(section not in allowed for section in ref["allowed_sections"]):
            raise ValidationError("decision section is unavailable")
        return {
            "ref_id": ref_id, "type": kind, "label": ref["label"], "role": "supervisor",
            "summary": decision["title"], "available_sections": ref["allowed_sections"],
            "hash": sha256_text(canonical_json(decision)), "authority_class": "ACTIVE_AUTHORITY",
        }

    def assign_task(
        self,
        workflow_id: str,
        role: str,
        task_id: str,
        objective: str,
        deliverables: list[str],
        acceptance_criteria: list[str],
        constraints: list[str],
        context_refs: list[dict[str, object]],
    ) -> dict[str, object]:
        self._require_supervisor()
        contract, warnings = build_task_contract(
            workflow_id, role, task_id, objective, deliverables,
            acceptance_criteria, constraints, context_refs,
        )
        authority = self._effective_authority(str(contract["workflow_id"]))
        contract["effective_authority"] = authority
        if authority["status"] == "ACTIVE_AUTHORITY":
            filtered_refs: list[dict[str, object]] = []
            active_decisions = set(authority.get("effective_decision_refs", []))
            for ref in contract["context_refs"]:
                kind = ref_type(str(ref["ref_id"]))
                if kind == "decision" and str(ref["ref_id"]) not in active_decisions:
                    continue
                if kind == "contract":
                    continue
                if kind == "task":
                    source = self._read_record(str(ref["ref_id"]).split(":", 1)[1])
                    if source.role == "explorer":
                        continue
                filtered_refs.append(ref)
            contract["context_refs"] = filtered_refs
            authority_ref = f"authority:{contract['workflow_id']}"
            if not any(ref["ref_id"] == authority_ref for ref in contract["context_refs"]):
                contract["context_refs"].append({
                    "ref_id": authority_ref,
                    "label": "Effective approved authority",
                    "allowed_sections": ["summary", "effective_plan", "effective_contract", "effective_decisions"],
                })
        elif not self._legacy_authority_warning_was_reported(str(contract["workflow_id"])):
            warnings.append("LEGACY_AUTHORITY_WARNING")
        contract_bytes = len(canonical_json(contract).encode("utf-8"))
        if contract_bytes > MAX_PAYLOAD_BYTES:
            raise ValidationError("task contract exceeds the technical payload limit")
        if contract_bytes > LARGE_TASK_WARNING_BYTES and "TASK_CONTRACT_LARGE" not in warnings:
            warnings.append("TASK_CONTRACT_LARGE")
        role, task_id = str(contract["role"]), str(contract["task_id"])
        with self._lock("all-tasks"), self._lock(role):
            if self._metadata_path(task_id).exists():
                raise StateError("task_id already exists")
            if self._active_for_role(role) is not None:
                raise StateError("role already has an active task")
            for ref in contract["context_refs"]:
                self._context_descriptor(ref)
            pane = self.tmux.fixed_pane(self.roles[role])
            text = canonical_json(contract)
            now = _now()
            record = TaskRecord(
                workflow_id=str(contract["workflow_id"]), task_id=task_id, role=role,
                nonce=secrets.token_hex(16), round=0, rework_count=0,
                created_at=now, updated_at=now, status="RUNNING", pane_id=pane.pane_id,
                contract_length=len(text.encode("utf-8")), contract_sha256=sha256_text(text),
                execution_status="DISPATCHED", dispatched_at=now,
            )
            self._write_json(self._contract_path(task_id), contract)
            self._write_record(record)
            try:
                self.documents.project_contract(contract)
                self.documents.refresh(
                    self._records(), self._decisions(), self._role_rows()
                )
            except DocumentError:
                record.document_warning = "DOCUMENT_RENDER_FAILED"
                self._write_record(record)
            self._log(record, "dispatched")
            try:
                self.tmux.paste(pane.pane_id, TASK_TRIGGER, f"crw-{secrets.token_hex(8)}")
            except TmuxError:
                record.error_code, record.updated_at = "WAKEUP_PENDING", _now()
                self._write_record(record)
                self._log(record, "wakeup_pending")
                warnings.append("WAKEUP_PENDING")
            return {**self._public(record), "warnings": warnings}

    def cancel_task(self, task_id: str, reason: str) -> dict[str, object]:
        self._require_supervisor()
        validate_task_id(task_id)
        validate_text(reason, field="reason", maximum=MAX_BLOCKED_REASON_CHARS)
        record = self._read_record(task_id)
        with self._lock(record.role):
            record = self._read_record(task_id)
            if record.status == "CANCELLED":
                if record.blocked_reason != reason:
                    raise StateError("task was already cancelled with a different reason")
                return self._public(record, True)
            if record.status != "RUNNING":
                raise StateError("only a running task can be cancelled")
            cancelled_at = _now()
            record.status = "CANCELLED"
            record.execution_status = "IDLE"
            record.error_code = "CANCELLED_BY_SUPERVISOR"
            record.blocked_reason = reason
            record.last_activity_at = cancelled_at
            record.last_activity_kind = "CANCEL_TASK"
            record.completed_at = cancelled_at
            record.updated_at = cancelled_at
            self._write_record(record)
            self._log(record, "cancelled")
            warning = self._refresh_documents()
            if warning:
                record.document_warning = warning
                self._write_record(record)
            return self._public(record)

    def retry_dispatch(self, task_id: str) -> dict[str, object]:
        self._require_supervisor()
        validate_task_id(task_id)
        record = self._read_record(task_id)
        with self._lock(record.role):
            record = self._read_record(task_id)
            if record.status != "RUNNING" or record.error_code != "WAKEUP_PENDING":
                raise StateError("task wakeup is not pending")
            pane = self.tmux.fixed_pane(self.roles[record.role])
            try:
                self.tmux.paste(pane.pane_id, TASK_TRIGGER, f"crw-{secrets.token_hex(8)}")
            except TmuxError:
                raise StateError("task wakeup retry failed") from None
            record.pane_id = pane.pane_id
            record.error_code = ""
            record.updated_at = _now()
            self._write_record(record)
            self._log(record, "wakeup_delivered")
            return self._public(record)

    def retry_callback(self, task_id: str) -> dict[str, object]:
        self._require_supervisor()
        validate_task_id(task_id)
        record = self._read_record(task_id)
        with self._lock(record.role):
            record = self._read_record(task_id)
            if record.status not in {"RESULT_READY", "BLOCKED"}:
                raise StateError("task does not have a callback-ready result")
            if record.callback_status == "DELIVERED":
                return self._public(record, True)
            if record.callback_status != "PENDING":
                raise StateError("task callback is not pending")
            try:
                self._deliver_callback(record)
            except TmuxError:
                raise StateError("task callback retry failed") from None
            return self._public(record)

    def get_current_task(self) -> dict[str, object]:
        worker = self._require_worker()
        with self._lock(worker):
            record = self._active_for_role(worker)
            if record is None:
                raise StateError("role has no active task")
            contract = self._read_contract(record.task_id)
            manifest = [self._context_descriptor(ref) for ref in self._context_refs(record)]
            response: dict[str, object] = {
                "task_contract": contract, "round": record.round, "nonce": record.nonce,
                "context_manifest": manifest,
                "effective_authority": contract.get("effective_authority", {"status": "LEGACY_AUTHORITY_WARNING"}),
                "warnings": ["TASK_CONTRACT_LARGE"]
                if record.contract_length > LARGE_TASK_WARNING_BYTES else [],
            }
            if record.round:
                response["rework_delta"] = self._read_json(
                    self._rework_path(record.task_id, record.round), "rework delta"
                )
            first_start = record.execution_status == "DISPATCHED"
            activity_at = _now()
            if first_start:
                record.execution_status = "STARTED"
                record.started_at = activity_at
            record.last_activity_at = activity_at
            record.last_activity_kind = "GET_CURRENT_TASK"
            record.updated_at = activity_at
            self._write_record(record)
            if first_start:
                self._log(record, "started")
                self._refresh_documents()
                try:
                    self._deliver_worker_started()
                except TmuxError:
                    pass
            return response

    def _context_refs(self, record: TaskRecord) -> list[dict[str, Any]]:
        refs = list(self._read_contract(record.task_id)["context_refs"])
        if record.round:
            delta = self._read_json(
                self._rework_path(record.task_id, record.round), "rework delta"
            )
            added = delta.get("added_context_refs", [])
            if not isinstance(added, list):
                raise StateError("rework delta is invalid")
            refs.extend(added)
        return refs

    def _authorized_ref(self, contract: dict[str, Any], ref_id: str, section: str) -> dict[str, Any]:
        active = self._active_for_role(self.caller_role)
        if active is None or active.task_id != str(contract["task_id"]):
            raise StateError("role has no active task")
        for ref in self._context_refs(active):
            if ref["ref_id"] == ref_id and section in ref["allowed_sections"]:
                return ref
        raise StateError("context section is not authorized")

    def get_context(self, ref_id: str, section: str) -> dict[str, object]:
        worker = self._require_worker()
        validate_ref_id(ref_id)
        validate_text(section, field="section", maximum=MAX_PAYLOAD_BYTES)
        with self._lock(worker):
            active = self._active_for_role(worker)
            if active is None:
                raise StateError("role has no active task")
            ref = self._authorized_ref(self._read_contract(active.task_id), ref_id, section)
            descriptor = self._context_descriptor(ref)
            kind = ref_type(ref_id)
            if kind == "task":
                source = self._read_record(ref_id.split(":", 1)[1])
                content = result_section(self._read_result(source.task_id, source.round), section)
            elif kind in {"preflight", "contract", "candidate", "authority"}:
                _, sections = self._workflow_metadata_sections(ref_id)
                content = sections[section]
            else:
                decision = self._read_json(
                    self._decision_path(ref_id.split(":", 1)[1]), "decision context"
                )
                content = decision["title"] if section == "summary" else decision[section]
            response = {
                "ref_id": ref_id,
                "section": section,
                "content": content,
                "hash": descriptor["hash"],
            }
            if json_size(response) > MAX_PAYLOAD_BYTES:
                raise StateError("context exceeds the technical payload limit")
            activity_at = _now()
            became_running = active.execution_status in {"DISPATCHED", "STARTED"}
            active.last_activity_at = activity_at
            active.last_activity_kind = "GET_CONTEXT"
            if became_running:
                active.execution_status = "RUNNING"
            active.updated_at = activity_at
            self._write_record(active)
            if became_running:
                self._log(active, "running")
                self._refresh_documents()
            return response

    def _validate_worker_submission(
        self, workflow_id: str, task_id: str, round_number: int, nonce: str
    ) -> TaskRecord:
        worker = self._require_worker()
        validate_workflow_id(workflow_id)
        validate_task_id(task_id)
        validate_nonce(nonce)
        record = self._read_record(task_id)
        if record.workflow_id != workflow_id or record.role != worker:
            raise StateError("task identity does not match this role")
        if record.round != round_number or record.nonce != nonce:
            raise StateError("task round or nonce does not match")
        if record.status == "RUNNING":
            active = self._active_for_role(worker)
            if active is None or active.task_id != task_id:
                raise StateError("task is not the active task for this role")
        return record

    def _authorized_sources(self, task_id: str) -> dict[str, set[str]]:
        record = self._read_record(task_id)
        return {
            str(ref["ref_id"]): set(ref["allowed_sections"])
            for ref in self._context_refs(record)
        }

    def _deliver_worker_started(self) -> None:
        pane = self.tmux.fixed_pane(self.roles["supervisor"])
        self.tmux.paste(
            pane.pane_id,
            WORKER_STARTED_TRIGGER,
            f"crw-{secrets.token_hex(8)}",
        )

    def _deliver_callback(self, record: TaskRecord) -> None:
        if record.callback_status != "PENDING":
            return
        pane = self.tmux.fixed_pane(self.roles["supervisor"])
        trigger = RESULT_TRIGGER.format(
            workflow_id=record.workflow_id,
            task_id=record.task_id,
            role=record.role,
            status=record.status,
        )
        self.tmux.paste(pane.pane_id, trigger, f"crw-{secrets.token_hex(8)}")
        record.callback_status, record.updated_at = "DELIVERED", _now()
        self._write_record(record)
        self._log(record, "callback_delivered")
        warning = self._refresh_documents()
        if warning and not record.document_warning:
            record.document_warning = warning
            self._write_record(record)

    def submit_result(
        self,
        workflow_id: str,
        task_id: str,
        round_number: int,
        nonce: str,
        summary: str,
        decisions: list[object],
        open_issues: list[object],
        evidence: list[object],
        sections: dict[str, object],
        source_refs: list[dict[str, str]],
    ) -> dict[str, object]:
        worker = self._require_worker()
        envelope = validate_result_envelope(
            workflow_id, worker, task_id, round_number, nonce, summary, decisions,
            open_issues, evidence, sections, source_refs, self._authorized_sources(task_id),
        )
        text, result_hash = canonical_json(envelope), sha256_text(canonical_json(envelope))
        with self._lock(worker):
            record = self._validate_worker_submission(workflow_id, task_id, round_number, nonce)
            if record.status == "RESULT_READY":
                if record.result_sha256 != result_hash:
                    raise StateError("a different result was already submitted")
                if record.callback_status == "PENDING":
                    try:
                        self._deliver_callback(record)
                    except TmuxError:
                        pass
                return self._public(record, True)
            if record.status != "RUNNING":
                raise StateError("task is not accepting a result")
            self._write_json(self._result_path(task_id, round_number), envelope)
            completed_at = _now()
            record.status, record.result_length, record.result_sha256 = "RESULT_READY", len(text.encode("utf-8")), result_hash
            record.blocked_reason, record.callback_status, record.updated_at = "", "PENDING", completed_at
            record.execution_status = "RESULT_READY"
            record.last_activity_at = completed_at
            record.last_activity_kind = "SUBMIT_RESULT"
            record.completed_at = completed_at
            self._write_record(record)
            try:
                self.documents.project_result(record, envelope)
                self.documents.refresh(
                    self._records(), self._decisions(), self._role_rows()
                )
            except DocumentError:
                record.document_warning = "DOCUMENT_RENDER_FAILED"
                self._write_record(record)
            self._log(record, "result_ready")
            try:
                self._deliver_callback(record)
            except TmuxError:
                pass
            return self._public(record)

    def report_blocked(
        self, workflow_id: str, task_id: str, round_number: int, nonce: str, reason: str
    ) -> dict[str, object]:
        worker = self._require_worker()
        validate_text(reason, field="reason", maximum=MAX_BLOCKED_REASON_CHARS)
        with self._lock(worker):
            record = self._validate_worker_submission(workflow_id, task_id, round_number, nonce)
            if record.status == "BLOCKED":
                if record.blocked_reason != reason:
                    raise StateError("a different blocked reason was already submitted")
                if record.callback_status == "PENDING":
                    try:
                        self._deliver_callback(record)
                    except TmuxError:
                        pass
                return self._public(record, True)
            if record.status != "RUNNING":
                raise StateError("task is not accepting a blocked report")
            blocked_at = _now()
            self._write_json(
                self._blocked_path(task_id, record.round),
                {
                    "workflow_id": record.workflow_id,
                    "task_id": record.task_id,
                    "role": record.role,
                    "round": record.round,
                    "reason": reason,
                    "blocked_at": blocked_at,
                },
            )
            record.status, record.blocked_reason = "BLOCKED", reason
            record.callback_status, record.updated_at = "PENDING", blocked_at
            record.execution_status = "BLOCKED"
            record.last_activity_at = blocked_at
            record.last_activity_kind = "REPORT_BLOCKED"
            record.completed_at = blocked_at
            self._write_record(record)
            self._log(record, "blocked")
            warning = self._refresh_documents()
            if warning:
                record.document_warning = warning
                self._write_record(record)
            try:
                self._deliver_callback(record)
            except TmuxError:
                pass
            return self._public(record)

    def _document_refs(self, record: TaskRecord) -> list[str]:
        root = WORKFLOW_ROOT / "tasks" / record.workflow_id
        candidates = [root / ROLE_DOCUMENTS[record.role], root / "index.md", root / "handoff.md"]
        return [str(path.relative_to(WORKFLOW_ROOT)) for path in candidates if path.is_file()]

    def get_task_result(self, task_id: str, section: str | None = None) -> dict[str, object]:
        self._require_supervisor()
        validate_task_id(task_id)
        record = self._read_record(task_id)
        if record.status == "BLOCKED":
            return {**self._public(record), "section": "blocked_reason", "content": record.blocked_reason}
        if record.status != "RESULT_READY":
            raise StateError("task result is not ready")
        envelope = self._read_result(task_id, record.round)
        sections = envelope.get("sections", {})
        assert isinstance(sections, dict)
        if section is None:
            return {
                "workflow_id": record.workflow_id, "task_id": task_id, "role": record.role,
                "round": record.round, "status": record.status, "summary": envelope["summary"],
                "decisions": envelope["decisions"], "open_issues": envelope["open_issues"],
                "evidence_summary": {"count": len(envelope["evidence"])},
                "available_sections": list(sections), "hash": record.result_sha256,
                "document_refs": self._document_refs(record),
                "document_warning": record.document_warning,
            }
        validate_text(section, field="section", maximum=MAX_PAYLOAD_BYTES)
        return {
            "workflow_id": record.workflow_id, "task_id": task_id, "role": record.role,
            "round": record.round, "status": record.status, "section": section,
            "content": result_section(envelope, section), "hash": record.result_sha256,
            "document_refs": self._document_refs(record),
            "document_warning": record.document_warning,
        }

    def send_rework(
        self,
        task_id: str,
        missing_items: list[str] | None = None,
        failed_acceptance_criteria: list[str] | None = None,
        required_changes: list[str] | None = None,
        clarified_information: str = "",
        added_context_refs: list[dict[str, object]] | None = None,
        required_next_action: str = "",
        event_kind: str = "",
        invariant_type: str = "",
    ) -> dict[str, object]:
        self._require_supervisor()
        validate_task_id(task_id)
        missing_items = [] if missing_items is None else missing_items
        failed_acceptance_criteria = (
            [] if failed_acceptance_criteria is None else failed_acceptance_criteria
        )
        required_changes = [] if required_changes is None else required_changes
        for field, value in {
            "missing_items": missing_items,
            "failed_acceptance_criteria": failed_acceptance_criteria,
            "required_changes": required_changes,
        }.items():
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValidationError(f"{field} must be a list of strings")
        if not isinstance(clarified_information, str) or not isinstance(required_next_action, str):
            raise ValidationError("unblock text fields must be strings")
        if clarified_information:
            validate_text(
                clarified_information,
                field="clarified_information",
                maximum=MAX_PAYLOAD_BYTES,
            )
        if required_next_action:
            validate_text(
                required_next_action,
                field="required_next_action",
                maximum=MAX_PAYLOAD_BYTES,
            )
        added_refs = validate_context_refs(
            [] if added_context_refs is None else added_context_refs
        )
        if event_kind and event_kind not in EVENT_KINDS:
            raise ValidationError("event_kind is invalid")
        if not isinstance(invariant_type, str):
            raise ValidationError("invariant_type must be a string")
        record = self._read_record(task_id)
        with self._lock(record.role):
            record = self._read_record(task_id)
            if record.status not in {"RESULT_READY", "BLOCKED"}:
                raise StateError("rework requires a ready or blocked task")
            if record.rework_count >= 100:
                raise StateError("rework technical limit has been reached")
            if self._active_for_role(record.role) is not None:
                raise StateError("role already has an active task")
            if record.status == "RESULT_READY":
                if clarified_information or added_refs or required_next_action:
                    raise ValidationError("ready-result rework accepts only result correction fields")
                delta_base: dict[str, object] = {
                    "missing_items": missing_items,
                    "failed_acceptance_criteria": failed_acceptance_criteria,
                    "required_changes": required_changes,
                }
                if not any(delta_base.values()):
                    raise ValidationError("rework delta must not be empty")
                event = "rework_running"
                selected_event_kind = event_kind or "IMPLEMENTATION_REVISION"
            else:
                if missing_items or failed_acceptance_criteria or required_changes:
                    raise ValidationError("blocked recovery accepts only unblock fields")
                if not required_next_action.strip():
                    raise ValidationError("required_next_action must not be empty")
                if not clarified_information.strip() and not added_refs:
                    raise ValidationError("blocked recovery requires information or context")
                for ref in added_refs:
                    self._context_descriptor(ref)
                delta_base = {
                    "clarified_information": clarified_information,
                    "added_context_refs": added_refs,
                    "required_next_action": required_next_action,
                }
                event = "unblocked_running"
                selected_event_kind = event_kind or "BLOCKED_RECOVERY"
            checked_invariant = self._semantic_repair_checkpoint(
                record.workflow_id, selected_event_kind, invariant_type
            )
            if json_size(delta_base) > MAX_PAYLOAD_BYTES:
                raise ValidationError("rework delta exceeds the technical payload limit")
            pane = self.tmux.fixed_pane(self.roles[record.role])
            record.round += 1
            record.rework_count += 1
            record.nonce = secrets.token_hex(16)
            delta = {**delta_base, "round": record.round, "nonce": record.nonce}
            self._write_json(self._rework_path(task_id, record.round), delta)
            record.status, record.callback_status = "RUNNING", "NONE"
            record.result_length, record.result_sha256, record.blocked_reason = 0, "", ""
            dispatched_at = _now()
            record.error_code, record.document_warning, record.updated_at = "", "", dispatched_at
            record.execution_status = "DISPATCHED"
            record.dispatched_at = dispatched_at
            record.started_at = ""
            record.last_activity_at = ""
            record.last_activity_kind = ""
            record.completed_at = ""
            record.event_kind = selected_event_kind
            record.pane_id = pane.pane_id
            self._write_record(record)
            self._log(record, event)
            warning = self._refresh_documents()
            if warning:
                record.document_warning = warning
                self._write_record(record)
            self._record_refinement_event(record, selected_event_kind, checked_invariant)
            try:
                self.tmux.paste(pane.pane_id, TASK_TRIGGER, f"crw-{secrets.token_hex(8)}")
            except TmuxError:
                record.error_code, record.updated_at = "WAKEUP_PENDING", _now()
                self._write_record(record)
                self._log(record, "wakeup_pending")
                return {**self._public(record), "warnings": ["WAKEUP_PENDING"]}
            return {**self._public(record), "warnings": []}

    def record_decision(
        self,
        decision_id: str,
        title: str,
        decision: str,
        reason: str,
        scope: str,
        affected_refs: list[str],
        supersedes: list[str] | None = None,
        replacement: str = "",
        authority_ref: str = "",
    ) -> dict[str, object]:
        self._require_supervisor()
        validate_decision_id(decision_id)
        for field, value in (("title", title), ("decision", decision), ("reason", reason), ("scope", scope)):
            validate_text(value, field=field, maximum=MAX_PAYLOAD_BYTES)
        if not isinstance(affected_refs, list):
            raise ValidationError("affected_refs must be a list")
        for ref_id in affected_refs:
            validate_ref_id(ref_id)
        supersedes = [] if supersedes is None else supersedes
        if not isinstance(supersedes, list) or any(not isinstance(item, str) or not item for item in supersedes):
            raise ValidationError("supersedes must be a list of authority refs")
        if supersedes and (not replacement.strip() or not authority_ref.strip()):
            raise ValidationError("superseding decision requires replacement and authority_ref")
        payload = {
            "decision_id": decision_id, "title": title, "decision": decision,
            "reason": reason, "scope": scope, "affected_refs": affected_refs,
            "status": "Approved", "date": _now(),
            "supersedes": supersedes, "replacement": replacement,
            "approved_at": _now(), "authority_ref": authority_ref or f"decision:{decision_id}",
        }
        if json_size(payload) > MAX_PAYLOAD_BYTES:
            raise ValidationError("decision exceeds the technical payload limit")
        path = self._decision_path(decision_id)
        with self._lock("decisions"):
            if path.exists():
                raise StateError("decision_id already exists")
            self._write_json(path, payload)
            warning = ""
            try:
                document_ref = self.documents.append_decision(payload)
                self.documents.refresh(
                    self._records(), self._decisions(), self._role_rows()
                )
            except DocumentError:
                document_ref, warning = "", "DOCUMENT_RENDER_FAILED"
        return {
            "decision_id": decision_id, "status": "Approved",
            "document_ref": document_ref, "document_warning": warning,
        }
