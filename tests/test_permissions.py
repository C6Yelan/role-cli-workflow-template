from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.config import ROLES, load_project

pytestmark = pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="Codex execpolicy binary is unavailable",
)


def decision(repo: Path, command: list[str]) -> str:
    codex = shutil.which("codex")
    assert codex
    rules = sorted((repo / ".codex/rules").glob("*.rules"))
    argv = [codex, "execpolicy", "check"]
    for rule in rules:
        argv.extend(["--rules", str(rule)])
    result = subprocess.run([*argv, "--", *command], text=True, capture_output=True, shell=False, check=True)
    return str(json.loads(result.stdout)["decision"])


def test_sandbox_and_git_matrix(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    config = load_project(project_root)
    expected = {
        "supervisor": ("workspace-write", "on-request"),
        "explorer": ("read-only", "never"),
        "implementer": ("workspace-write", "never"),
        "evaluator": ("workspace-write", "never"),
        "reviewer": ("read-only", "never"),
        "doc-curator": ("workspace-write", "never"),
    }
    for role, values in expected.items():
        text = (config.repo(role) / ".codex/config.toml").read_text()
        assert f'sandbox_mode = "{values[0]}"' in text
        assert f'approval_policy = "{values[1]}"' in text
    supervisor = config.repo("supervisor")
    assert decision(supervisor, ["git", "status"]) == "allow"
    for subcommand in ("add", "commit", "merge", "push"):
        assert decision(supervisor, ["git", subcommand]) == "prompt"
    assert decision(supervisor, ["role-cli-workflow", "git", "execute", str(project_root), "tx-1"]) == "allow"
    for role in ("implementer", "doc-curator"):
        repo = config.repo(role)
        for subcommand in ("add", "commit", "push"):
            assert decision(repo, ["git", subcommand]) == "allow"
        assert decision(repo, ["git", "merge"]) == "forbidden"
        assert decision(repo, ["git", "push", "origin", "trunk"]) == "forbidden"
        assert decision(repo, ["role-cli-workflow", "git", "execute", str(project_root), "tx-1"]) == "forbidden"
    for role in ("explorer", "evaluator", "reviewer"):
        repo = config.repo(role)
        assert decision(repo, ["git", "fetch"]) == "allow"
        assert decision(repo, ["git", "pull", "--ff-only"]) == "allow"
        for subcommand in ("add", "commit", "push", "merge"):
            assert decision(repo, ["git", subcommand]) == "forbidden"
        assert decision(repo, ["role-cli-workflow", "git", "execute", str(project_root), "tx-1"]) == "forbidden"
    for role in ROLES:
        repo = config.repo(role)
        assert decision(repo, ["git", "reset", "--hard"]) == "forbidden"
        assert decision(repo, ["git", "push", "--force"]) == "forbidden"
        assert decision(repo, ["bash", "-lc", "git status"]) == "forbidden"
