from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path

import pytest

from codex_role_workflow.bootstrap import init_project
from codex_role_workflow.config import load_project
from codex_role_workflow.git_transaction import GitTransactionError, GitTransactionStore


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, shell=False, check=True)


def setup_transaction_repo(project_root: Path, tmp_path: Path) -> tuple[GitTransactionStore, Path]:
    init_project(project_root, assume_yes=True)
    repo = project_root / "main"
    bare = tmp_path / "remote.git"
    run("git", "init", "--bare", str(bare), cwd=tmp_path)
    run("git", "remote", "add", "origin", str(bare), cwd=repo)
    run("git", "push", "-u", "origin", "trunk", cwd=repo)
    run("git", "switch", "-c", "feature/transaction", cwd=repo)
    return GitTransactionStore(load_project(project_root)), repo


def make_change(repo: Path, text: str = "changed\n") -> None:
    (repo / "README.md").write_text(text, encoding="utf-8")


def plan(store: GitTransactionStore, operations: list[str]) -> dict[str, object]:
    return store.create_plan(
        transaction_id="tx-1", workflow_id="wf-1", task_id="task-1",
        repo_id="main", operations=operations, explicit_files=["README.md"],
        commit_message="Update README", remote="origin", remote_branch="feature/transaction",
    )


def test_single_approval_add_commit_push_and_consumption(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    before = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    created = plan(store, ["add", "commit", "push"])
    assert run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip() == before
    assert created["partial_state"] == "NOT_STARTED"
    monkeypatch.setenv("CODEX_ROLE_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved exact shown add commit push transaction")
    result = store.execute("tx-1")
    assert result["partial_state"] == "PUSH_COMPLETED"
    assert result["consumed"] is True
    assert result["commit_sha"] != before
    assert run("git", "ls-remote", "origin", "refs/heads/feature/transaction", cwd=repo).stdout.startswith(result["commit_sha"])
    with pytest.raises(GitTransactionError, match="consumed"):
        store.execute("tx-1")


def test_add_commit_subset_does_not_push(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    plan(store, ["add", "commit"])
    monkeypatch.setenv("CODEX_ROLE_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved add and commit only")
    result = store.execute("tx-1")
    assert result["partial_state"] == "COMMIT_COMPLETED"
    assert not run("git", "ls-remote", "origin", "refs/heads/feature/transaction", cwd=repo).stdout


def test_transaction_rejects_merge_protected_secret_and_worker(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    with pytest.raises(GitTransactionError, match="operations"):
        store.create_plan(
            transaction_id="bad-merge", workflow_id="wf-1", task_id="task-1", repo_id="main",
            operations=["merge"], explicit_files=["README.md"], commit_message="x",
        )
    with pytest.raises(GitTransactionError, match="protected"):
        store.create_plan(
            transaction_id="bad-protected", workflow_id="wf-1", task_id="task-1", repo_id="main",
            operations=["add", "commit", "push"], explicit_files=["README.md"],
            commit_message="x", remote_branch="trunk",
        )
    (repo / ".env").write_text("TOKEN=fake\n")
    with pytest.raises(GitTransactionError, match="sensitive"):
        store.create_plan(
            transaction_id="bad-secret", workflow_id="wf-1", task_id="task-1", repo_id="main",
            operations=["add"], explicit_files=[".env"], commit_message="x",
        )
    monkeypatch.setenv("CODEX_ROLE_WORKFLOW_ROLE", "implementer")
    with pytest.raises(GitTransactionError, match="Supervisor"):
        store.approve("missing", "no")


@pytest.mark.parametrize("mutation", ["branch", "head", "files"])
def test_scope_change_invalidates_transaction(project_root: Path, tmp_path: Path, monkeypatch, mutation: str) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    plan(store, ["add", "commit"])
    monkeypatch.setenv("CODEX_ROLE_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved")
    if mutation == "branch":
        run("git", "switch", "-c", "feature/other", cwd=repo)
    elif mutation == "head":
        run("git", "add", "README.md", cwd=repo)
        run("git", "commit", "-m", "unexpected", cwd=repo)
    else:
        make_change(repo, "changed again\n")
    with pytest.raises(GitTransactionError, match="invalidated"):
        store.execute("tx-1")
    assert store.show("tx-1")["partial_state"] == "INVALIDATED"


def test_push_failure_retries_only_push(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    plan(store, ["add", "commit", "push"])
    monkeypatch.setenv("CODEX_ROLE_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved")
    import codex_role_workflow.git_transaction as module
    original = module.git
    attempts = {"push": 0}
    def flaky(repo_path: Path, *arguments: str, check: bool = True):
        if arguments and arguments[0] == "push" and attempts["push"] == 0:
            attempts["push"] += 1
            return subprocess.CompletedProcess(["git"], 1, "", "temporary failure")
        return original(repo_path, *arguments, check=check)
    monkeypatch.setattr(module, "git", flaky)
    with pytest.raises(GitTransactionError, match="push failure"):
        store.execute("tx-1")
    failed = store.show("tx-1")
    commit_sha = failed["commit_sha"]
    assert failed["partial_state"] == "PUSH_FAILED"
    result = store.execute("tx-1")
    assert result["commit_sha"] == commit_sha
    assert result["partial_state"] == "PUSH_COMPLETED"


@pytest.mark.parametrize("failure,state", [("add", "ADD_FAILED"), ("commit", "COMMIT_FAILED")])
def test_add_and_commit_failure_save_partial_state(project_root: Path, tmp_path: Path, monkeypatch, failure: str, state: str) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    plan(store, ["add", "commit"])
    monkeypatch.setenv("CODEX_ROLE_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved")
    import codex_role_workflow.git_transaction as module
    original = module.git
    def failing(repo_path: Path, *arguments: str, check: bool = True):
        if arguments and arguments[0] == failure:
            return subprocess.CompletedProcess(["git"], 1, "", "synthetic failure")
        return original(repo_path, *arguments, check=check)
    monkeypatch.setattr(module, "git", failing)
    with pytest.raises(GitTransactionError):
        store.execute("tx-1")
    assert store.show("tx-1")["partial_state"] == state


def test_approved_plan_tampering_invalidates(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    plan(store, ["add", "commit"])
    monkeypatch.setenv("CODEX_ROLE_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved")
    path = store._path("tx-1")
    payload = json.loads(path.read_text())
    payload["plan"]["commit_message"] = "different message"
    path.write_text(json.dumps(payload))
    with pytest.raises(GitTransactionError, match="integrity"):
        store.execute("tx-1")
    assert store.show("tx-1")["partial_state"] == "INVALIDATED"


def test_single_approval_records_one_transaction_not_three_interruptions(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    plan(store, ["add", "commit"])
    monkeypatch.setenv("CODEX_ROLE_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "one approval for the displayed operation subset")
    metrics = json.loads((project_root / "shared_workspace/workflow/tasks/wf-1/data/retrospective-metrics.json").read_text())
    assert metrics["approved_git_transaction_count"] == 1
    assert metrics["git_approval_interruption_count"] == 0


def test_executor_uses_fixed_argv_and_shell_false() -> None:
    source = Path(__file__).parents[1] / "src/codex_role_workflow/git_transaction.py"
    text = source.read_text(encoding="utf-8")
    assert "shell=True" not in text
    assert "subprocess" not in text
    assert "git(repo, \"add\", \"--\", *files" in text
