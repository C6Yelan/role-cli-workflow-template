from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.config import load_project
from role_cli_workflow.git_transaction import GitTransactionError, GitTransactionStore


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


def setup_integration_repo(
    project_root: Path, tmp_path: Path
) -> tuple[GitTransactionStore, Path, str]:
    init_project(project_root, assume_yes=True)
    repo = project_root / "main"
    bare = tmp_path / "remote.git"
    run("git", "init", "--bare", str(bare), cwd=tmp_path)
    run("git", "remote", "add", "origin", str(bare), cwd=repo)
    run("git", "push", "-u", "origin", "trunk", cwd=repo)
    run("git", "switch", "-c", "feature/integration", cwd=repo)
    make_change(repo, "integration change\n")
    run("git", "add", "README.md", cwd=repo)
    run("git", "commit", "-m", "integration source", cwd=repo)
    source_sha = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    run("git", "switch", "trunk", cwd=repo)
    return GitTransactionStore(load_project(project_root)), repo, source_sha


def integration_plan(
    store: GitTransactionStore,
    operations: list[str],
    *,
    merge_method: str = "ff-only",
) -> dict[str, object]:
    return store.create_integration_plan(
        transaction_id="tx-integration",
        workflow_id="wf-1",
        task_id="task-1",
        repo_id="main",
        operations=operations,
        source_branch="feature/integration",
        target_branch="trunk",
        merge_method=merge_method,
        remote="origin",
        destination_ref="refs/heads/trunk",
    )


