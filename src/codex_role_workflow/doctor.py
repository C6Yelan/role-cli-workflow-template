"""Read-only validation for a project workflow instance."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ROLES, WORKERS, ProjectConfig, ProjectConfigError, load_project
from .project import branch_exists, is_git_repo, worktree_map


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


def _check(level: str, name: str, detail: str) -> Check:
    return Check(level, name, detail)


def _trust(repo: Path) -> bool:
    path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    row = data.get("projects", {}).get(str(repo), {})
    return isinstance(row, dict) and row.get("trust_level") == "trusted"


def _version(binary: str, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run([binary, *args], text=True, capture_output=True, shell=False, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return 1, "version probe failed"
    return result.returncode, (result.stdout or result.stderr).strip()


def _login_ok(binary: str, args: tuple[str, ...]) -> bool:
    try:
        return subprocess.run([binary, *args], text=True, capture_output=True, shell=False, check=False, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _autoload_ok(codex: str, repo: Path, role: str) -> bool:
    try:
        result = subprocess.run([codex, "-C", str(repo), "debug", "prompt-input"], text=True, capture_output=True, shell=False, check=False, timeout=10)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and f"CODEX_ROLE_WORKFLOW_CONFIG_PROBE:{role}" in result.stdout


async def _handshake(root: Path, role: str) -> set[str]:
    env = {
        "CODEX_ROLE_WORKFLOW_PROJECT_ROOT": str(root),
        "CODEX_ROLE_WORKFLOW_ROLE": role,
    }
    params = StdioServerParameters(
        command=str(root / "shared_workspace" / "scripts" / "run_bridge_server.sh"),
        args=[],
        cwd=str(root / "shared_workspace" / "role_bridge"),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10)
            response = await asyncio.wait_for(session.list_tools(), timeout=10)
            return {tool.name for tool in response.tools}


async def _handshake_matrix(root: Path) -> dict[str, set[str] | BaseException]:
    async def one(role: str) -> tuple[str, set[str] | BaseException]:
        try:
            return role, await asyncio.wait_for(_handshake(root, role), timeout=10.0)
        except BaseException as exc:
            return role, exc
    return dict(await asyncio.gather(*(one(role) for role in ROLES)))


def _role_checks(config: ProjectConfig, binary: str | None, role: str) -> list[Check]:
    if config.cli_provider == "generic":
        instructions = config.shared / "roles" / f"{role}.md"
        runner = config.shared / "scripts" / f"run_{role.replace('-', '_')}.sh"
        return [
            _check(
                "PASS" if instructions.is_file() and runner.is_file() else "FAIL",
                f"provider:{role}",
                "role instructions and launcher available" if instructions.is_file() and runner.is_file() else "run sync",
            )
        ]
    checks = [_check("PASS" if _trust(config.repo(role)) else "FAIL", f"trust:{role}", str(config.repo(role)))]
    local = config.repo(role) / ".codex" / "config.toml"
    canonical = config.bridge_root / "config" / "codex_roles" / role / "config.toml"
    same = local.is_file() and canonical.is_file() and local.read_bytes() == canonical.read_bytes()
    checks.append(_check("PASS" if same else "FAIL", f"config:{role}", "deployment matches canonical" if same else "run sync"))
    loaded = bool(binary and _autoload_ok(binary, config.repo(role), role))
    checks.append(_check("PASS" if loaded else "FAIL", f"config-autoload:{role}", "repo-local marker loaded by Codex" if loaded else "project-local config did not auto-load"))
    checks.append(_execpolicy(config, role))
    return checks


def _execpolicy(config: ProjectConfig, role: str) -> Check:
    codex = shutil.which(config.cli_command)
    if not codex:
        return _check("FAIL", f"rules:{role}", "Codex CLI unavailable")
    rules = config.repo(role) / ".codex" / "rules"
    files = sorted(rules.glob("*.rules"))
    if len(files) != 2:
        return _check("FAIL", f"rules:{role}", "deployed rules are incomplete")
    command = [codex, "execpolicy", "check"]
    for path in files:
        command.extend(["--rules", str(path)])
    command.extend(["--", "git", "status"])
    try:
        result = subprocess.run(command, text=True, capture_output=True, shell=False, check=False, timeout=10)
    except subprocess.TimeoutExpired:
        return _check("FAIL", f"rules:{role}", "execpolicy probe timed out")
    if result.returncode:
        return _check("FAIL", f"rules:{role}", "deployed rules did not load")
    try:
        decision = json.loads(result.stdout).get("decision")
    except (json.JSONDecodeError, AttributeError):
        decision = None
    return _check("PASS" if decision == "allow" else "FAIL", f"rules:{role}", f"git status => {decision}")


def run_doctor(root: str | Path, *, include_handshake: bool = True) -> list[Check]:
    checks: list[Check] = []
    try:
        config = load_project(root)
    except ProjectConfigError as exc:
        return [_check("FAIL", "project.toml", str(exc))]
    checks.append(_check("PASS", "project.toml", str(config.metadata_dir / "project.toml")))
    main = config.repo("supervisor")
    checks.append(_check("PASS" if is_git_repo(main) else "FAIL", "main Git repo", str(main)))
    mapping = worktree_map(main) if is_git_repo(main) else {}
    for role in WORKERS:
        repo = config.repo(role)
        row = mapping.get(str(repo.resolve()))
        expected = f"refs/heads/{config.role_branches[role]}"
        valid = is_git_repo(repo) and branch_exists(main, config.role_branches[role]) and row is not None and row.get("branch") == expected
        checks.append(_check("PASS" if valid else "FAIL", f"worktree:{role}", f"{repo} -> {config.role_branches[role]}"))
    binary = shutil.which(config.cli_command)
    if binary:
        checks.append(_check("PASS", "CLI", f"{config.cli_provider}: {binary}"))
        if config.cli_version_args:
            code, output = _version(binary, *config.cli_version_args)
            checks.append(_check("PASS" if code == 0 else "WARNING", "CLI version", output or "version probe returned no output"))
        else:
            checks.append(_check("WARNING", "CLI version", "version probe not configured"))
    else:
        checks.append(_check("FAIL", "CLI", f"{config.cli_command} not found"))
    if config.cli_login_check_args:
        logged_in = bool(binary and _login_ok(binary, config.cli_login_check_args))
        checks.append(_check("PASS" if logged_in else "FAIL", "CLI login", "configured login probe passed" if logged_in else "configured login probe failed"))
    else:
        checks.append(_check("WARNING", "CLI login", "login probe not configured"))
    if config.cli_provider == "generic":
        checks.append(_check("WARNING", "CLI safety policy", "generic provider sandbox, approvals, trust, and Git policy must be enforced by the CLI or adapter"))
    for tool in ("tmux", "uv"):
        checks.append(_check("PASS" if shutil.which(tool) else "FAIL", tool, shutil.which(tool) or "not found"))
    if sys.version_info[:2] == (3, 12):
        checks.append(_check("PASS", "Python", sys.version.split()[0]))
    else:
        checks.append(_check("FAIL", "Python", "Python 3.12 is required"))
    with ThreadPoolExecutor(max_workers=len(ROLES)) as pool:
        role_rows = list(pool.map(lambda role: _role_checks(config, binary, role), ROLES))
    for rows in role_rows:
        checks.extend(rows)
    if include_handshake:
        expected = {
            "supervisor": {
                "list_roles", "assign_task", "cancel_task", "retry_dispatch",
                "retry_callback", "get_task_result", "send_rework", "record_decision",
            },
            "worker": {"get_current_task", "get_context", "submit_result", "report_blocked"},
        }
        matrix = asyncio.run(_handshake_matrix(config.root))
        for role in ROLES:
            tools = matrix[role]
            if isinstance(tools, BaseException):
                checks.append(_check("FAIL", f"MCP:{role}", f"stdio handshake failed: {type(tools).__name__}"))
            else:
                wanted = expected["supervisor" if role == "supervisor" else "worker"]
                checks.append(_check("PASS" if tools == wanted else "FAIL", f"MCP:{role}", ", ".join(sorted(tools))))
    if config.socket.exists():
        live = subprocess.run(["tmux", "-S", str(config.socket), "has-session", "-t", config.session], shell=False, check=False, capture_output=True).returncode == 0
        checks.append(_check("PASS" if live else "WARNING", "runtime socket", "live runtime" if live else "stale socket"))
    else:
        checks.append(_check("PASS", "runtime socket", "not running"))
    for name, value in config.commands.items():
        if not value:
            checks.append(_check("WARNING", f"command:{name}", "not configured"))
    checks.append(_check("PASS" if config.private_paths else "FAIL", "private paths", f"{len(config.private_paths)} configured patterns"))
    return checks


def print_checks(checks: list[Check]) -> int:
    for row in checks:
        print(f"{row.level:7} {row.name}: {row.detail}")
    return 1 if any(row.level == "FAIL" for row in checks) else 0
