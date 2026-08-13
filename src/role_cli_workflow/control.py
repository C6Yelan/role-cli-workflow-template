"""Durable ownership for optional on-demand workflow escalation."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import ESCALATION_CONTROLLER

CONTROL_SCHEMA_VERSION = 1
NORMAL_CONTROLLER = "supervisor"
CONTROL_MODES = frozenset({"NORMAL", "ESCALATION"})
INTERVENTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_CONTROL_TEXT = 4096
LOCK_TIMEOUT_SECONDS = 5.0


class ControlError(RuntimeError):
    """Sanitized workflow-control failure."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: str, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > MAX_CONTROL_TEXT:
        raise ControlError(f"{field} is invalid")
    value = value.strip()
    if required and not value:
        raise ControlError(f"{field} must not be empty")
    return value


def _intervention_id(value: str) -> str:
    if not isinstance(value, str) or not INTERVENTION_ID_RE.fullmatch(value):
        raise ControlError("intervention_id format is invalid")
    return value


@dataclass(frozen=True)
class ControlState:
    schema_version: int
    mode: str
    intervention_id: str
    reason: str
    changed_at: str
    release_summary: str

    @property
    def owner(self) -> str:
        return ESCALATION_CONTROLLER if self.mode == "ESCALATION" else NORMAL_CONTROLLER

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "owner": self.owner}

    @classmethod
    def normal(cls) -> "ControlState":
        return cls(CONTROL_SCHEMA_VERSION, "NORMAL", "", "", "", "")

    @classmethod
    def from_dict(cls, value: object) -> "ControlState":
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "mode", "intervention_id", "reason", "changed_at",
            "release_summary",
        }:
            raise ControlError("control state is invalid")
        if (
            not isinstance(value["schema_version"], int)
            or isinstance(value["schema_version"], bool)
            or not all(
                isinstance(value[field], str)
                for field in (
                    "mode", "intervention_id", "reason", "changed_at",
                    "release_summary",
                )
            )
        ):
            raise ControlError("control state is invalid")
        state = cls(
            schema_version=value["schema_version"],
            mode=value["mode"],
            intervention_id=value["intervention_id"],
            reason=value["reason"],
            changed_at=value["changed_at"],
            release_summary=value["release_summary"],
        )
        if state.schema_version != CONTROL_SCHEMA_VERSION or state.mode not in CONTROL_MODES:
            raise ControlError("control state is invalid")
        if state.mode == "ESCALATION":
            _intervention_id(state.intervention_id)
            _text(state.reason, "reason")
            if state.release_summary:
                raise ControlError("control state is invalid")
        elif any((state.intervention_id, state.reason, state.changed_at, state.release_summary)):
            _intervention_id(state.intervention_id)
            _text(state.reason, "reason")
            _text(state.release_summary, "release_summary")
        try:
            if state.changed_at:
                changed_at = datetime.fromisoformat(
                    state.changed_at.replace("Z", "+00:00")
                )
                if changed_at.tzinfo is None:
                    raise ValueError
            elif state.mode != "NORMAL":
                raise ValueError
        except ValueError as exc:
            raise ControlError("control state is invalid") from exc
        return state


class ControlStore:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.path = runtime_root / "control.json"
        self.lock_path = runtime_root / "control.lock"
        self.metadata_dir = runtime_root / "metadata"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.runtime_root, 0o700)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                raise ControlError("workflow control lock is invalid")
        except OSError as exc:
            raise ControlError("workflow control lock is invalid") from exc
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ControlError("workflow control state is busy; retry") from None
                    time.sleep(0.05)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def read(self) -> ControlState:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return ControlState.normal()
        except OSError as exc:
            raise ControlError("control state is invalid") from exc
        try:
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_size > MAX_CONTROL_TEXT * 2
            ):
                raise ValueError
            return ControlState.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ControlError("control state is invalid") from exc

    def _write(self, state: ControlState) -> None:
        data = (json.dumps(asdict(state), sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".control-", dir=self.runtime_root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _attention_tasks(self) -> list[str]:
        attention: list[str] = []
        for path in sorted(self.metadata_dir.glob("*.json")) if self.metadata_dir.is_dir() else []:
            if path.name == "active-tasks.json":
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ControlError("task metadata is invalid") from exc
            if not isinstance(value, dict):
                raise ControlError("task metadata is invalid")
            if (
                value.get("status") == "RUNNING"
                or value.get("callback_status") == "PENDING"
                or value.get("error_code") == "WAKEUP_PENDING"
            ):
                task_id = value.get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    raise ControlError("task metadata is invalid")
                attention.append(task_id)
        return attention

    @contextmanager
    def mutation_guard(self, caller: str) -> Iterator[ControlState]:
        with self.locked():
            state = self.read()
            if caller != state.owner:
                raise ControlError(f"workflow control belongs to {state.owner}")
            yield state

    def acquire(self, intervention_id: str, reason: str) -> ControlState:
        intervention_id = _intervention_id(intervention_id)
        reason = _text(reason, "reason")
        with self.locked():
            state = self.read()
            if state.mode != "NORMAL":
                raise ControlError("workflow escalation is already active")
            attention = self._attention_tasks()
            if attention:
                raise ControlError("ACTIVE_TASKS_PRESENT: " + ", ".join(attention))
            state = ControlState(
                CONTROL_SCHEMA_VERSION, "ESCALATION", intervention_id, reason, _now(), ""
            )
            self._write(state)
            return state

    def release(self, intervention_id: str, summary: str) -> ControlState:
        intervention_id = _intervention_id(intervention_id)
        summary = _text(summary, "summary")
        with self.locked():
            state = self.read()
            if state.mode != "ESCALATION":
                raise ControlError("workflow escalation is not active")
            if state.intervention_id != intervention_id:
                raise ControlError("intervention_id does not match active escalation")
            released = ControlState(
                CONTROL_SCHEMA_VERSION,
                "NORMAL",
                state.intervention_id,
                state.reason,
                _now(),
                summary,
            )
            self._write(released)
            return released
