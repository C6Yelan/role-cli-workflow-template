"""Bridge input validation and canonical instruction loading."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from ..config import WORKERS

WORKER_ROLES = frozenset(WORKERS)

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DECISION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REF_ID_RE = re.compile(r"^(?:task:[A-Za-z0-9][A-Za-z0-9_-]{0,63}|decision:[A-Za-z0-9][A-Za-z0-9_.-]{0,63})$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_PAYLOAD_BYTES = 1024 * 1024
LARGE_TASK_WARNING_BYTES = 64 * 1024
MAX_BLOCKED_REASON_CHARS = 64 * 1024
CALLER_ROLES = frozenset({"supervisor", *WORKER_ROLES})


class ValidationError(ValueError):
    pass


def validate_caller_role(role: str | None) -> str:
    if role not in CALLER_ROLES:
        raise ValidationError("ROLE_CLI_WORKFLOW_ROLE is invalid")
    return role


def validate_worker_role(role: str) -> str:
    if role not in WORKER_ROLES:
        raise ValidationError("worker role is not allowed")
    return role


def _validated(value: str, pattern: re.Pattern[str], field: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValidationError(f"{field} format is invalid")
    return value


def validate_task_id(value: str) -> str: return _validated(value, TASK_ID_RE, "task_id")
def validate_workflow_id(value: str) -> str: return _validated(value, WORKFLOW_ID_RE, "workflow_id")
def validate_decision_id(value: str) -> str: return _validated(value, DECISION_ID_RE, "decision_id")
def validate_ref_id(value: str) -> str: return _validated(value, REF_ID_RE, "ref_id")
def validate_nonce(value: str) -> str: return _validated(value, NONCE_RE, "nonce")


def validate_text(value: str, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must not be empty")
    if len(value) > maximum or "\x00" in value:
        raise ValidationError(f"{field} is too large or invalid")
    return value


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def toml_instruction_override(role: str, project_root: Path | None = None) -> str:
    validate_caller_role(role)
    if project_root is None:
        raw = os.environ.get("ROLE_CLI_WORKFLOW_PROJECT_ROOT", "")
        if not raw:
            raise ValidationError("project root environment is missing")
        root = Path(raw).expanduser().resolve()
    else:
        root = project_root.resolve()
    role_path = root / "shared_workspace" / "roles" / f"{role}.md"
    project_path = root / ".role-cli-workflow" / "project_instructions.md"
    try:
        content = role_path.read_text(encoding="utf-8") + "\n\n" + project_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("canonical instruction file is unavailable") from exc
    return "developer_instructions=" + json.dumps(content, ensure_ascii=True)


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    try:
        print(toml_instruction_override(argv[1]))
        return 0
    except ValidationError:
        print("launcher safety check failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
