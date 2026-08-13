from __future__ import annotations

import json
import subprocess
from pathlib import Path

from role_cli_workflow import lifecycle
from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.config import ROLES, load_project
from role_cli_workflow.control import ControlStore
from role_cli_workflow.lifecycle import status_workflow
from role_cli_workflow.project import git


def _status_row(output: str, role: str) -> list[str]:
    worktrees = output.split("Git worktrees (read-only; DIRTY is informational)", 1)[1]
    return next(
        line.split()
        for line in worktrees.splitlines()
        if line.startswith(f"{role} ")
    )


def _git_snapshot(project_root: Path) -> dict[str, tuple[str, str]]:
    config = load_project(project_root)
    return {
        role: (
            git(config.repo(role), "rev-parse", "HEAD").stdout.strip(),
            git(
                config.repo(role),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ).stdout,
        )
        for role in ROLES
    }


def test_status_displays_clean_worktrees(
    project_root: Path, capsys
) -> None:
    init_project(project_root, assume_yes=True)
    capsys.readouterr()

    assert status_workflow(project_root) == 0
    output = capsys.readouterr().out

    assert "Git worktrees (read-only; DIRTY is informational)" in output
    assert "Control: NORMAL (owner: supervisor)" in output
    for role in ROLES:
        row = _status_row(output, role)
        assert row[4:] == ["CLEAN", "0", "0"]
        assert row[2] != "UNKNOWN"
        assert len(row[3]) == 7


def test_status_displays_dirty_counts_without_modifying_worktrees(
    project_root: Path, capsys
) -> None:
    init_project(project_root, assume_yes=True)
    implementer = load_project(project_root).repo("implementer")
    (implementer / "README.md").write_text("# changed locally\n", encoding="utf-8")
    (implementer / "local-note.txt").write_text("untracked\n", encoding="utf-8")
    before = _git_snapshot(project_root)
    capsys.readouterr()

    assert status_workflow(project_root) == 0
    output = capsys.readouterr().out
    after = _git_snapshot(project_root)

    assert _status_row(output, "implementer")[4:] == ["DIRTY", "1", "1"]
    assert _status_row(output, "evaluator")[4:] == ["CLEAN", "0", "0"]
    assert after == before


def test_status_keeps_other_rows_when_one_worktree_query_fails(
    project_root: Path, monkeypatch, capsys
) -> None:
    init_project(project_root, assume_yes=True)
    config = load_project(project_root)
    real_git = lifecycle.git

    def failing_git(repo: Path, *arguments: str, check: bool = True):
        if repo == config.repo("evaluator"):
            return subprocess.CompletedProcess(
                ["git", "-C", str(repo), *arguments],
                1,
                "",
                "simulated query failure",
            )
        return real_git(repo, *arguments, check=check)

    monkeypatch.setattr(lifecycle, "git", failing_git)
    capsys.readouterr()

    assert status_workflow(project_root) == 0
    output = capsys.readouterr().out

    assert _status_row(output, "evaluator")[2:] == [
        "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"
    ]
    assert _status_row(output, "explorer")[4:] == ["CLEAN", "0", "0"]


def test_status_keeps_legacy_authority_warning_visible(
    project_root: Path, capsys
) -> None:
    init_project(project_root, assume_yes=True)
    task = load_project(project_root).runtime_root / "tasks/task-legacy"
    task.mkdir(parents=True)
    (task / "contract.json").write_text(json.dumps({
        "workflow_id": "wf-legacy",
        "effective_authority": {"status": "LEGACY_AUTHORITY_WARNING"},
    }), encoding="utf-8")
    capsys.readouterr()

    assert status_workflow(project_root) == 0
    output = capsys.readouterr().out

    assert "Warnings" in output
    assert "LEGACY_AUTHORITY_WARNING workflow:wf-legacy" in output


def test_status_displays_escalation_and_invalid_state(
    project_root: Path, capsys
) -> None:
    init_project(project_root, assume_yes=True)
    store = ControlStore(load_project(project_root).runtime_root)
    store.acquire("architecture-001", "Resolve one complex change directly")
    capsys.readouterr()

    assert status_workflow(project_root) == 0
    output = capsys.readouterr().out
    assert (
        "Control: ESCALATION (owner: escalation-controller), "
        "intervention: architecture-001"
    ) in output

    store.path.write_text("invalid", encoding="utf-8")
    assert status_workflow(project_root) == 1
    assert "Control: INVALID (management mutations fail closed)" in capsys.readouterr().out
