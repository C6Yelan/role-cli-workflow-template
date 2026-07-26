"""Bootstrap and synchronize one fixed-layout project instance."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from .config import ROLES, TEMPLATE_VERSION, WORKERS, ProjectConfig, load_project
from .project import ProjectError, detect_repo, ensure_worktree, git


class BootstrapError(RuntimeError):
    pass


def _template_root() -> Path:
    packaged = Path(__file__).resolve().parent / "templates"
    source = Path(__file__).resolve().parents[2] / "templates"
    root = packaged if packaged.is_dir() else source
    if not root.is_dir():
        raise BootstrapError("installed workflow templates are unavailable")
    return root


def _template_text(relative: str) -> str:
    try:
        return (_template_root() / relative).read_text(encoding="utf-8")
    except OSError as exc:
        raise BootstrapError(f"workflow template is unavailable: {relative}") from exc


def _atomic_text(path: Path, text: str, mode: int = 0o600) -> bool:
    if path.is_symlink():
        raise BootstrapError(f"refusing symlink target: {path}")
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    if previous == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _project_toml(root: Path, name: str, base: str) -> str:
    protected = list(dict.fromkeys([base, "main"]))
    protected_text = ", ".join(_toml_string(item) for item in protected)
    return f'''[project]
name = {_toml_string(name)}
root = {_toml_string(str(root))}
main_repo = "main"
workflow_dir = "shared_workspace/workflow"

[cli]
provider = "codex"
command = "codex"
args = []
version_args = ["--version"]
login_check_args = ["login", "status"]

# Optional defaults for every role. Omit them to use the CLI's defaults.
# model = "your-model"
# reasoning_effort = "medium"

# Optional per-role overrides.
# [cli.roles.supervisor]
# model = "your-model"
# reasoning_effort = "medium"
# args = []

[git]
base_branch = {_toml_string(base)}
role_branch_prefix = "workflow/"
feature_branch_pattern = "feature/*"
protected_branches = [{protected_text}]

[paths]
private = [".env", ".env.*", "private_docs/", "secrets/", "credentials/", "*.pem", "*.key"]

[commands]
test = ""
lint = ""
build = ""
format = ""
'''


def _project_instructions(main: Path, base: str) -> tuple[str, list[str]]:
    markers = {
        "pyproject.toml": "Python",
        "package.json": "Node.js",
        "Cargo.toml": "Rust",
        "go.mod": "Go",
    }
    stack = [label for filename, label in markers.items() if (main / filename).is_file()]
    if any((main / name).is_file() for name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")):
        stack.append("Docker Compose")
    warnings: list[str] = []
    if not stack:
        warnings.append("TECH_STACK_UNCONFIRMED")
    readme = next((path for path in (main / "README.md", main / "README.rst", main / "README") if path.is_file()), None)
    purpose = "TODO — confirm from the repository README and product owner."
    if readme is None:
        warnings.append("PROJECT_PURPOSE_UNCONFIRMED")
    else:
        try:
            candidates = [
                line.strip().lstrip("#").strip()
                for line in readme.read_text(encoding="utf-8", errors="replace").splitlines()[:80]
                if line.strip() and not line.lstrip().startswith(("[", "!", "<"))
            ]
            if candidates:
                purpose = f"Candidate from README — {candidates[0][:300]} (confirm before use)."
            else:
                warnings.append("PROJECT_PURPOSE_UNCONFIRMED")
        except OSError:
            warnings.append("PROJECT_PURPOSE_UNCONFIRMED")
    text = f'''# Project-specific instructions

- Project purpose: {purpose}
- Detected technology stack: {", ".join(stack) if stack else "TODO — no reliable manifest detected."}
- Base branch: `{base}`
- Feature branch rule: `feature/*`
- Test command: TODO
- Build command: TODO
- Private paths: `.env`, `.env.*`, `private_docs/`, `secrets/`, `credentials/`, `*.pem`, `*.key`
- Project-specific approval rules: TODO

Only confirmed project-specific facts belong here. Fixed role, Git, MCP, and safety rules live in canonical role instructions and Codex rules.
'''
    return text, warnings


def init_project(root: str | Path, *, assume_yes: bool = False) -> list[str]:
    project_root = Path(root).expanduser().resolve()
    main = project_root / "main"
    if (project_root / ".role-cli-workflow" / "project.toml").exists():
        raise BootstrapError("project is already initialized; use sync")
    discovery = detect_repo(main)
    for role in WORKERS:
        target = project_root / role
        if target.exists() and any(target.iterdir()):
            raise BootstrapError(f"refusing non-empty role directory: {target}")
    base = str(discovery["base_branch"])
    branches = [f"{role}: workflow/{role}" for role in WORKERS]
    if not assume_yes:
        print(f"Main repo: {main}\nBase branch: {base}\nClean: {discovery['clean']}")
        print("Worktrees:\n  " + "\n  ".join(branches))
        if input("Create these local branches and worktrees? [yes/N] ").strip().lower() != "yes":
            raise BootstrapError("initialization cancelled")
    for role in WORKERS:
        ensure_worktree(main, project_root / role, f"workflow/{role}", base)
    metadata = project_root / ".role-cli-workflow"
    name = project_root.name
    _atomic_text(metadata / "project.toml", _project_toml(project_root, name, base))
    instructions, warnings = _project_instructions(main, base)
    _atomic_text(metadata / "project_instructions.md", instructions)
    _atomic_text(metadata / "VERSION", TEMPLATE_VERSION + "\n")
    sync_project(project_root)
    return warnings


def _common_rules() -> str:
    return '''# Shared deny-only boundary for all six roles.
prefix_rule(pattern = [["bash", "/bin/bash", "/usr/bin/bash", "sh", "/bin/sh", "/usr/bin/sh", "zsh", "/bin/zsh", "/usr/bin/zsh"], ["-c", "-lc"]], decision = "forbidden", justification = "Shell command-string wrappers cannot bypass role Git policy.")
prefix_rule(pattern = ["git", "reset", "--hard"], decision = "forbidden")
prefix_rule(pattern = ["git", "clean", ["-f", "-fd", "-fdx", "-df", "-dfx"]], decision = "forbidden")
prefix_rule(pattern = ["git", "push", ["--force", "-f", "--force-with-lease"]], decision = "forbidden")
prefix_rule(pattern = ["git", "push", "origin", ["--force", "-f", "--force-with-lease", "--delete"]], decision = "forbidden")
prefix_rule(pattern = ["git", "branch", "-D"], decision = "forbidden")
prefix_rule(pattern = ["git", "worktree", "remove", "--force"], decision = "forbidden")
prefix_rule(pattern = ["git", "reflog", "expire"], decision = "forbidden")
prefix_rule(pattern = ["git", "gc", "--prune=now"], decision = "forbidden")
prefix_rule(pattern = ["git", "checkout", "--", "."], decision = "forbidden")
prefix_rule(pattern = ["git", "restore", "."], decision = "forbidden")
prefix_rule(pattern = ["git", "restore", "--worktree", "."], decision = "forbidden")
'''


def _role_rules(config: ProjectConfig, role: str) -> str:
    read = '''prefix_rule(pattern = ["git", ["status", "diff", "log", "show", "rev-parse", "fetch"]], decision = "allow")
prefix_rule(pattern = ["git", "branch", "--show-current"], decision = "allow")
prefix_rule(pattern = ["git", "pull", "--ff-only"], decision = "allow")
'''
    if role == "supervisor":
        body = read + '''prefix_rule(pattern = ["git", "worktree", "list"], decision = "allow")
prefix_rule(pattern = ["git", ["add", "commit", "merge", "push"]], decision = "prompt", justification = "Supervisor Git integration requires explicit user approval for this stage.")
prefix_rule(pattern = ["git", ["rebase", "cherry-pick", "tag"]], decision = "prompt")
prefix_rule(pattern = ["role-cli-workflow", "git", ["plan", "approve", "execute", "show"]], decision = "allow", justification = "Fixed executor requires a matching approved transaction and accepts no arbitrary command or repo path.")
prefix_rule(pattern = ["role-cli-workflow", "workflow", ["preflight", "freeze-contract", "approve-contract-change", "freeze-sha"]], decision = "allow")
'''
        repos = ", ".join(_toml_string(str(config.repo(item))) for item in ROLES)
        body += f'''fixed_repos = [{repos}]
prefix_rule(pattern = ["git", "-C", fixed_repos, ["status", "diff", "log", "show", "rev-parse", "fetch"]], decision = "allow")
prefix_rule(pattern = ["git", "-C", fixed_repos, ["add", "commit", "merge", "push"]], decision = "prompt")
'''
        return body
    if role in {"implementer", "doc-curator"}:
        body = read + '''prefix_rule(pattern = ["git", ["add", "commit", "push"]], decision = "allow")
prefix_rule(pattern = ["git", ["merge", "rebase", "cherry-pick", "tag"]], decision = "forbidden")
prefix_rule(pattern = ["role-cli-workflow", "git", ["plan", "approve", "execute"]], decision = "forbidden")
prefix_rule(pattern = ["role-cli-workflow", "workflow"], decision = "forbidden")
'''
        for branch in config.protected_branches:
            body += f'prefix_rule(pattern = ["git", "push", "origin", {_toml_string(branch)}], decision = "forbidden")\n'
        return body
    return read + '''prefix_rule(pattern = ["git", ["add", "commit", "push", "merge", "rebase", "cherry-pick", "tag"]], decision = "forbidden")
prefix_rule(pattern = ["role-cli-workflow", "git", ["plan", "approve", "execute"]], decision = "forbidden")
prefix_rule(pattern = ["role-cli-workflow", "workflow"], decision = "forbidden")
prefix_rule(pattern = ["git", "branch", ["-d", "-D", "--delete"]], decision = "forbidden")
prefix_rule(pattern = ["git", "remote", ["add", "remove", "set-url", "rename"]], decision = "forbidden")
'''


def _role_config(config: ProjectConfig, role: str) -> str:
    sandbox = "workspace-write" if role in {"supervisor", "implementer", "evaluator", "doc-curator"} else "read-only"
    approval = "on-request" if role == "supervisor" else "never"
    text = f'developer_instructions = "ROLE_CLI_WORKFLOW_CONFIG_PROBE:{role}"\nsandbox_mode = "{sandbox}"\napproval_policy = "{approval}"\n\n[features]\nmulti_agent = false\nmemories = false\n\n[sandbox_workspace_write]\nnetwork_access = true\n'
    if role == "supervisor":
        text += f'writable_roots = [{_toml_string(str(config.root))}]\n'
    return text


def _ensure_exclude(repo: Path) -> None:
    result = git(repo, "rev-parse", "--git-path", "info/exclude")
    exclude = Path(result.stdout.strip())
    if not exclude.is_absolute():
        exclude = repo / exclude
    exclude = exclude.resolve()
    content = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if ".codex/" not in content.splitlines():
        if content and not content.endswith("\n"):
            content += "\n"
        _atomic_text(exclude, content + ".codex/\n", 0o644)


def _deploy_codex(config: ProjectConfig, role: str) -> None:
    canonical = config.bridge_root / "config" / "codex_roles"
    role_root = canonical / role
    _atomic_text(canonical / "common" / "rules" / "destructive-git.rules", _common_rules())
    _atomic_text(role_root / "config.toml", _role_config(config, role))
    _atomic_text(role_root / "rules" / "git.rules", _role_rules(config, role))
    repo = config.repo(role)
    tracked = git(repo, "ls-files", "--", ".codex").stdout.strip()
    if tracked:
        raise BootstrapError(f"tracked .codex content is forbidden in {repo}")
    _ensure_exclude(repo)
    deployed = repo / ".codex"
    _atomic_text(deployed / "config.toml", _role_config(config, role))
    _atomic_text(deployed / "rules" / "workflow-common.rules", _common_rules())
    _atomic_text(deployed / "rules" / "workflow-role.rules", _role_rules(config, role))


def _role_script(config: ProjectConfig, role: str) -> str:
    return "#!/usr/bin/env bash\nset -euo pipefail\nexec " + " ".join(
        shlex.quote(item) for item in (sys.executable, "-m", "role_cli_workflow.cli", "_role-launch", str(config.root), role)
    ) + "\n"


def _server_script(config: ProjectConfig) -> str:
    return (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + "export ROLE_CLI_WORKFLOW_PROJECT_ROOT=" + shlex.quote(str(config.root)) + "\n"
        + "exec " + " ".join(shlex.quote(item) for item in (sys.executable, "-m", "role_cli_workflow.bridge.server")) + "\n"
    )


def sync_project(root: str | Path) -> list[str]:
    config = load_project(root)
    changed: list[str] = []
    for path in (config.bridge_root, config.shared / "roles", config.workflow_root, config.shared / "scripts", config.runtime_root):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    roles_toml = "".join(
        f'[roles.{role}]\nsession = {_toml_string(config.session)}\nwindow = {_toml_string(role)}\nworking_directory = {_toml_string(str(config.repo(role)))}\n\n'
        for role in ROLES
    )
    if _atomic_text(config.bridge_root / "config" / "roles.toml", roles_toml):
        changed.append("role map")
    tmux_conf = _template_text("project/tmux.conf")
    _atomic_text(config.bridge_root / "config" / "tmux.conf", tmux_conf)
    for role in ROLES:
        if config.cli_provider == "codex":
            _deploy_codex(config, role)
        source = _template_role(role)
        _atomic_text(config.shared / "roles" / f"{role}.md", source)
        script = config.shared / "scripts" / f"run_{role.replace('-', '_')}.sh"
        _atomic_text(script, _role_script(config, role), 0o700)
    _atomic_text(config.shared / "scripts" / "run_bridge_server.sh", _server_script(config), 0o700)
    for name, text in {
        "current_task.md": _template_text("workflow/current_task.md"),
        "decision_log.md": _template_text("workflow/decision_log.md"),
    }.items():
        path = config.workflow_root / name
        if not path.exists():
            _atomic_text(path, text)
    version = (config.metadata_dir / "VERSION").read_text(encoding="utf-8").strip()
    warnings = [] if version == TEMPLATE_VERSION else [f"TEMPLATE_VERSION_MISMATCH:{version}->{TEMPLATE_VERSION}"]
    return warnings + changed


def _template_role(role: str) -> str:
    if role not in ROLES:
        raise BootstrapError("invalid fixed role template")
    role_text = _template_text(f"roles/{role}.md").rstrip()
    long_command = _template_text("roles/long-command-protocol.md").strip()
    return f"{role_text}\n\n{long_command}\n"
