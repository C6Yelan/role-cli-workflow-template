from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.config import load_project
from role_cli_workflow.git_transaction import GitTransactionError, GitTransactionStore


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, text=True, capture_output=True, shell=False, check=True)


def setup_integration(project_root: Path, tmp_path: Path):
    main = project_root / "main"
    run("git", "switch", "-c", "feature/integration", cwd=main)
    (main / "feature.py").write_text("value = 1\n", encoding="utf-8")
    run("git", "add", "feature.py", cwd=main); run("git", "commit", "-m", "feature", cwd=main)
    source_sha = run("git", "rev-parse", "HEAD", cwd=main).stdout.strip()
    run("git", "switch", "dev", cwd=main)
    init_project(project_root, assume_yes=True)
    config_path = project_root / ".role-cli-workflow/project.toml"
    text = config_path.read_text(encoding="utf-8").replace('protected_branches = ["dev", "main"]', 'protected_branches = ["dev", "main"]')
    config_path.write_text(text, encoding="utf-8")
    remote = tmp_path / "remote.git"
    run("git", "init", "--bare", str(remote), cwd=tmp_path)
    run("git", "remote", "add", "origin", str(remote), cwd=main)
    run("git", "push", "origin", "dev:refs/heads/dev", cwd=main)
    return load_project(project_root), source_sha


def test_bootstrap_unified_policy_and_no_obsolete_branches(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    config = load_project(project_root)
    assert config.base_branch == "dev"
    assert config.feature_branch_pattern == "feature/*"
    assert config.protected_branches == ("dev", "main")
    assert config.integration_mode == "approved_transaction"
    text = (project_root / ".role-cli-workflow/project.toml").read_text(encoding="utf-8")
    assert "be-dev" not in text and "fe-dev" not in text and "be/feature" not in text


def test_exact_integration_drift_invalidates_approval(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    config, _ = setup_integration(project_root, tmp_path)
    store = GitTransactionStore(config)
    plan = store.create_integration_plan(
        transaction_id="tx-dev", workflow_id="wf-1", task_id="task-1",
        operations=["merge", "push"], source_branch="feature/integration", target_branch="dev",
        merge_method="ff-only", remote="origin", destination_ref="refs/heads/dev",
    )
    assert plan["plan"]["force_allowed"] is False
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-dev", "approve exact tx-dev")
    run("git", "branch", "-f", "feature/integration", "dev", cwd=config.repo("supervisor"))
    with pytest.raises(GitTransactionError, match="source SHA changed"):
        store.execute("tx-dev")
    assert store.show("tx-dev")["status"] == "INVALIDATED"


def test_worker_cannot_approve_protected_integration(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    config, _ = setup_integration(project_root, tmp_path)
    store = GitTransactionStore(config)
    store.create_integration_plan(
        transaction_id="tx-worker", workflow_id="wf-1", task_id="task-1", operations=["merge"],
        source_branch="feature/integration", target_branch="dev", merge_method="ff-only",
        remote="origin", destination_ref="refs/heads/dev",
    )
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "implementer")
    with pytest.raises(GitTransactionError, match="Supervisor"):
        store.approve("tx-worker", "approve tx-worker")


def test_destructive_and_force_commands_remain_denied_in_generated_rules(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    rules = (project_root / "implementer/.codex/rules/workflow-common.rules").read_text(encoding="utf-8")
    assert '["git", "reset", "--hard"]' in rules
    assert '["git", "clean"' in rules
    assert '"--force"' in rules
