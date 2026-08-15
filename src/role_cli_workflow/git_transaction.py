"""Compact exact approval for protected-branch integration."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .candidate import CandidateError, validate_commit_range
from .config import ProjectConfig
from .project import git

INTEGRATION_OPERATIONS = ("merge", "push")
MERGE_METHODS = ("ff-only", "no-ff")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")


class GitTransactionError(RuntimeError):
    pass


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
        raise GitTransactionError("integration transaction is unavailable") from exc
    if not isinstance(payload, dict):
        raise GitTransactionError("integration transaction is invalid")
    return payload


class GitTransactionStore:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config

    def _path(self, transaction_id: str) -> Path:
        return self.config.runtime_root / "git-transactions-v2" / f"{_safe_id(transaction_id, 'transaction_id')}.json"

    def _repo(self) -> Path:
        return self.config.repo("supervisor")

    def _require_supervisor(self) -> None:
        if os.environ.get("ROLE_CLI_WORKFLOW_ROLE") != "supervisor":
            raise GitTransactionError("only Supervisor may approve or execute integration")

    def _remote_url_hash(self, repo: Path, remote: str) -> str:
        result = git(repo, "remote", "get-url", remote, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            raise GitTransactionError("configured remote is unavailable")
        return hashlib.sha256(result.stdout.strip().encode("utf-8")).hexdigest()

    def _remote_sha(self, repo: Path, remote: str, destination_ref: str) -> str:
        result = git(repo, "ls-remote", "--refs", remote, destination_ref, check=False)
        if result.returncode != 0:
            raise GitTransactionError("remote destination could not be verified")
        line = result.stdout.strip()
        return line.split()[0] if line else ""

    def create_integration_plan(
        self,
        *,
        transaction_id: str,
        workflow_id: str,
        task_id: str,
        operations: list[str],
        source_branch: str,
        target_branch: str,
        merge_method: str,
        remote: str = "origin",
        destination_ref: str,
    ) -> dict[str, Any]:
        path = self._path(transaction_id)
        if path.exists():
            raise GitTransactionError("transaction_id already exists")
        _safe_id(workflow_id, "workflow_id"); _safe_id(task_id, "task_id")
        source = _safe_ref(source_branch, "source branch")
        target = _safe_ref(target_branch, "target branch")
        remote = _safe_ref(remote, "remote")
        destination = _safe_ref(destination_ref, "destination ref")
        if operations not in (["merge"], ["merge", "push"]):
            raise GitTransactionError("integration operations must be merge or merge then push")
        if merge_method not in MERGE_METHODS:
            raise GitTransactionError("merge method is invalid")
        if target not in self.config.protected_branches:
            raise GitTransactionError("integration target must be a configured protected branch")
        if target == self.config.base_branch:
            if not fnmatch.fnmatchcase(source, self.config.feature_branch_pattern):
                raise GitTransactionError("base integration source must match feature_branch_pattern")
        elif target == "main":
            if source != self.config.base_branch:
                raise GitTransactionError("main integration source must be the configured base branch")
        else:
            raise GitTransactionError("protected integration route is not authorized")
        if destination != f"refs/heads/{target}":
            raise GitTransactionError("destination ref must exactly match the target branch")
        repo = self._repo()
        if git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout.strip():
            raise GitTransactionError("integration worktree must be clean")
        current = git(repo, "branch", "--show-current").stdout.strip()
        if current != target:
            raise GitTransactionError("integration worktree must be on the exact target branch")
        source_sha = git(repo, "rev-parse", "--verify", f"refs/heads/{source}^{{commit}}").stdout.strip()
        target_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        try:
            validate_commit_range(self.config, repo=repo, base_sha=target_sha, candidate_sha=source_sha)
        except CandidateError as exc:
            raise GitTransactionError(str(exc)) from exc
        if merge_method == "ff-only" and git(repo, "merge-base", "--is-ancestor", target_sha, source_sha, check=False).returncode != 0:
            raise GitTransactionError("ff-only integration is not a fast-forward")
        remote_start = self._remote_sha(repo, remote, destination) if "push" in operations else ""
        if "push" in operations and remote_start != target_sha:
            raise GitTransactionError("remote target does not match the local target starting SHA")
        plan = {
            "source_branch": source,
            "source_sha": source_sha,
            "target_branch": target,
            "target_starting_sha": target_sha,
            "remote": remote,
            "remote_url_hash": self._remote_url_hash(repo, remote),
            "destination_ref": destination,
            "remote_starting_sha": remote_start,
            "merge_method": merge_method,
            "operations": operations,
            "force_allowed": False,
        }
        payload = {
            "schema_version": 2,
            "transaction_id": transaction_id,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "status": "PLANNED",
            "plan": plan,
            "plan_hash": _hash(plan),
            "approval": None,
            "merge_sha": "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        _atomic(path, payload)
        return payload

    def approve(self, transaction_id: str, approval_summary: str) -> dict[str, Any]:
        self._require_supervisor()
        path = self._path(transaction_id)
        payload = _read(path)
        if payload.get("status") != "PLANNED" or payload.get("plan_hash") != _hash(payload.get("plan")):
            raise GitTransactionError("integration plan is stale or invalid")
        if transaction_id not in approval_summary:
            raise GitTransactionError("approval summary must name the transaction_id")
        payload["approval"] = {"summary": approval_summary, "approved_at": _now(), "plan_hash": payload["plan_hash"]}
        payload["status"] = "APPROVED"
        payload["updated_at"] = _now()
        _atomic(path, payload)
        return payload

    def _invalidate(self, path: Path, payload: dict[str, Any], reason: str) -> None:
        payload["status"] = "INVALIDATED"
        payload["failure_reason"] = reason
        payload["updated_at"] = _now()
        _atomic(path, payload)

    def _validate_exact_state(self, payload: dict[str, Any], *, after_merge: bool = False) -> None:
        plan = payload["plan"]
        repo = self._repo()
        if payload.get("plan_hash") != _hash(plan) or payload.get("approval", {}).get("plan_hash") != payload.get("plan_hash"):
            raise GitTransactionError("approved integration plan integrity failed")
        if self._remote_url_hash(repo, plan["remote"]) != plan["remote_url_hash"]:
            raise GitTransactionError("remote changed after approval")
        if git(repo, "branch", "--show-current").stdout.strip() != plan["target_branch"]:
            raise GitTransactionError("target branch changed after approval")
        if git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout.strip():
            raise GitTransactionError("integration worktree changed after approval")
        expected_head = payload.get("merge_sha") if after_merge else plan["target_starting_sha"]
        if git(repo, "rev-parse", "HEAD").stdout.strip() != expected_head:
            raise GitTransactionError("target SHA changed after approval")
        if git(repo, "rev-parse", f"refs/heads/{plan['source_branch']}").stdout.strip() != plan["source_sha"]:
            raise GitTransactionError("source SHA changed after approval")
        if "push" in plan["operations"]:
            actual_remote = self._remote_sha(repo, plan["remote"], plan["destination_ref"])
            allowed = {plan["remote_starting_sha"]}
            if after_merge:
                allowed.add(str(payload.get("merge_sha", "")))
            if actual_remote not in allowed:
                raise GitTransactionError("remote destination changed after approval")

    def execute(self, transaction_id: str) -> dict[str, Any]:
        self._require_supervisor()
        path = self._path(transaction_id)
        payload = _read(path)
        if payload.get("status") not in {"APPROVED", "PUSH_PENDING"}:
            raise GitTransactionError("integration transaction is not executable")
        plan = payload["plan"]
        repo = self._repo()
        try:
            if payload["status"] == "APPROVED":
                self._validate_exact_state(payload)
                args = ["merge", "--ff-only", plan["source_sha"]] if plan["merge_method"] == "ff-only" else ["merge", "--no-ff", "--no-edit", plan["source_sha"]]
                result = git(repo, *args, check=False)
                if result.returncode != 0:
                    self._invalidate(path, payload, "merge failed; no automatic reset or cleanup was attempted")
                    raise GitTransactionError("integration merge failed")
                payload["merge_sha"] = git(repo, "rev-parse", "HEAD").stdout.strip()
                payload["status"] = "PUSH_PENDING" if "push" in plan["operations"] else "COMPLETED"
                payload["updated_at"] = _now()
                _atomic(path, payload)
            if payload["status"] == "PUSH_PENDING":
                self._validate_exact_state(payload, after_merge=True)
                remote_sha = self._remote_sha(repo, plan["remote"], plan["destination_ref"])
                if remote_sha != payload["merge_sha"]:
                    pushed = git(repo, "push", "--porcelain", plan["remote"], f"{payload['merge_sha']}:{plan['destination_ref']}", check=False)
                    if pushed.returncode != 0:
                        message = (pushed.stderr or pushed.stdout).lower()
                        if "non-fast-forward" in message or "fetch first" in message:
                            self._invalidate(path, payload, "non-fast-forward publication rejected")
                            raise GitTransactionError("non-fast-forward publication rejected")
                        _atomic(path, payload)
                        raise GitTransactionError("integration push failed; exact push may be retried")
                if self._remote_sha(repo, plan["remote"], plan["destination_ref"]) != payload["merge_sha"]:
                    raise GitTransactionError("integration push could not be verified")
                payload["status"] = "COMPLETED"
                payload["updated_at"] = _now()
                _atomic(path, payload)
        except GitTransactionError as exc:
            if payload.get("status") not in {"INVALIDATED", "PUSH_PENDING"}:
                self._invalidate(path, payload, str(exc))
            raise
        return payload

    def show(self, transaction_id: str) -> dict[str, Any]:
        return _read(self._path(transaction_id))
