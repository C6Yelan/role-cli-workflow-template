"""Typed metadata for fixed Project role tasks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

TaskStatus = Literal["RUNNING", "RESULT_READY", "BLOCKED", "FAILED", "CANCELLED"]
CallbackStatus = Literal["NONE", "PENDING", "DELIVERED"]
ExecutionStatus = Literal[
    "IDLE", "DISPATCHED", "STARTED", "RUNNING", "RESULT_READY", "BLOCKED", "REWORK_REQUIRED"
]


def _execution_status(data: dict[str, object]) -> str:
    value = str(data.get("execution_status", ""))
    if value == "WORKER_UNAVAILABLE":
        if str(data.get("status", "")) != "RUNNING":
            return "IDLE"
        return {
            "GET_CURRENT_TASK": "STARTED",
            "GET_CONTEXT": "RUNNING",
        }.get(str(data.get("last_activity_kind", "")), "DISPATCHED")
    if value:
        return value
    return {
        "RUNNING": "DISPATCHED",
        "RESULT_READY": "RESULT_READY",
        "BLOCKED": "BLOCKED",
        "FAILED": "IDLE",
        "CANCELLED": "IDLE",
    }.get(str(data.get("status", "")), "IDLE")


@dataclass(frozen=True)
class RoleConfig:
    name: str
    session: str
    window: str
    working_directory: str


@dataclass
class TaskRecord:
    workflow_id: str
    task_id: str
    role: str
    nonce: str
    round: int
    rework_count: int
    created_at: str
    updated_at: str
    status: TaskStatus
    pane_id: str
    contract_length: int
    contract_sha256: str
    result_length: int = 0
    result_sha256: str = ""
    blocked_reason: str = ""
    error_code: str = ""
    document_warning: str = ""
    callback_status: CallbackStatus = "NONE"
    execution_status: ExecutionStatus = "DISPATCHED"
    dispatched_at: str = ""
    started_at: str = ""
    last_activity_at: str = ""
    last_activity_kind: str = ""
    completed_at: str = ""
    event_kind: str = "INITIAL_IMPLEMENTATION"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "TaskRecord":
        return cls(
            workflow_id=str(data.get("workflow_id", "legacy")),
            task_id=str(data["task_id"]),
            role=str(data["role"]),
            nonce=str(data["nonce"]),
            round=int(data["round"]),
            rework_count=int(data["rework_count"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            status=str(data["status"]),  # type: ignore[arg-type]
            pane_id=str(data["pane_id"]),
            contract_length=int(data.get("contract_length", data.get("prompt_length", 0))),
            contract_sha256=str(data.get("contract_sha256", data.get("prompt_sha256", ""))),
            result_length=int(data.get("result_length", 0)),
            result_sha256=str(data.get("result_sha256", "")),
            blocked_reason=str(data.get("blocked_reason", "")),
            error_code=str(data.get("error_code", "")),
            document_warning=str(data.get("document_warning", "")),
            callback_status=str(data.get("callback_status", "NONE")),  # type: ignore[arg-type]
            execution_status=_execution_status(data),  # type: ignore[arg-type]
            dispatched_at=str(data.get("dispatched_at", data.get("created_at", ""))),
            started_at=str(data.get("started_at", "")),
            last_activity_at=str(data.get("last_activity_at", "")),
            last_activity_kind=str(data.get("last_activity_kind", "")),
            completed_at=str(data.get("completed_at", "")),
            event_kind=str(data.get("event_kind", "INITIAL_IMPLEMENTATION")),
        )
