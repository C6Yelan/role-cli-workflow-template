"""Durable records for the minimal TaskStore."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

TaskStatus = Literal["DISPATCHED", "RUNNING", "RESULT_READY", "BLOCKED", "ACCEPTED", "CANCELLED"]
NoticeStatus = Literal["NONE", "PENDING", "DELIVERED"]


@dataclass(frozen=True)
class RoleConfig:
    name: str
    session: str
    window: str
    working_directory: str


@dataclass
class TaskRecord:
    schema_version: int
    workflow_id: str
    task_id: str
    role: str
    round: int
    nonce: str
    status: TaskStatus
    task_contract_hash: str
    input_candidate_sha: str
    produced_candidate_sha: str
    result_hash: str
    dispatch_notice_status: NoticeStatus
    result_notice_status: NoticeStatus
    created_at: str
    updated_at: str
    started_at: str = ""
    completed_at: str = ""
    blocked_reason: str = ""
    warning: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "TaskRecord":
        if int(data.get("schema_version", 0)) != 2:
            raise ValueError("legacy task metadata is historical and is not an active TaskStore record")
        return cls(
            schema_version=2,
            workflow_id=str(data["workflow_id"]),
            task_id=str(data["task_id"]),
            role=str(data["role"]),
            round=int(data["round"]),
            nonce=str(data["nonce"]),
            status=str(data["status"]),  # type: ignore[arg-type]
            task_contract_hash=str(data["task_contract_hash"]),
            input_candidate_sha=str(data.get("input_candidate_sha", "")),
            produced_candidate_sha=str(data.get("produced_candidate_sha", "")),
            result_hash=str(data.get("result_hash", "")),
            dispatch_notice_status=str(data.get("dispatch_notice_status", "NONE")),  # type: ignore[arg-type]
            result_notice_status=str(data.get("result_notice_status", "NONE")),  # type: ignore[arg-type]
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            started_at=str(data.get("started_at", "")),
            completed_at=str(data.get("completed_at", "")),
            blocked_reason=str(data.get("blocked_reason", "")),
            warning=str(data.get("warning", "")),
        )
