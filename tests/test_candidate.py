from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.candidate import CandidateError, prepare_validation_worktree, validate_candidate
from role_cli_workflow.config import load_project


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, text=True, capture_output=True, shell=False, check=True)


def make_candidate(project_root: Path, name: str = "source.py", content: str = "token = get_token()\n"):
    config = load_project(project_root)
    repo = config.repo("implementer")
    base = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    run("git", "switch", "-c", "feature/candidate", cwd=repo)
    (repo / name).write_text(content, encoding="utf-8")
    run("git", "add", name, cwd=repo)
    run("git", "commit", "-m", "candidate", cwd=repo)
    sha = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    return config, repo, base, sha


def test_exact_sha_handoff_to_both_validation_worktrees(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    config, repo, base, sha = make_candidate(project_root)
    candidate = validate_candidate(config, repo=repo, expected_role="implementer", base_sha=base, candidate_sha=sha)
    assert candidate.sha == sha and candidate.branch == "feature/candidate"
    assert prepare_validation_worktree(config, role="evaluator", candidate_sha=sha) == sha
    assert prepare_validation_worktree(config, role="reviewer", candidate_sha=sha) == sha


def test_dirty_validation_worktree_is_preserved(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    config, _, _, sha = make_candidate(project_root)
    dirty = config.repo("evaluator") / "local.txt"
    dirty.write_text("keep me\n", encoding="utf-8")
    with pytest.raises(CandidateError, match="dirty"):
        prepare_validation_worktree(config, role="evaluator", candidate_sha=sha)
    assert dirty.read_text(encoding="utf-8") == "keep me\n"


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        (".env", "API_KEY=not-a-real-value-but-long\n", "private path"),
        ("server.pem", "-----BEGIN PRIVATE KEY-----\nfixture\n", "private path"),
        ("config.py", "password = '0123456789abcdef0123'\n", "secret material"),
    ],
)
def test_candidate_rejects_private_and_high_confidence_secret_content(
    project_root: Path, name: str, content: str, message: str
) -> None:
    init_project(project_root, assume_yes=True)
    config, repo, base, sha = make_candidate(project_root, name, content)
    with pytest.raises(CandidateError, match=message):
        validate_candidate(config, repo=repo, expected_role="implementer", base_sha=base, candidate_sha=sha)


def test_secret_like_identifiers_do_not_false_positive(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    config, repo, base, sha = make_candidate(project_root, content="def refresh_token(token):\n    return token\n")
    assert validate_candidate(config, repo=repo, expected_role="implementer", base_sha=base, candidate_sha=sha).sha == sha
