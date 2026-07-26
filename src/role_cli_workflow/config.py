"""Project-instance configuration and fixed role topology."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROLES = ("supervisor", "explorer", "implementer", "evaluator", "reviewer", "doc-curator")
WORKERS = ROLES[1:]
DEFAULT_BRANCHES = {role: f"workflow/{role}" for role in WORKERS}
TEMPLATE_VERSION = "0.2.0"


class ProjectConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    name: str
    main_repo: str
    workflow_dir: str
    cli_provider: str
    cli_command: str
    cli_args: tuple[str, ...]
    cli_version_args: tuple[str, ...]
    cli_login_check_args: tuple[str, ...]
    default_model: str | None
    default_reasoning_effort: str | None
    base_branch: str
    role_branch_prefix: str
    feature_branch_pattern: str
    protected_branches: tuple[str, ...]
    private_paths: tuple[str, ...]
    commands: dict[str, str]
    role_branches: dict[str, str]
    role_models: dict[str, str]
    role_reasoning_efforts: dict[str, str]
    role_cli_args: dict[str, tuple[str, ...]]

    @property
    def metadata_dir(self) -> Path:
        return self.root / ".role-cli-workflow"

    @property
    def shared(self) -> Path:
        return self.root / "shared_workspace"

    @property
    def bridge_root(self) -> Path:
        return self.shared / "role_bridge"

    @property
    def runtime_root(self) -> Path:
        return self.shared / "runtime"

    @property
    def workflow_root(self) -> Path:
        return self.root / self.workflow_dir

    @property
    def socket(self) -> Path:
        return self.runtime_root / "tmux.sock"

    @property
    def session(self) -> str:
        safe = "".join(ch if ch.isalnum() else "-" for ch in self.name.lower()).strip("-")
        return f"crw-{safe or 'project'}"

    def repo(self, role: str) -> Path:
        return self.root / ("main" if role == "supervisor" else role)

    def model_for(self, role: str) -> str | None:
        return self.role_models.get(role, self.default_model)

    def reasoning_effort_for(self, role: str) -> str | None:
        return self.role_reasoning_efforts.get(role, self.default_reasoning_effort)


def load_project(root: str | Path) -> ProjectConfig:
    project_root = Path(root).expanduser().resolve()
    path = project_root / ".role-cli-workflow" / "project.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        project = raw["project"]
        cli = raw.get("cli", raw.get("codex"))
        if not isinstance(cli, dict):
            raise KeyError("cli")
        git = raw["git"]
        paths = raw["paths"]
        commands = raw["commands"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ProjectConfigError(f"invalid project configuration: {path}") from exc
    configured_root = Path(str(project["root"])).expanduser().resolve()
    if configured_root != project_root:
        raise ProjectConfigError("project root does not match project.toml")
    provider = str(cli.get("provider", "codex")).strip().lower()
    if provider not in {"codex", "generic"}:
        raise ProjectConfigError("cli.provider must be codex or generic")
    command = cli.get("command", "codex" if provider == "codex" else None)
    if not isinstance(command, str) or not command.strip():
        raise ProjectConfigError("cli.command must be a non-empty string")

    def string_list(value: object, name: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ProjectConfigError(f"{name} must be an array of non-empty strings")
        return tuple(value)

    cli_args = string_list(cli.get("args", []), "cli.args")
    cli_version_args = string_list(cli.get("version_args", ["--version"]), "cli.version_args")
    default_login = ["login", "status"] if provider == "codex" else []
    cli_login_check_args = string_list(cli.get("login_check_args", default_login), "cli.login_check_args")

    def optional_string(value: object, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ProjectConfigError(f"{name} must be a non-empty string")
        return value.strip()

    default_model = optional_string(cli.get("model"), "cli.model")
    default_reasoning_effort = optional_string(cli.get("reasoning_effort"), "cli.reasoning_effort")
    role_settings = cli.get("roles", {})
    if not isinstance(role_settings, dict):
        raise ProjectConfigError("cli.roles must be a table")
    unknown_roles = sorted(set(role_settings) - set(ROLES))
    if unknown_roles:
        raise ProjectConfigError(f"unknown cli role settings: {', '.join(unknown_roles)}")
    role_models: dict[str, str] = {}
    role_reasoning_efforts: dict[str, str] = {}
    role_cli_args: dict[str, tuple[str, ...]] = {}
    for role, values in role_settings.items():
        if not isinstance(values, dict):
            raise ProjectConfigError(f"cli.roles.{role} must be a table")
        for key, target in (("model", role_models), ("reasoning_effort", role_reasoning_efforts)):
            value = values.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ProjectConfigError(f"cli.roles.{role}.{key} must be a non-empty string")
            target[role] = value.strip()
        role_cli_args[role] = string_list(values.get("args", []), f"cli.roles.{role}.args")
    branches = {role: str(git.get(f"{role.replace('-', '_')}_branch", f"{git['role_branch_prefix']}{role}")) for role in WORKERS}
    return ProjectConfig(
        root=project_root,
        name=str(project["name"]),
        main_repo=str(project.get("main_repo", "main")),
        workflow_dir=str(project.get("workflow_dir", "shared_workspace/workflow")),
        cli_provider=provider,
        cli_command=command.strip(),
        cli_args=cli_args,
        cli_version_args=cli_version_args,
        cli_login_check_args=cli_login_check_args,
        default_model=default_model,
        default_reasoning_effort=default_reasoning_effort,
        base_branch=str(git["base_branch"]),
        role_branch_prefix=str(git.get("role_branch_prefix", "workflow/")),
        feature_branch_pattern=str(git.get("feature_branch_pattern", "feature/*")),
        protected_branches=tuple(str(item) for item in git.get("protected_branches", [])),
        private_paths=tuple(str(item) for item in paths.get("private", [])),
        commands={name: str(commands.get(name, "")) for name in ("test", "lint", "build", "format")},
        role_branches=branches,
        role_models=role_models,
        role_reasoning_efforts=role_reasoning_efforts,
        role_cli_args=role_cli_args,
    )