def test_single_approval_add_commit_push_and_consumption(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    before = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    created = plan(store, ["add", "commit", "push"])
    assert run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip() == before
    assert created["partial_state"] == "NOT_STARTED"
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved exact shown tx-1 add commit push transaction")
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
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved tx-1 add and commit only")
    result = store.execute("tx-1")
    assert result["partial_state"] == "COMMIT_COMPLETED"
    assert not run("git", "ls-remote", "origin", "refs/heads/feature/transaction", cwd=repo).stdout


def test_add_only_transaction_allows_empty_commit_message(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, _ = setup_transaction_repo(project_root, tmp_path)
    make_change(project_root / "main")
    created = store.create_plan(
        transaction_id="tx-add",
        workflow_id="wf-1",
        task_id="task-1",
        repo_id="main",
        operations=["add"],
        explicit_files=["README.md"],
        commit_message="",
    )
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-add", "approved displayed tx-add plan")
    result = store.execute("tx-add")
    assert created["plan"]["commit_message"] == ""
    assert result["partial_state"] == "ADD_COMPLETED"


@pytest.mark.parametrize("commit_message", ["", "   \t"])
def test_commit_transaction_rejects_blank_commit_message_before_plan_write(
    project_root: Path, tmp_path: Path, commit_message: str
) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    before = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    with pytest.raises(GitTransactionError, match="commit message"):
        store.create_plan(
            transaction_id="tx-blank-message",
            workflow_id="wf-1",
            task_id="task-1",
            repo_id="main",
            operations=["add", "commit"],
            explicit_files=["README.md"],
            commit_message=commit_message,
        )
    assert not store._path("tx-blank-message").exists()
    assert run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip() == before
    assert run("git", "diff", "--cached", "--name-only", cwd=repo).stdout == ""


def test_transaction_rejects_protected_secret_and_worker(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
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
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "implementer")
    with pytest.raises(GitTransactionError, match="Supervisor"):
        store.approve("missing", "no")


@pytest.mark.parametrize("mutation", ["branch", "head", "files"])
def test_scope_change_invalidates_transaction(project_root: Path, tmp_path: Path, monkeypatch, mutation: str) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    plan(store, ["add", "commit"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved tx-1")
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
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved tx-1")
    import role_cli_workflow.git_transaction as module
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
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved tx-1")
    import role_cli_workflow.git_transaction as module
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
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "approved tx-1")
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
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-1", "one approval for the displayed tx-1 operation subset")
    metrics = json.loads((project_root / "shared_workspace/workflow/tasks/wf-1/data/retrospective-metrics.json").read_text())
    assert metrics["approved_git_transaction_count"] == 1
    assert metrics["git_approval_interruption_count"] == 0


def test_executor_uses_fixed_argv_and_shell_false() -> None:
    source = Path(__file__).parents[1] / "src/role_cli_workflow/git_transaction.py"
    text = source.read_text(encoding="utf-8")
    assert "shell=True" not in text
    assert "subprocess" not in text
    assert "git(repo, \"add\", \"--\", *files" in text


def test_vague_approval_does_not_authorize_transaction(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, repo = setup_transaction_repo(project_root, tmp_path)
    make_change(repo)
    plan(store, ["add", "commit"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    with pytest.raises(GitTransactionError, match="transaction_id"):
        store.approve("tx-1", "finish all remaining Git operations")
    assert store.show("tx-1")["approval"] is None


def test_single_approval_merge_only(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, repo, source_sha = setup_integration_repo(project_root, tmp_path)
    created = integration_plan(store, ["merge"])
    assert created["plan"]["force_allowed"] is False
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    result = store.execute("tx-integration")
    assert result["partial_state"] == "MERGE_COMPLETED"
    assert result["merge_sha"] == source_sha
    assert result["consumed"] is True
    remote = run(
        "git", "ls-remote", "origin", "refs/heads/trunk", cwd=repo
    ).stdout.split()[0]
    assert remote == created["plan"]["target_starting_sha"]


def test_single_approval_merge_and_resulting_push(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, repo, source_sha = setup_integration_repo(project_root, tmp_path)
    created = integration_plan(store, ["merge", "push"])
    assert "remote_url" not in created["plan"]
    assert created["plan"]["remote_url_hash"]
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration merge and push")
    result = store.execute("tx-integration")
    assert result["partial_state"] == "PUSH_COMPLETED"
    assert result["merge_sha"] == source_sha
    assert result["approval"]["approved_operations"] == ["merge", "push"]
    assert result["consumed"] is True
    remote = run(
        "git", "ls-remote", "origin", "refs/heads/trunk", cwd=repo
    ).stdout.split()[0]
    assert remote == source_sha
    metrics = json.loads(
        (
            project_root
            / "shared_workspace/workflow/tasks/wf-1/data/retrospective-metrics.json"
        ).read_text()
    )
    assert metrics["approved_git_transaction_count"] == 1


@pytest.mark.parametrize("mutation", ["source", "target", "worktree"])
def test_integration_scope_drift_invalidates(
    project_root: Path, tmp_path: Path, monkeypatch, mutation: str
) -> None:
    store, repo, _ = setup_integration_repo(project_root, tmp_path)
    integration_plan(store, ["merge", "push"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    if mutation == "source":
        run("git", "branch", "-f", "feature/integration", "trunk", cwd=repo)
    elif mutation == "target":
        run("git", "commit", "--allow-empty", "-m", "target drift", cwd=repo)
    else:
        make_change(repo, "unapproved worktree change\n")
    with pytest.raises(GitTransactionError, match="invalidated"):
        store.execute("tx-integration")
    assert store.show("tx-integration")["partial_state"] == "INVALIDATED"


def test_merge_conflict_invalidates_without_resolution(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, repo, _ = setup_integration_repo(project_root, tmp_path)
    make_change(repo, "conflicting target change\n")
    run("git", "add", "README.md", cwd=repo)
    run("git", "commit", "-m", "diverge target", cwd=repo)
    integration_plan(store, ["merge"], merge_method="no-ff")
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    with pytest.raises(GitTransactionError, match="merge conflict"):
        store.execute("tx-integration")
    result = store.show("tx-integration")
    assert result["partial_state"] == "INVALIDATED"
    assert result["failure_stage"] == "MERGE"
    assert run(
        "git", "diff", "--name-only", "--diff-filter=U", cwd=repo
    ).stdout.splitlines() == ["README.md"]


@pytest.mark.parametrize("mutation", ["remote", "destination"])
def test_integration_remote_or_destination_drift_invalidates(
    project_root: Path, tmp_path: Path, monkeypatch, mutation: str
) -> None:
    store, repo, source_sha = setup_integration_repo(project_root, tmp_path)
    integration_plan(store, ["merge", "push"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    if mutation == "remote":
        other = tmp_path / "other.git"
        run("git", "init", "--bare", str(other), cwd=tmp_path)
        run("git", "remote", "set-url", "origin", str(other), cwd=repo)
    else:
        run(
            "git",
            "push",
            "origin",
            f"{source_sha}:refs/heads/feature/integration",
            cwd=repo,
        )
        run(
            "git",
            "--git-dir",
            str(tmp_path / "remote.git"),
            "update-ref",
            "refs/heads/trunk",
            source_sha,
            cwd=tmp_path,
        )
    with pytest.raises(GitTransactionError, match="invalidated"):
        store.execute("tx-integration")
    assert store.show("tx-integration")["partial_state"] == "INVALIDATED"


def test_non_fast_forward_push_invalidates_without_force(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, _, _ = setup_integration_repo(project_root, tmp_path)
    integration_plan(store, ["merge", "push"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    import role_cli_workflow.git_transaction as module
    original = module.git

    def reject_push(repo_path: Path, *arguments: str, check: bool = True):
        if arguments and arguments[0] == "push":
            return subprocess.CompletedProcess(
                ["git"], 1, "", "rejected (non-fast-forward)"
            )
        return original(repo_path, *arguments, check=check)

    monkeypatch.setattr(module, "git", reject_push)
    with pytest.raises(GitTransactionError, match="non-fast-forward"):
        store.execute("tx-integration")
    result = store.show("tx-integration")
    assert result["partial_state"] == "INVALIDATED"
    assert result["plan"]["force_allowed"] is False


def test_integration_push_failure_resumes_without_remerging(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, _, _ = setup_integration_repo(project_root, tmp_path)
    integration_plan(store, ["merge", "push"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    import role_cli_workflow.git_transaction as module
    original = module.git
    calls = {"merge": 0, "push": 0}

    def flaky_push(repo_path: Path, *arguments: str, check: bool = True):
        if arguments and arguments[0] == "merge":
            calls["merge"] += 1
        if arguments and arguments[0] == "push":
            calls["push"] += 1
            if calls["push"] == 1:
                return subprocess.CompletedProcess(["git"], 1, "", "temporary failure")
        return original(repo_path, *arguments, check=check)

    monkeypatch.setattr(module, "git", flaky_push)
    with pytest.raises(GitTransactionError, match="push failure"):
        store.execute("tx-integration")
    failed = store.show("tx-integration")
    assert failed["partial_state"] == "PUSH_FAILED"
    merge_sha = failed["merge_sha"]
    result = store.execute("tx-integration")
    assert result["partial_state"] == "PUSH_COMPLETED"
    assert result["merge_sha"] == merge_sha
    assert calls == {"merge": 1, "push": 2}


def test_integration_push_and_probe_network_failure_remains_retryable(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, _, _ = setup_integration_repo(project_root, tmp_path)
    integration_plan(store, ["merge", "push"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    import role_cli_workflow.git_transaction as module
    original = module.git
    calls = {"merge": 0, "push": 0}
    push_failed = False

    def network_failure(repo_path: Path, *arguments: str, check: bool = True):
        nonlocal push_failed
        if arguments and arguments[0] == "merge":
            calls["merge"] += 1
        if arguments and arguments[0] == "push":
            calls["push"] += 1
            push_failed = True
            return subprocess.CompletedProcess(["git"], 1, "", "network unavailable")
        if arguments and arguments[0] == "ls-remote" and push_failed:
            return subprocess.CompletedProcess(["git"], 1, "", "network unavailable")
        return original(repo_path, *arguments, check=check)

    monkeypatch.setattr(module, "git", network_failure)
    with pytest.raises(GitTransactionError, match="push failure"):
        store.execute("tx-integration")
    assert store.show("tx-integration")["partial_state"] == "PUSH_FAILED"
    with pytest.raises(GitTransactionError, match="before push"):
        store.execute("tx-integration")
    assert store.show("tx-integration")["partial_state"] == "PUSH_FAILED"
    assert calls == {"merge": 1, "push": 1}


def test_integration_recovery_accepts_push_that_succeeded_before_probe_failure(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, _, _ = setup_integration_repo(project_root, tmp_path)
    integration_plan(store, ["merge", "push"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    import role_cli_workflow.git_transaction as module
    original = module.git
    calls = {"merge": 0, "push": 0}
    fail_verification = False

    def verification_failure(repo_path: Path, *arguments: str, check: bool = True):
        nonlocal fail_verification
        if arguments and arguments[0] == "merge":
            calls["merge"] += 1
        if arguments and arguments[0] == "push":
            calls["push"] += 1
            result = original(repo_path, *arguments, check=check)
            fail_verification = True
            return result
        if arguments and arguments[0] == "ls-remote" and fail_verification:
            fail_verification = False
            return subprocess.CompletedProcess(["git"], 1, "", "network unavailable")
        return original(repo_path, *arguments, check=check)

    monkeypatch.setattr(module, "git", verification_failure)
    with pytest.raises(GitTransactionError, match="could not be verified"):
        store.execute("tx-integration")
    assert store.show("tx-integration")["partial_state"] == "PUSH_FAILED"
    result = store.execute("tx-integration")
    assert result["partial_state"] == "PUSH_COMPLETED"
    assert result["consumed"] is True
    assert calls == {"merge": 1, "push": 1}


def test_integration_recovery_invalidates_other_remote_sha_without_remerging(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    store, repo, _ = setup_integration_repo(project_root, tmp_path)
    integration_plan(store, ["merge", "push"])
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_ROLE", "supervisor")
    store.approve("tx-integration", "approved displayed tx-integration plan")
    import role_cli_workflow.git_transaction as module
    original = module.git
    calls = {"merge": 0, "push": 0}

    def fail_first_push(repo_path: Path, *arguments: str, check: bool = True):
        if arguments and arguments[0] == "merge":
            calls["merge"] += 1
        if arguments and arguments[0] == "push":
            calls["push"] += 1
            if calls["push"] == 1:
                return subprocess.CompletedProcess(["git"], 1, "", "temporary failure")
        return original(repo_path, *arguments, check=check)

    monkeypatch.setattr(module, "git", fail_first_push)
    with pytest.raises(GitTransactionError, match="push failure"):
        store.execute("tx-integration")
    merge_sha = store.show("tx-integration")["merge_sha"]
    tree = run("git", "rev-parse", f"{merge_sha}^{{tree}}", cwd=repo).stdout.strip()
    other_sha = run(
        "git", "commit-tree", tree, "-p", merge_sha, "-m", "remote drift", cwd=repo
    ).stdout.strip()
    run(
        "git",
        "push",
        "origin",
        f"{other_sha}:refs/heads/remote-drift",
        cwd=repo,
    )
    run(
        "git",
        "--git-dir",
        str(tmp_path / "remote.git"),
        "update-ref",
        "refs/heads/trunk",
        other_sha,
        cwd=tmp_path,
    )
    with pytest.raises(GitTransactionError, match="destination ref changed"):
        store.execute("tx-integration")
    assert store.show("tx-integration")["partial_state"] == "INVALIDATED"
    assert calls == {"merge": 1, "push": 1}


def test_integration_plan_rejects_unlisted_operations(
    project_root: Path, tmp_path: Path
) -> None:
    store, _, _ = setup_integration_repo(project_root, tmp_path)
    with pytest.raises(GitTransactionError, match="operations"):
        store.create_integration_plan(
            transaction_id="tx-unlisted",
            workflow_id="wf-1",
            task_id="task-1",
            repo_id="main",
            operations=["merge", "push", "tag"],
            source_branch="feature/integration",
            target_branch="trunk",
            merge_method="ff-only",
        )
