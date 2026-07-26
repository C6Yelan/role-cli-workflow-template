from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, text=True, capture_output=True, shell=False, check=True)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "SampleProject"
    main = root / "main"
    main.mkdir(parents=True)
    run("git", "init", "-b", "trunk", cwd=main)
    run("git", "config", "user.name", "Template Test", cwd=main)
    run("git", "config", "user.email", "template@example.invalid", cwd=main)
    (main / "README.md").write_text("# Sample project\n", encoding="utf-8")
    (main / "pyproject.toml").write_text("[project]\nname='sample'\nversion='0'\n", encoding="utf-8")
    run("git", "add", "README.md", "pyproject.toml", cwd=main)
    run("git", "commit", "-m", "initial", cwd=main)
    return root
