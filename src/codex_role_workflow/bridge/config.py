"""Resolve one fixed six-role project instance from a trusted environment root."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from ..config import ROLES, WORKERS
from .models import RoleConfig


class ConfigurationError(RuntimeError):
    pass


def _project_root() -> Path:
    raw = os.environ.get("CODEX_ROLE_WORKFLOW_PROJECT_ROOT", "")
    if not raw:
        raise ConfigurationError("project root environment is missing")
    root = Path(raw).expanduser().resolve()
    marker = root / ".codex-workflow" / "project.toml"
    if not marker.is_file():
        raise ConfigurationError("project root is not initialized")
    return root


PROJECT_ROOT = _project_root()
BRIDGE_ROOT = PROJECT_ROOT / "shared_workspace" / "role_bridge"
CONFIG_PATH = BRIDGE_ROOT / "config" / "roles.toml"
RUNTIME_ROOT = PROJECT_ROOT / "shared_workspace" / "runtime"
WORKFLOW_ROOT = PROJECT_ROOT / "shared_workspace" / "workflow"
TMUX_SOCKET = RUNTIME_ROOT / "tmux.sock"
ROLE_REPOS = {role: PROJECT_ROOT / ("main" if role == "supervisor" else role) for role in ROLES}
WORKER_ROLES = frozenset(WORKERS)


def load_roles() -> dict[str, RoleConfig]:
    if not CONFIG_PATH.is_file() or CONFIG_PATH.stat().st_mode & 0o022:
        raise ConfigurationError("role configuration is unavailable")
    with CONFIG_PATH.open("rb") as handle:
        raw = tomllib.load(handle)
    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, dict) or set(roles_raw) != set(ROLE_REPOS):
        raise ConfigurationError("role map is invalid")
    roles: dict[str, RoleConfig] = {}
    sessions: set[str] = set()
    for name, expected_repo in ROLE_REPOS.items():
        value = roles_raw.get(name)
        if not isinstance(value, dict):
            raise ConfigurationError("role map is invalid")
        role = RoleConfig(name, str(value.get("session", "")), str(value.get("window", "")), str(value.get("working_directory", "")))
        if role.window != name or Path(role.working_directory) != expected_repo or not role.session:
            raise ConfigurationError("role map is invalid")
        sessions.add(role.session)
        roles[name] = role
    if len(sessions) != 1:
        raise ConfigurationError("role sessions are inconsistent")
    return roles


def ensure_runtime_permissions() -> None:
    RUNTIME_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(RUNTIME_ROOT, 0o700)
    for name in ("tasks", "metadata", "decisions", "logs"):
        path = RUNTIME_ROOT / name
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
