"""Git and project discovery using fixed, non-shell subprocess calls."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class ProjectError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, shell=False, check=False)
    if check and result.returncode:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "command failed"
        raise ProjectError(detail)
    return result


def git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *arguments], check=check)


def is_git_repo(repo: Path) -> bool:
    return git(repo, "rev-parse", "--is-inside-work-tree", check=False).stdout.strip() == "true"


def detect_repo(repo: Path) -> dict[str, object]:
    if not repo.is_dir() or not is_git_repo(repo):
        raise ProjectError(f"main is not a Git repository: {repo}")
    branch = git(repo, "branch", "--show-current").stdout.strip()
    if not branch:
        raise ProjectError("main repository is in detached HEAD state")
    origin = git(repo, "remote", "get-url", "origin", check=False)
    symbolic = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    origin_head = symbolic.stdout.strip()
    base = origin_head.removeprefix("origin/") if origin_head else branch
    status = git(repo, "status", "--porcelain=v1").stdout
    return {
        "branch": branch,
        "origin": origin.stdout.strip() if origin.returncode == 0 else "",
        "origin_head": origin_head,
        "base_branch": base,
        "clean": not bool(status.strip()),
    }


def worktree_map(main: Path) -> dict[str, dict[str, str]]:
    output = git(main, "worktree", "list", "--porcelain").stdout
    rows: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if "worktree" in current:
                rows[str(Path(current["worktree"]).resolve())] = current
            current = {}
        elif " " in line:
            key, value = line.split(" ", 1)
            current[key] = value
        else:
            current[line] = "true"
    return rows


def branch_exists(main: Path, branch: str) -> bool:
    return git(main, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


def ensure_worktree(main: Path, target: Path, branch: str, base: str) -> None:
    mapping = worktree_map(main)
    existing = mapping.get(str(target.resolve()))
    expected_ref = f"refs/heads/{branch}"
    if existing:
        if existing.get("branch") != expected_ref:
            raise ProjectError(f"existing worktree uses a different branch: {target}")
        return
    if target.exists() and any(target.iterdir()):
        raise ProjectError(f"refusing non-empty role directory: {target}")
    if branch_exists(main, branch):
        raise ProjectError(f"branch exists but is not mapped to the expected worktree: {branch}")
    target.parent.mkdir(parents=True, exist_ok=True)
    git(main, "worktree", "add", "-b", branch, str(target), base)


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
