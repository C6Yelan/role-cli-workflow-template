"""Stateless validation and handoff for a Git commit candidate."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import ProjectConfig
from .project import git

SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
KEY_BLOCK_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
CERT_BLOCK_RE = re.compile(r"-----BEGIN CERTIFICATE-----")
ASSIGNMENT_SECRET_RE = re.compile(
    r"(?im)^\s*(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"]?([^\s'\"]{16,})"
)
CONNECTION_RE = re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+")
BUILTIN_PRIVATE = (
    ".env", ".env.*", "docs_local", "docs_local/*", "*.pem", "*.key",
    "*.p12", "*.pfx", "*.crt", "*.cer",
)


class CandidateError(RuntimeError):
    """The commit is stale, unsafe, or checked out in the wrong role worktree."""


@dataclass(frozen=True)
class Candidate:
    sha: str
    base_sha: str
    branch: str
    changed_paths: tuple[str, ...]


def _sha(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if not SHA_RE.fullmatch(normalized):
        raise CandidateError(f"{label} must be a full Git commit SHA")
    return normalized


def _matches(path: str, pattern: str) -> bool:
    normalized = PurePosixPath(path).as_posix()
    return fnmatch.fnmatchcase(normalized, pattern) or fnmatch.fnmatchcase(normalized + "/", pattern)


def is_private_path(path: str, configured: tuple[str, ...]) -> bool:
    return any(_matches(path, pattern.rstrip("/")) for pattern in (*BUILTIN_PRIVATE, *configured))


def _clean(repo: Path) -> bool:
    return not git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout.strip()


def _changed_paths(repo: Path, base_sha: str, candidate_sha: str) -> tuple[str, ...]:
    output = git(repo, "diff", "--name-only", "--diff-filter=ACMRT", f"{base_sha}..{candidate_sha}").stdout
    paths = tuple(line for line in output.splitlines() if line)
    if any(PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts for path in paths):
        raise CandidateError("candidate contains an invalid path")
    return paths


def _scan_committed_file(repo: Path, sha: str, path: str) -> None:
    blob = git(repo, "show", f"{sha}:{path}", check=False)
    if blob.returncode != 0:
        return
    text = blob.stdout
    if KEY_BLOCK_RE.search(text):
        raise CandidateError(f"candidate contains private-key material: {path}")
    if CERT_BLOCK_RE.search(text):
        raise CandidateError(f"candidate contains certificate material: {path}")
    if ASSIGNMENT_SECRET_RE.search(text) or CONNECTION_RE.search(text):
        raise CandidateError(f"candidate contains high-confidence secret material: {path}")


def validate_candidate(
    config: ProjectConfig,
    *,
    repo: Path,
    expected_role: str,
    base_sha: str,
    candidate_sha: str,
    require_clean: bool = True,
) -> Candidate:
    """Validate one exact Implementer commit without creating duplicate freeze state."""
    if expected_role not in {"implementer", "doc-curator"} or repo.resolve() != config.repo(expected_role).resolve():
        raise CandidateError("candidate must come from the assigned writer worktree")
    base = _sha(base_sha, "base_sha")
    candidate = _sha(candidate_sha, "candidate_sha")
    resolved = git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}").stdout.strip().lower()
    if resolved != candidate:
        raise CandidateError("candidate SHA does not resolve exactly")
    branch = git(repo, "branch", "--show-current").stdout.strip()
    if not fnmatch.fnmatchcase(branch, config.feature_branch_pattern):
        raise CandidateError("candidate is not on an authorized feature branch")
    head = git(repo, "rev-parse", "HEAD").stdout.strip().lower()
    if head != candidate:
        raise CandidateError("Implementer worktree HEAD does not match candidate SHA")
    if require_clean and not _clean(repo):
        raise CandidateError("Implementer worktree must be clean at candidate handoff")
    if git(repo, "merge-base", "--is-ancestor", base, candidate, check=False).returncode != 0:
        raise CandidateError("candidate does not descend from the expected base")
    paths = _changed_paths(repo, base, candidate)
    private = [path for path in paths if is_private_path(path, config.private_paths)]
    if private:
        raise CandidateError("candidate contains a configured private path: " + ", ".join(private))
    for path in paths:
        _scan_committed_file(repo, candidate, path)
    return Candidate(candidate, base, branch, paths)


def validate_commit_range(
    config: ProjectConfig, *, repo: Path, base_sha: str, candidate_sha: str
) -> tuple[str, ...]:
    """Apply publication security checks to an exact already-committed range."""
    base = _sha(base_sha, "base_sha")
    candidate = _sha(candidate_sha, "candidate_sha")
    if git(repo, "merge-base", "--is-ancestor", base, candidate, check=False).returncode != 0:
        raise CandidateError("publication candidate does not descend from the target start")
    paths = _changed_paths(repo, base, candidate)
    private = [path for path in paths if is_private_path(path, config.private_paths)]
    if private:
        raise CandidateError("publication candidate contains a configured private path: " + ", ".join(private))
    for path in paths:
        _scan_committed_file(repo, candidate, path)
    return paths


def prepare_writer_base(
    config: ProjectConfig, *, role: str, base_sha: str
) -> str:
    """Make the exact current base commit available without changing the writer worktree."""
    if role not in {"implementer", "doc-curator"}:
        raise CandidateError("only a writer may receive a candidate base")
    base = _sha(base_sha, "base_sha")
    canonical = config.repo("supervisor")
    expected = git(
        canonical, "rev-parse", "--verify", f"refs/heads/{config.base_branch}^{{commit}}"
    ).stdout.strip().lower()
    if expected != base:
        raise CandidateError("input base SHA is not the current configured base branch")
    repo = config.repo(role)
    probe = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}", check=False)
    if probe.returncode != 0:
        fetched = git(repo, "fetch", "--no-tags", str(canonical), base, check=False)
        if fetched.returncode != 0:
            raise CandidateError("writer could not receive the exact base commit")
        probe = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}", check=False)
    if probe.stdout.strip().lower() != base:
        raise CandidateError("writer base commit is unavailable")
    return base


def prepare_validation_worktree(
    config: ProjectConfig, *, role: str, candidate_sha: str
) -> str:
    """Detach an already-clean Evaluator/Reviewer worktree at an exact commit."""
    if role not in {"evaluator", "reviewer"}:
        raise CandidateError("only Evaluator or Reviewer may prepare a validation worktree")
    repo = config.repo(role)
    candidate = _sha(candidate_sha, "candidate_sha")
    if not _clean(repo):
        raise CandidateError("validation worktree is dirty; refusing destructive recovery")
    resolved = git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}").stdout.strip().lower()
    if resolved != candidate:
        raise CandidateError("candidate SHA is unavailable in validation worktree")
    switched = git(repo, "switch", "--detach", candidate, check=False)
    if switched.returncode != 0:
        raise CandidateError("candidate checkout failed without modifying dirty work")
    actual = git(repo, "rev-parse", "HEAD").stdout.strip().lower()
    if actual != candidate or not _clean(repo):
        raise CandidateError("validation worktree does not match the exact candidate SHA")
    return actual
