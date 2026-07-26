from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import BootstrapError, init_project, sync_project
from role_cli_workflow.config import ROLES, WORKERS, load_project


def digest(root: Path) -> str:
    value = hashlib.sha256()
    for path in sorted((root / "shared_workspace").rglob("*")):
        if path.is_file() and "runtime" not in path.parts and "workflow" not in path.parts:
            value.update(str(path.relative_to(root)).encode())
            value.update(path.read_bytes())
    return value.hexdigest()


def test_init_creates_fixed_layout_without_overwriting_main(project_root: Path) -> None:
    before = (project_root / "main/README.md").read_bytes()
    warnings = init_project(project_root, assume_yes=True)
    assert "Sample project" in (project_root / ".role-cli-workflow/project_instructions.md").read_text()
    assert (project_root / "main/README.md").read_bytes() == before
    config = load_project(project_root)
    assert config.base_branch == "trunk"
    assert config.root == project_root.resolve()
    for role in WORKERS:
        repo = project_root / role
        assert (repo / ".git").is_file()
        branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"], text=True, capture_output=True, check=True, shell=False).stdout.strip()
        assert branch == f"workflow/{role}"
    for role in ROLES:
        repo = config.repo(role)
        ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", ".codex/config.toml"], text=True, capture_output=True, shell=False)
        assert ignored.returncode == 0
        assert not subprocess.run(["git", "-C", str(repo), "ls-files", "--", ".codex"], text=True, capture_output=True, check=True, shell=False).stdout
        runner = project_root / "shared_workspace/scripts" / f"run_{role.replace('-', '_')}.sh"
        subprocess.run(["bash", "-n", str(runner)], check=True, shell=False)
    subprocess.run(["bash", "-n", str(project_root / "shared_workspace/scripts/run_bridge_server.sh")], check=True, shell=False)
    assert not (project_root / "main/.gitignore").exists()
    assert warnings == []


def test_nonempty_role_directory_is_rejected(project_root: Path) -> None:
    target = project_root / "explorer"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(BootstrapError, match="non-empty"):
        init_project(project_root, assume_yes=True)
    assert (target / "keep.txt").read_text() == "keep"
    assert not (project_root / ".role-cli-workflow").exists()


def test_sync_is_idempotent_and_preserves_runtime_history(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    runtime = project_root / "shared_workspace/runtime/tasks/keep/result.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("keep", encoding="utf-8")
    workflow = project_root / "shared_workspace/workflow/tasks/keep/report.md"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("keep", encoding="utf-8")
    first = digest(project_root)
    sync_project(project_root)
    second = digest(project_root)
    sync_project(project_root)
    assert digest(project_root) == second == first
    assert runtime.read_text() == workflow.read_text() == "keep"


def test_init_does_not_create_codex_home_or_copy_auth(project_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    init_project(project_root, assume_yes=True)
    assert not (fake_home / ".codex").exists()
    assert not list(project_root.rglob("auth.json"))


def test_unknown_project_facts_remain_todo(tmp_path: Path) -> None:
    root = tmp_path / "Unknown"
    main = root / "main"
    main.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=main, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=main, check=True, shell=False)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=main, check=True, shell=False)
    (main / "file.txt").write_text("x")
    subprocess.run(["git", "add", "file.txt"], cwd=main, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=main, check=True, capture_output=True, shell=False)
    warnings = init_project(root, assume_yes=True)
    text = (root / ".role-cli-workflow/project_instructions.md").read_text()
    assert "TECH_STACK_UNCONFIRMED" in warnings
    assert "PROJECT_PURPOSE_UNCONFIRMED" in warnings
    assert "TODO" in text
