"""Fixed, single-approval Supervisor Git transaction executor."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .config import ProjectConfig, ROLES
from .project import git
from .refinement import WorkflowRefinementStore

OPERATIONS = ("add", "commit", "push")
PARTIAL_STATES = (
    "NOT_STARTED", "ADD_COMPLETED", "COMMIT_COMPLETED", "PUSH_COMPLETED",
    "ADD_FAILED", "COMMIT_FAILED", "PUSH_FAILED", "INVALIDATED",
)
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")
_FORBIDDEN_PATH_PATTERNS = (
    ".codex", ".codex/*", ".role-cli-workflow", ".role-cli-workflow/*",
    "shared_workspace/runtime", "shared_workspace/runtime/*",
    "shared_workspace/workflow/tasks/*/data/*",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.crt", "*.cer",
)


class GitTransactionError(RuntimeError):
    """A transaction is unsafe, stale, unauthorized, or failed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise GitTransactionError(f"{label} is invalid")
    return value


def _safe_ref(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_REF.fullmatch(value) or ".." in value:
        raise GitTransactionError(f"{label} is invalid")
    return value


def _relative_file(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise GitTransactionError("explicit file must be repository-relative")
    return path.as_posix()


def _atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitTransactionError("transaction is unavailable") from exc
    if not isinstance(payload, dict):
        raise GitTransactionError("transaction is invalid")
    return payload


def _status_paths(repo: Path) -> tuple[list[str], str]:
    result = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    tokens = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token:
            index += 1
            continue
        if len(token) < 4:
            raise GitTransactionError("unsupported Git status entry")
        code, path = token[:2], token[3:]
        paths.append(path)
        if "R" in code or "C" in code:
            index += 1
            if index < len(tokens) and tokens[index]:
                paths.append(tokens[index])
        index += 1
    return sorted(set(paths)), result.stdout


def _fingerprints(repo: Path, files: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in files:
        path = repo / item
        if path.is_symlink():
            values[item] = "symlink:" + os.readlink(path)
        elif path.is_file():
            values[item] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.exists():
            raise GitTransactionError(f"explicit file is not a regular file: {item}")
        else:
            values[item] = "deleted"
    return values


def _matches_pattern(path: str, pattern: str) -> bool:
    normalized = pattern.rstrip("/")
    return path == normalized or fnmatch.fnmatch(path, pattern) or path.startswith(normalized + "/")


def _sensitive(config: ProjectConfig, path: str) -> bool:
    lowered = path.lower()
    if any(word in lowered for word in ("credential", "private_key", "secret", "token")):
        return True
    return any(_matches_pattern(path, pattern) for pattern in (*config.private_paths, *_FORBIDDEN_PATH_PATTERNS))


class GitTransactionStore:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.root = config.metadata_dir / "git-transactions"

    def _path(self, transaction_id: str) -> Path:
        return self.root / f"{_safe_id(transaction_id, 'transaction_id')}.json"

    def _repo(self, repo_id: str) -> Path:
        if repo_id not in ("main", *ROLES[1:]):
            raise GitTransactionError("repo_id is invalid")
        role = "supervisor" if repo_id == "main" else repo_id
        return self.config.repo(role)

    def create_plan(
        self,
        *,
        transaction_id: str,
        workflow_id: str,
        task_id: str,
        repo_id: str,
        operations: list[str],
        explicit_files: list[str],
        commit_message: str,
        remote: str = "origin",
        remote_branch: str = "",
    ) -> dict[str, Any]:
        path = self._path(transaction_id)
        if path.exists():
            raise GitTransactionError("transaction_id already exists")
        if not operations or any(item not in OPERATIONS for item in operations):
            raise GitTransactionError("transaction operations are invalid")
        ordered = [item for item in OPERATIONS if item in operations]
        if ordered != operations or len(set(operations)) != len(operations):
            raise GitTransactionError("transaction operations must be an ordered subset")
        if "commit" in operations and "add" not in operations:
            raise GitTransactionError("commit requires add in the same transaction")
        if "push" in operations and "commit" not in operations:
            raise GitTransactionError("push requires commit in the same transaction")
        if not explicit_files:
            raise GitTransactionError("explicit file list is required")
        files = [_relative_file(item) for item in explicit_files]
        if len(files) != len(set(files)):
            raise GitTransactionError("explicit files must be unique")
        if any(_sensitive(self.config, item) for item in files):
            raise GitTransactionError("sensitive or private runtime files are forbidden")
        repo = self._repo(repo_id)
        branch = git(repo, "branch", "--show-current").stdout.strip()
        if not branch:
            raise GitTransactionError("detached HEAD is not supported")
        remote_branch = _safe_ref(remote_branch or branch, "remote branch")
        _safe_ref(remote, "remote")
        if "push" in operations and remote_branch in self.config.protected_branches:
            raise GitTransactionError("protected or integration branch push is forbidden")
        status_paths, status_raw = _status_paths(repo)
        if set(status_paths) != set(files):
            raise GitTransactionError("working tree contains changes outside the explicit file set")
        staged = git(repo, "diff", "--cached", "--name-only").stdout.splitlines()
        if staged:
            raise GitTransactionError("pre-existing staged changes are not supported")
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        remote_head_result = git(repo, "ls-remote", "--heads", remote, f"refs/heads/{remote_branch}", check=False)
        if remote_head_result.returncode:
            raise GitTransactionError("remote ref could not be inspected")
        remote_head = remote_head_result.stdout.split()[0] if remote_head_result.stdout.strip() else ""
        expected_ff = True
        if remote_head:
            expected_ff = git(repo, "merge-base", "--is-ancestor", remote_head, head, check=False).returncode == 0
        if "push" in operations and not expected_ff:
            raise GitTransactionError("planned push is not fast-forward")
        plan = {
            "transaction_id": _safe_id(transaction_id, "transaction_id"),
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "task_id": _safe_id(task_id, "task_id"),
            "repo_id": repo_id,
            "repo_path": "main" if repo_id == "main" else repo_id,
            "branch": branch,
            "base_head_sha": head,
            "operations": operations,
            "explicit_files": files,
            "file_fingerprints": _fingerprints(repo, files),
            "commit_message": commit_message,
            "remote": remote,
            "remote_branch": remote_branch,
            "remote_head_sha": remote_head,
            "expected_fast_forward": expected_ff,
            "protected_branch_check": "PASS",
            "sensitive_file_check": "PASS",
            "status_summary": status_raw,
            "diff_summary": git(repo, "diff", "--stat", "--", *files).stdout,
            "created_at": _now(),
        }
        payload = {
            "plan": plan,
            "plan_hash": _hash(plan),
            "approval": None,
            "partial_state": "NOT_STARTED",
            "consumed": False,
            "commit_sha": "",
            "remote_ref": "",
            "last_error": "",
        }
        _atomic(path, payload)
        return payload

    def approve(self, transaction_id: str, approval_summary: str) -> dict[str, Any]:
        if os.environ.get("ROLE_CLI_WORKFLOW_ROLE") != "supervisor":
            raise GitTransactionError("only Supervisor may approve a Git transaction")
        path = self._path(transaction_id)
        payload = _read(path)
        if payload.get("approval") or payload.get("consumed"):
            raise GitTransactionError("transaction is already approved or consumed")
        plan = payload.get("plan")
        if not isinstance(plan, dict) or payload.get("plan_hash") != _hash(plan):
            raise GitTransactionError("transaction plan integrity check failed")
        payload["approval"] = {
            "approved_operations": list(plan["operations"]),
            "approved_transaction_hash": payload["plan_hash"],
            "user_approval_summary": approval_summary,
            "approved_at": _now(),
            "consumed": False,
        }
        _atomic(path, payload)
        WorkflowRefinementStore(self.config).increment_metric(str(plan["workflow_id"]), "approved_git_transaction_count")
        return payload

    def _invalidate(self, path: Path, payload: dict[str, Any], reason: str) -> None:
        payload["partial_state"] = "INVALIDATED"
        payload["last_error"] = reason
        _atomic(path, payload)
        plan = payload.get("plan", {})
        if isinstance(plan, dict) and plan.get("workflow_id"):
            WorkflowRefinementStore(self.config).increment_metric(str(plan["workflow_id"]), "invalidated_git_transaction_count")

    def _validate_approved(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan = payload.get("plan")
        approval = payload.get("approval")
        if not isinstance(plan, dict) or not isinstance(approval, dict):
            raise GitTransactionError("transaction is not approved")
        if payload.get("plan_hash") != _hash(plan):
            raise GitTransactionError("transaction plan integrity check failed")
        if approval.get("approved_transaction_hash") != payload["plan_hash"]:
            raise GitTransactionError("transaction approval does not match the plan")
        if approval.get("approved_operations") != plan.get("operations"):
            raise GitTransactionError("approved operations do not match the plan")
        if payload.get("consumed"):
            raise GitTransactionError("transaction approval has already been consumed")
        return plan

    def execute(self, transaction_id: str) -> dict[str, Any]:
        if os.environ.get("ROLE_CLI_WORKFLOW_ROLE") != "supervisor":
            raise GitTransactionError("only Supervisor may execute a Git transaction")
        path = self._path(transaction_id)
        payload = _read(path)
        try:
            plan = self._validate_approved(payload)
        except GitTransactionError as exc:
            if isinstance(payload.get("approval"), dict) and not payload.get("consumed"):
                self._invalidate(path, payload, str(exc))
            raise
        state = str(payload.get("partial_state"))
        if state == "INVALIDATED":
            raise GitTransactionError("transaction is invalidated")
        repo = self._repo(str(plan["repo_id"]))
        branch = git(repo, "branch", "--show-current").stdout.strip()
        if branch != plan["branch"]:
            self._invalidate(path, payload, "branch changed")
            raise GitTransactionError("transaction invalidated: branch changed")
        operations = list(plan["operations"])
        files = list(plan["explicit_files"])
        if state in {"NOT_STARTED", "ADD_FAILED", "COMMIT_FAILED", "ADD_COMPLETED"}:
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            if head != plan["base_head_sha"]:
                self._invalidate(path, payload, "HEAD changed")
                raise GitTransactionError("transaction invalidated: HEAD changed")
        if state in {"NOT_STARTED", "ADD_FAILED"}:
            status_paths, _ = _status_paths(repo)
            if set(status_paths) != set(files) or _fingerprints(repo, files) != plan["file_fingerprints"]:
                self._invalidate(path, payload, "explicit file set or content changed")
                raise GitTransactionError("transaction invalidated: explicit file scope changed")
            try:
                result = git(repo, "add", "--", *files, check=False)
                if result.returncode:
                    raise GitTransactionError("git add failed")
                payload["partial_state"] = "ADD_COMPLETED"
                _atomic(path, payload)
            except Exception as exc:
                payload["partial_state"], payload["last_error"] = "ADD_FAILED", str(exc)
                _atomic(path, payload)
                raise GitTransactionError("Git transaction stopped after add failure") from exc
            state = "ADD_COMPLETED"
        if "commit" in operations and state in {"ADD_COMPLETED", "COMMIT_FAILED"}:
            staged = sorted(git(repo, "diff", "--cached", "--name-only").stdout.splitlines())
            if staged != sorted(files):
                self._invalidate(path, payload, "staged file set changed")
                raise GitTransactionError("transaction invalidated: staged file set changed")
            check = git(repo, "diff", "--cached", "--check", check=False)
            if check.returncode:
                payload["partial_state"], payload["last_error"] = "COMMIT_FAILED", "staged diff check failed"
                _atomic(path, payload)
                raise GitTransactionError("Git transaction stopped after staged diff check failure")
            payload["staged_diff_summary"] = git(repo, "diff", "--cached", "--stat").stdout
            try:
                result = git(repo, "commit", "-m", str(plan["commit_message"]), check=False)
                if result.returncode:
                    raise GitTransactionError("git commit failed")
                payload["commit_sha"] = git(repo, "rev-parse", "HEAD").stdout.strip()
                payload["partial_state"] = "COMMIT_COMPLETED"
                _atomic(path, payload)
            except Exception as exc:
                payload["partial_state"], payload["last_error"] = "COMMIT_FAILED", str(exc)
                _atomic(path, payload)
                raise GitTransactionError("Git transaction stopped after commit failure") from exc
            state = "COMMIT_COMPLETED"
        if "push" in operations and state in {"COMMIT_COMPLETED", "PUSH_FAILED"}:
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            if head != payload.get("commit_sha"):
                self._invalidate(path, payload, "committed HEAD changed before push")
                raise GitTransactionError("transaction invalidated: committed HEAD changed")
            remote_result = git(repo, "ls-remote", "--heads", str(plan["remote"]), f"refs/heads/{plan['remote_branch']}", check=False)
            if remote_result.returncode:
                payload["partial_state"], payload["last_error"] = "PUSH_FAILED", "remote ref could not be inspected"
                _atomic(path, payload)
                raise GitTransactionError("Git transaction stopped before push")
            remote_head = remote_result.stdout.split()[0] if remote_result.stdout.strip() else ""
            if remote_head != plan["remote_head_sha"]:
                self._invalidate(path, payload, "remote ref changed")
                raise GitTransactionError("transaction invalidated: remote ref changed")
            if remote_head and git(repo, "merge-base", "--is-ancestor", remote_head, head, check=False).returncode:
                self._invalidate(path, payload, "push is not fast-forward")
                raise GitTransactionError("transaction invalidated: push is not fast-forward")
            result = git(repo, "push", str(plan["remote"]), f"HEAD:refs/heads/{plan['remote_branch']}", check=False)
            if result.returncode:
                payload["partial_state"], payload["last_error"] = "PUSH_FAILED", "git push failed"
                _atomic(path, payload)
                raise GitTransactionError("Git transaction stopped after push failure")
            payload["partial_state"] = "PUSH_COMPLETED"
            payload["remote_ref"] = f"{plan['remote']}/{plan['remote_branch']}"
            state = "PUSH_COMPLETED"
        terminal = (
            (operations[-1] == "add" and state == "ADD_COMPLETED")
            or (operations[-1] == "commit" and state == "COMMIT_COMPLETED")
            or (operations[-1] == "push" and state == "PUSH_COMPLETED")
        )
        if terminal:
            payload["consumed"] = True
            approval = payload.get("approval")
            if isinstance(approval, dict):
                approval["consumed"] = True
                approval["consumed_at"] = _now()
            payload["last_error"] = ""
            _atomic(path, payload)
        return payload

    def show(self, transaction_id: str) -> dict[str, Any]:
        return _read(self._path(transaction_id))
