"""Fixed tmux lifecycle without pane parsing or background monitoring."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .bootstrap import sync_project
from .config import ROLES, ProjectConfig, load_project
from .doctor import print_checks, run_doctor
from .project import git


class LifecycleError(RuntimeError):
    pass


def _tmux(config: ProjectConfig, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["tmux", "-S", str(config.socket), *args], text=True, capture_output=True, shell=False, check=False)
    if check and result.returncode:
        raise LifecycleError(result.stderr.strip() or "tmux operation failed")
    return result


def is_live(config: ProjectConfig) -> bool:
    return config.socket.exists() and _tmux(config, "has-session", "-t", config.session, check=False).returncode == 0


def open_workflow(root: str | Path) -> None:
    config = load_project(root)
    sync_project(config.root)
    checks = run_doctor(config.root)
    if print_checks(checks):
        raise LifecycleError("doctor reported FAIL; runtime was not started")
    if is_live(config):
        raise LifecycleError("workflow runtime is already running")
    if config.socket.exists():
        config.socket.unlink()
    config.runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmux_conf = config.bridge_root / "config" / "tmux.conf"
    supervisor = config.shared / "scripts" / "run_supervisor.sh"
    _tmux(config, "-f", str(tmux_conf), "new-session", "-d", "-s", config.session, "-n", "supervisor", "-c", str(config.repo("supervisor")), str(supervisor))
    for role in ROLES[1:]:
        runner = config.shared / "scripts" / f"run_{role.replace('-', '_')}.sh"
        _tmux(config, "new-window", "-d", "-t", config.session, "-n", role, "-c", str(config.repo(role)), str(runner))
    _tmux(config, "select-window", "-t", f"{config.session}:supervisor")


def attach_workflow(root: str | Path) -> None:
    config = load_project(root)
    if not is_live(config):
        raise LifecycleError("workflow runtime is not running")
    os.execvp("tmux", ["tmux", "-S", str(config.socket), "attach-session", "-t", f"{config.session}:supervisor"])


def stop_workflow(root: str | Path) -> None:
    config = load_project(root)
    if is_live(config):
        _tmux(config, "kill-server")
        for _ in range(20):
            if not config.socket.exists():
                break
            time.sleep(0.05)
    if config.socket.exists() and not is_live(config):
        config.socket.unlink()


def verify_workflow(root: str | Path) -> int:
    config = load_project(root)
    if not is_live(config):
        print("FAIL runtime: fixed tmux session is not running")
        return 1
    failure = False
    for role in ROLES:
        result = _tmux(config, "list-panes", "-t", f"{config.session}:{role}", "-F", "#{pane_dead}\t#{pane_current_command}", check=False)
        good = result.returncode == 0 and result.stdout.startswith("0\t")
        print(f"{'PASS' if good else 'FAIL'} pane:{role}: {result.stdout.strip() or 'unavailable'}")
        failure |= not good
    return int(failure)


def _worktree_git_status(config: ProjectConfig, role: str) -> dict[str, object]:
    repo = config.repo(role)
    branch_result = git(repo, "branch", "--show-current", check=False)
    head_result = git(repo, "rev-parse", "--short=7", "HEAD", check=False)
    status_result = git(
        repo, "status", "--porcelain=v1", "--untracked-files=normal", check=False
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    head = head_result.stdout.strip() if head_result.returncode == 0 else ""
    if status_result.returncode == 0:
        changes = [line for line in status_result.stdout.splitlines() if line]
        modified = sum(not line.startswith("??") for line in changes)
        untracked = sum(line.startswith("??") for line in changes)
        state = "CLEAN" if not changes else "DIRTY"
    else:
        modified = untracked = "UNKNOWN"
        state = "UNKNOWN"
    return {
        "role": role,
        "repo": "main" if role == "supervisor" else role,
        "branch": branch or "UNKNOWN",
        "head": head or "UNKNOWN",
        "state": state,
        "modified": modified,
        "untracked": untracked,
    }


def _legacy_authority_workflows(config: ProjectConfig) -> list[str]:
    workflows: set[str] = set()
    tasks = config.runtime_root / "tasks"
    for path in sorted(tasks.glob("*/contract.json")) if tasks.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        authority = payload.get("effective_authority") if isinstance(payload, dict) else None
        workflow_id = payload.get("workflow_id") if isinstance(payload, dict) else None
        if (
            isinstance(authority, dict)
            and authority.get("status") == "LEGACY_AUTHORITY_WARNING"
            and isinstance(workflow_id, str)
        ):
            workflows.add(workflow_id)
    return sorted(workflows)


def status_workflow(root: str | Path) -> int:
    config = load_project(root)
    print(f"Runtime: {'RUNNING' if is_live(config) else 'STOPPED'}")
    metadata = config.runtime_root / "metadata"
    rows = []
    for path in sorted(metadata.glob("*.json")) if metadata.is_dir() else []:
        if path.name == "active-tasks.json":
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    latest = {role: max((row for row in rows if row.get("role") == role), key=lambda row: str(row.get("updated_at", "")), default={}) for role in ROLES[1:]}
    print("Role          Task                  Execution      Callback   Recovery")
    for role in ROLES[1:]:
        row = latest[role]
        print(
            f"{role:13} {str(row.get('task_id', '-')):21} "
            f"{row.get('execution_status', 'IDLE'):14} "
            f"{row.get('callback_status', 'NONE'):10} {row.get('error_code', '') or '-'}"
        )
    print()
    print("Git worktrees (read-only; DIRTY is informational)")
    print("Role          Repo          Branch                    HEAD     State    Modified Untracked")
    for role in ROLES:
        row = _worktree_git_status(config, role)
        print(
            f"{row['role']:13} {row['repo']:13} {row['branch']:25} "
            f"{row['head']:8} {row['state']:8} {str(row['modified']):8} {row['untracked']}"
        )
    legacy = _legacy_authority_workflows(config)
    if legacy:
        print()
        print("Warnings")
        for workflow_id in legacy:
            print(f"LEGACY_AUTHORITY_WARNING workflow:{workflow_id}")
    return 0
