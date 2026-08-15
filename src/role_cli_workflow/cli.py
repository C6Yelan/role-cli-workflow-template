"""Command-line interface for the six-role workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .bootstrap import BootstrapError, init_project, sync_project
from .config import ROLES, ProjectConfig, load_project
from .doctor import print_checks, run_doctor
from .git_transaction import GitTransactionError, GitTransactionStore
from .lifecycle import LifecycleError, attach_workflow, open_workflow, status_workflow, stop_workflow, verify_workflow
from .project import ProjectError
from .routing import EXECUTION_PROFILES, route_roles


def _bridge_server(root: str, role: str | None = None) -> int:
    os.environ["ROLE_CLI_WORKFLOW_PROJECT_ROOT"] = str(Path(root).expanduser().resolve())
    if role:
        os.environ["ROLE_CLI_WORKFLOW_ROLE"] = role
    from .bridge.server import main as server_main
    server_main()
    return 0


SUPERVISOR_TOOLS = (
    "list_roles", "assign_task", "cancel_task", "retry_dispatch", "retry_callback",
    "get_task_result", "accept_task", "send_rework", "record_decision",
)
WORKER_TOOLS = ("get_current_task", "get_context", "submit_result", "report_blocked")


def _expand_arg(value: str, substitutions: dict[str, str]) -> str:
    for name, replacement in substitutions.items():
        value = value.replace("{" + name + "}", replacement)
    return value


def _launch_cli(
    config: ProjectConfig,
    *,
    identity: str,
    working_directory: Path,
    repo: Path,
    instructions: Path,
    tools: tuple[str, ...],
    model: str | None,
    reasoning_effort: str | None,
    identity_args: tuple[str, ...],
) -> int:
    binary = shutil.which(config.cli_command)
    if not binary:
        raise LifecycleError(f"configured CLI is unavailable: {config.cli_command}")
    bridge_command = config.shared / "scripts" / "run_bridge_server.sh"
    substitutions = {
        "project_root": str(config.root), "repo": str(repo), "role": identity,
        "role_instructions": str(instructions), "bridge_command": str(bridge_command),
        "bridge_cwd": str(config.bridge_root), "enabled_tools": ",".join(tools),
        "model": model or "", "reasoning_effort": reasoning_effort or "",
    }

    def expand(items: tuple[str, ...]) -> list[str]:
        if model is None and any("{model}" in item for item in items):
            raise LifecycleError(f"cli model is required by configured arguments for: {identity}")
        if reasoning_effort is None and any("{reasoning_effort}" in item for item in items):
            raise LifecycleError(f"cli reasoning_effort is required by configured arguments for: {identity}")
        return [_expand_arg(item, substitutions) for item in items]

    os.environ.update({
        "ROLE_CLI_WORKFLOW_PROJECT_ROOT": str(config.root),
        "ROLE_CLI_WORKFLOW_REPO": str(repo),
        "ROLE_CLI_WORKFLOW_ROLE": identity,
        "ROLE_CLI_WORKFLOW_ROLE_INSTRUCTIONS": str(instructions),
        "ROLE_CLI_WORKFLOW_MCP_COMMAND": str(bridge_command),
        "ROLE_CLI_WORKFLOW_MCP_CWD": str(config.bridge_root),
        "ROLE_CLI_WORKFLOW_MCP_ENABLED_TOOLS": ",".join(tools),
    })
    if config.cli_provider == "generic":
        args = [binary, *expand(config.cli_args), *expand(identity_args)]
        os.chdir(working_directory)
        os.execvpe(binary, args, os.environ)
        return 0
    from .bridge.security import toml_instruction_override
    enabled = "[" + ",".join(f'"{item}"' for item in tools) + "]"
    args = [binary, *expand(config.cli_args), *expand(identity_args)]
    if model:
        args += ["-m", model]
    if reasoning_effort:
        args += ["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"]
    args += ["--strict-config", "--no-alt-screen", "-c", toml_instruction_override(identity, config.root)]
    for setting in ("multi_agent", "goals", "memories", "hooks", "plugins", "apps", "remote_plugin", "skill_mcp_dependency_install"):
        args += ["-c", f"features.{setting}=false"]
    args += [
        "-c", f'mcp_servers.role-cli-workflow.command="{bridge_command}"',
        "-c", "mcp_servers.role-cli-workflow.args=[]",
        "-c", "mcp_servers.role-cli-workflow.enabled=true",
        "-c", "mcp_servers.role-cli-workflow.required=true",
        "-c", 'mcp_servers.role-cli-workflow.startup_timeout_sec=20.0',
        "-c", 'mcp_servers.role-cli-workflow.tool_timeout_sec=55.0',
        "-c", f'mcp_servers.role-cli-workflow.cwd="{config.bridge_root}"',
        "-c", f'mcp_servers.role-cli-workflow.env.ROLE_CLI_WORKFLOW_PROJECT_ROOT="{config.root}"',
        "-c", f'mcp_servers.role-cli-workflow.env.ROLE_CLI_WORKFLOW_ROLE="{identity}"',
        "-c", f"mcp_servers.role-cli-workflow.enabled_tools={enabled}",
    ]
    os.chdir(working_directory)
    os.execvpe(binary, args, os.environ)
    return 0


def _role_launch(root: str, role: str) -> int:
    config = load_project(root)
    if role not in ROLES:
        raise LifecycleError("invalid fixed role")
    return _launch_cli(
        config, identity=role, working_directory=config.repo(role), repo=config.repo(role),
        instructions=config.shared / "roles" / f"{role}.md",
        tools=SUPERVISOR_TOOLS if role == "supervisor" else WORKER_TOOLS,
        model=config.model_for(role), reasoning_effort=config.reasoning_effort_for(role),
        identity_args=config.role_cli_args.get(role, ()),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="role-cli-workflow")
    sub = result.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("project_root"); init.add_argument("--yes", action="store_true")
    for name in ("sync", "doctor", "open", "verify", "attach", "status", "stop"):
        command = sub.add_parser(name); command.add_argument("project_root")
    bridge = sub.add_parser("_bridge-server"); bridge.add_argument("project_root")
    role = sub.add_parser("_role-launch"); role.add_argument("project_root"); role.add_argument("role", choices=ROLES)
    route = sub.add_parser("route"); route.add_argument("profile", choices=EXECUTION_PROFILES)
    route.add_argument("--needs-exploration", action="store_true"); route.add_argument("--needs-docs", action="store_true")
    git_command = sub.add_parser("git")
    git_sub = git_command.add_subparsers(dest="git_command", required=True)
    plan = git_sub.add_parser("plan-integration")
    plan.add_argument("project_root"); plan.add_argument("transaction_id")
    plan.add_argument("--workflow-id", required=True); plan.add_argument("--task-id", required=True)
    plan.add_argument("--source-branch", required=True); plan.add_argument("--target-branch", required=True)
    plan.add_argument("--remote", default="origin"); plan.add_argument("--destination-ref", required=True)
    plan.add_argument("--merge-method", choices=("ff-only", "no-ff"), default="ff-only")
    plan.add_argument("--push", action="store_true")
    approve = git_sub.add_parser("approve"); approve.add_argument("project_root"); approve.add_argument("transaction_id"); approve.add_argument("--approval-summary", required=True)
    for name in ("execute", "show"):
        command = git_sub.add_parser(name); command.add_argument("project_root"); command.add_argument("transaction_id")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "init":
            warnings = init_project(args.project_root, assume_yes=args.yes)
            print("Initialized fixed six-role workflow.")
            for warning in warnings: print(f"WARNING {warning}")
            return 0
        if args.command == "sync":
            for item in sync_project(args.project_root): print(f"WARNING {item}" if item.startswith("TEMPLATE_") else f"UPDATED {item}")
            return 0
        if args.command == "doctor": return print_checks(run_doctor(args.project_root))
        if args.command == "open": open_workflow(args.project_root); return 0
        if args.command == "verify": return verify_workflow(args.project_root)
        if args.command == "attach": attach_workflow(args.project_root); return 0
        if args.command == "status": return status_workflow(args.project_root)
        if args.command == "stop": stop_workflow(args.project_root); return 0
        if args.command == "_bridge-server": return _bridge_server(args.project_root)
        if args.command == "_role-launch": return _role_launch(args.project_root, args.role)
        if args.command == "route":
            print(" -> ".join(route_roles(args.profile, needs_exploration=args.needs_exploration, needs_docs=args.needs_docs)))
            return 0
        if args.command == "git":
            store = GitTransactionStore(load_project(args.project_root))
            if args.git_command == "plan-integration":
                operations = ["merge", *( ["push"] if args.push else [])]
                payload = store.create_integration_plan(
                    transaction_id=args.transaction_id, workflow_id=args.workflow_id,
                    task_id=args.task_id, operations=operations,
                    source_branch=args.source_branch, target_branch=args.target_branch,
                    merge_method=args.merge_method, remote=args.remote,
                    destination_ref=args.destination_ref,
                )
            elif args.git_command == "approve": payload = store.approve(args.transaction_id, args.approval_summary)
            elif args.git_command == "execute": payload = store.execute(args.transaction_id)
            else: payload = store.show(args.transaction_id)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    except (BootstrapError, LifecycleError, ProjectError, GitTransactionError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
