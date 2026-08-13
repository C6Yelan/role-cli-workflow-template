"""Command-line interface for the fixed project workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .bootstrap import BootstrapError, init_project, sync_project
from .config import ESCALATION_CONTROLLER, ROLES, ProjectConfig, load_project
from .control import ControlError, ControlStore
from .doctor import print_checks, run_doctor
from .lifecycle import (
    LifecycleError,
    attach_workflow,
    is_live,
    open_workflow,
    role_processes_live,
    status_workflow,
    stop_workflow,
    verify_workflow,
)
from .project import ProjectError
from .git_transaction import GitTransactionError, GitTransactionStore
from .refinement import EXECUTION_PROFILES, RefinementError, WorkflowRefinementStore


def _bridge_server(root: str, role: str | None = None) -> int:
    os.environ["ROLE_CLI_WORKFLOW_PROJECT_ROOT"] = str(Path(root).expanduser().resolve())
    if role:
        os.environ["ROLE_CLI_WORKFLOW_ROLE"] = role
    from .bridge.server import main as server_main
    server_main()
    return 0


SUPERVISOR_TOOLS = (
    "list_roles", "get_control_state", "assign_task", "cancel_task",
    "retry_dispatch", "retry_callback", "get_task_result", "send_rework",
    "record_decision",
)
WORKER_TOOLS = ("get_current_task", "get_context", "submit_result", "report_blocked")
ESCALATION_TOOLS = (
    "list_roles", "get_control_state", "get_task_result", "record_decision",
    "release_control",
)


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
        "project_root": str(config.root),
        "repo": str(repo),
        "role": identity,
        "role_instructions": str(instructions),
        "bridge_command": str(bridge_command),
        "bridge_cwd": str(config.bridge_root),
        "enabled_tools": ",".join(tools),
        "model": model or "",
        "reasoning_effort": reasoning_effort or "",
    }

    def expand(items: tuple[str, ...]) -> list[str]:
        if model is None and any("{model}" in item for item in items):
            raise LifecycleError(f"cli model is required by configured arguments for: {identity}")
        if reasoning_effort is None and any("{reasoning_effort}" in item for item in items):
            raise LifecycleError(
                f"cli reasoning_effort is required by configured arguments for: {identity}"
            )
        return [_expand_arg(item, substitutions) for item in items]

    os.environ["ROLE_CLI_WORKFLOW_PROJECT_ROOT"] = str(config.root)
    os.environ["ROLE_CLI_WORKFLOW_ROLE"] = identity
    os.environ.update({
        "ROLE_CLI_WORKFLOW_PROJECT_ROOT": str(config.root),
        "ROLE_CLI_WORKFLOW_REPO": str(repo),
        "ROLE_CLI_WORKFLOW_ROLE": identity,
        "ROLE_CLI_WORKFLOW_ROLE_INSTRUCTIONS": str(instructions),
        "ROLE_CLI_WORKFLOW_MCP_COMMAND": str(bridge_command),
        "ROLE_CLI_WORKFLOW_MCP_CWD": str(config.bridge_root),
        "ROLE_CLI_WORKFLOW_MCP_ENABLED_TOOLS": ",".join(tools),
    })
    if model is not None:
        os.environ["ROLE_CLI_WORKFLOW_MODEL"] = model
    else:
        os.environ.pop("ROLE_CLI_WORKFLOW_MODEL", None)
    if reasoning_effort is not None:
        os.environ["ROLE_CLI_WORKFLOW_REASONING_EFFORT"] = reasoning_effort
    else:
        os.environ.pop("ROLE_CLI_WORKFLOW_REASONING_EFFORT", None)
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
    args += [
        "--strict-config", "--no-alt-screen", "-c",
        toml_instruction_override(identity, config.root),
    ]
    if identity == ESCALATION_CONTROLLER:
        args += ["--sandbox", "workspace-write", "--ask-for-approval", "on-request"]
    args += [
        "-c",
        f"features.multi_agent={'true' if identity == ESCALATION_CONTROLLER else 'false'}",
    ]
    for setting in ("goals", "memories", "hooks", "plugins", "apps", "remote_plugin", "skill_mcp_dependency_install"):
        args += ["-c", f"features.{setting}=false"]
    args += [
        "-c", f'mcp_servers.role-cli-workflow.command="{config.shared / "scripts" / "run_bridge_server.sh"}"',
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
        config,
        identity=role,
        working_directory=config.repo(role),
        repo=config.repo(role),
        instructions=config.shared / "roles" / f"{role}.md",
        tools=SUPERVISOR_TOOLS if role == "supervisor" else WORKER_TOOLS,
        model=config.model_for(role),
        reasoning_effort=config.reasoning_effort_for(role),
        identity_args=config.role_cli_args.get(role, ()),
    )


def _start_escalation(root: str, intervention_id: str, reason: str) -> int:
    config = load_project(root)
    if not config.escalation_enabled:
        raise LifecycleError("escalation controller is disabled")
    active_tmux = os.environ.get("TMUX", "").split(",", 1)[0]
    if active_tmux and Path(active_tmux).resolve() == config.socket.resolve():
        raise LifecycleError(
            "escalation controller must be started outside the fixed workflow tmux session"
        )
    store = ControlStore(config.runtime_root)
    store.acquire(intervention_id, reason)
    try:
        stop_workflow(config.root)
        if is_live(config) or role_processes_live(config):
            raise LifecycleError("fixed workflow runtime did not stop")
    except Exception as exc:
        raise LifecycleError(
            "fixed workflow runtime stop failed; escalation remains active"
        ) from exc
    return _launch_cli(
        config,
        identity=ESCALATION_CONTROLLER,
        working_directory=config.root,
        repo=config.root,
        instructions=config.shared / "controllers" / f"{ESCALATION_CONTROLLER}.md",
        tools=ESCALATION_TOOLS,
        model=config.escalation_model_for_launch(),
        reasoning_effort=config.escalation_reasoning_effort_for_launch(),
        identity_args=config.escalation_cli_args,
    )


def _expand_arg(value: str, substitutions: dict[str, str]) -> str:
    result = value
    for name, replacement in substitutions.items():
        result = result.replace("{" + name + "}", replacement)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="role-cli-workflow")
    sub = result.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("project_root"); init.add_argument("--yes", action="store_true")
    for name in ("sync", "doctor", "open", "verify", "attach", "status", "stop"):
        command = sub.add_parser(name); command.add_argument("project_root")
    bridge = sub.add_parser("_bridge-server"); bridge.add_argument("project_root")
    role = sub.add_parser("_role-launch"); role.add_argument("project_root"); role.add_argument("role", choices=ROLES)
    escalation = sub.add_parser("escalation")
    escalation_sub = escalation.add_subparsers(dest="escalation_command", required=True)
    escalation_start = escalation_sub.add_parser("start")
    escalation_start.add_argument("project_root")
    escalation_start.add_argument("--intervention-id", required=True)
    escalation_start.add_argument("--reason", required=True)
    escalation_status = escalation_sub.add_parser("status")
    escalation_status.add_argument("project_root")
    escalation_release = escalation_sub.add_parser("release")
    escalation_release.add_argument("project_root")
    escalation_release.add_argument("--intervention-id", required=True)
    escalation_release.add_argument("--summary", required=True)
    git_command = sub.add_parser("git")
    git_sub = git_command.add_subparsers(dest="git_command", required=True)
    git_plan = git_sub.add_parser("plan")
    git_plan.add_argument("project_root"); git_plan.add_argument("transaction_id")
    git_plan.add_argument("--workflow-id", required=True); git_plan.add_argument("--task-id", required=True)
    git_plan.add_argument("--repo-id", choices=("main", *ROLES[1:]), required=True)
    git_plan.add_argument(
        "--operation", action="append", choices=("add", "commit", "merge", "push"),
        required=True,
    )
    git_plan.add_argument("--file", action="append", default=[])
    git_plan.add_argument("--commit-message", default="")
    git_plan.add_argument("--remote", default="origin"); git_plan.add_argument("--remote-branch", default="")
    git_plan.add_argument("--source-branch", default="")
    git_plan.add_argument("--target-branch", default="")
    git_plan.add_argument("--merge-method", choices=("ff-only", "no-ff"), default="ff-only")
    git_plan.add_argument("--destination-ref", default="")
    git_approve = git_sub.add_parser("approve")
    git_approve.add_argument("project_root"); git_approve.add_argument("transaction_id")
    git_approve.add_argument("--approval-summary", required=True)
    for name in ("execute", "show"):
        command = git_sub.add_parser(name); command.add_argument("project_root"); command.add_argument("transaction_id")
    workflow = sub.add_parser("workflow")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    preflight = workflow_sub.add_parser("preflight")
    preflight.add_argument("project_root"); preflight.add_argument("workflow_id")
    preflight.add_argument(
        "--execution-profile", choices=EXECUTION_PROFILES, default="VERIFY"
    )
    preflight.add_argument("--execution-reason", default="")
    preflight.add_argument("--execution-trigger", action="append", default=[])
    preflight.add_argument("--target-role", choices=ROLES, required=True)
    preflight.add_argument("--base-sha", required=True); preflight.add_argument("--target-sha", required=True)
    preflight.add_argument("--working-tree-status", required=True)
    preflight.add_argument("--input", action="append", default=[]); preflight.add_argument("--output", action="append", default=[])
    preflight.add_argument("--toolchain", action="append", default=[])
    preflight.add_argument("--shared-access", choices=("NONE", "READ", "READ_WRITE"), default="READ")
    preflight.add_argument("--private-owner", default=""); preflight.add_argument("--private-publisher", default="")
    preflight.add_argument("--private-handoff", default=""); preflight.add_argument("--candidate", default=""); preflight.add_argument("--stable", default="")
    preflight.add_argument("--git-handoff", required=True); preflight.add_argument("--unresolved", action="append", default=[])
    freeze = workflow_sub.add_parser("freeze-contract")
    freeze.add_argument("project_root"); freeze.add_argument("workflow_id"); freeze.add_argument("--approval-summary", required=True)
    freeze.add_argument("--required-now-json", required=True)
    freeze.add_argument("--optional-hardening-json", default="{}")
    freeze.add_argument("--deferred-json", default="{}")
    approve_contract = workflow_sub.add_parser("approve-contract-change")
    approve_contract.add_argument("project_root"); approve_contract.add_argument("workflow_id"); approve_contract.add_argument("--approval-summary", required=True)
    authority = workflow_sub.add_parser("set-authority")
    authority.add_argument("project_root"); authority.add_argument("workflow_id")
    authority.add_argument("--plan-revision", required=True)
    authority.add_argument("--plan-projection-json", required=True)
    authority.add_argument("--contract-revision", type=int)
    authority.add_argument("--decision-json", action="append", default=[])
    review = workflow_sub.add_parser("review-assessment")
    review.add_argument("project_root"); review.add_argument("workflow_id")
    review.add_argument("--correctness", choices=("PASS", "FAIL"), required=True)
    review.add_argument("--proportionality", choices=("PROPORTIONATE", "OVERDESIGNED", "UNCERTAIN"), required=True)
    review.add_argument("--base-sha", required=True); review.add_argument("--candidate-sha", required=True)
    review.add_argument("--observation", action="append", default=[])
    semantic = workflow_sub.add_parser("assess-semantic-repair")
    semantic.add_argument("project_root"); semantic.add_argument("workflow_id")
    semantic.add_argument("--invariant-type", required=True)
    semantic.add_argument("--directly-required", action="store_true")
    semantic.add_argument("--real-risk", action="store_true")
    semantic.add_argument("--scope-proportionate", action="store_true")
    resolve = workflow_sub.add_parser("resolve-semantic-repair")
    resolve.add_argument("project_root"); resolve.add_argument("workflow_id")
    resolve.add_argument("--classification", choices=("required", "advisory", "deferred"), required=True)
    resolve.add_argument("--authority-ref", default=""); resolve.add_argument("--contract-revision", type=int)
    freeze_sha = workflow_sub.add_parser("freeze-sha")
    freeze_sha.add_argument("project_root"); freeze_sha.add_argument("workflow_id")
    freeze_sha.add_argument("--repo-id", choices=("main", *ROLES[1:]), required=True)
    freeze_sha.add_argument("--branch", required=True); freeze_sha.add_argument("--base-sha", required=True); freeze_sha.add_argument("--candidate-sha", required=True)
    freeze_sha.add_argument("--commit-message", required=True); freeze_sha.add_argument("--remote-ref", required=True); freeze_sha.add_argument("--working-tree-clean", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if any(item == "--profile" or item.startswith("--profile=") for item in arguments):
        print("ERROR: --profile was renamed to --execution-profile", file=sys.stderr)
        return 2
    args = parser().parse_args(arguments)
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
        if args.command == "escalation":
            config = load_project(args.project_root)
            if args.escalation_command == "start":
                return _start_escalation(
                    args.project_root, args.intervention_id, args.reason
                )
            store = ControlStore(config.runtime_root)
            if args.escalation_command == "release":
                if is_live(config) or role_processes_live(config):
                    raise LifecycleError(
                        "fixed workflow runtime must be stopped before escalation release"
                    )
                payload = store.release(args.intervention_id, args.summary).to_dict()
            else:
                payload = store.read().to_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "git":
            store = GitTransactionStore(load_project(args.project_root))
            if args.git_command == "plan":
                if "merge" in args.operation:
                    payload = store.create_integration_plan(
                        transaction_id=args.transaction_id,
                        workflow_id=args.workflow_id,
                        task_id=args.task_id,
                        repo_id=args.repo_id,
                        operations=args.operation,
                        source_branch=args.source_branch,
                        target_branch=args.target_branch,
                        merge_method=args.merge_method,
                        remote=args.remote,
                        destination_ref=args.destination_ref,
                    )
                else:
                    payload = store.create_plan(
                        transaction_id=args.transaction_id, workflow_id=args.workflow_id,
                        task_id=args.task_id, repo_id=args.repo_id,
                        operations=args.operation, explicit_files=args.file,
                        commit_message=args.commit_message, remote=args.remote,
                        remote_branch=args.remote_branch,
                    )
            elif args.git_command == "approve":
                payload = store.approve(args.transaction_id, args.approval_summary)
            elif args.git_command == "execute":
                payload = store.execute(args.transaction_id)
            else:
                payload = store.show(args.transaction_id)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "workflow":
            store = WorkflowRefinementStore(load_project(args.project_root))
            if args.workflow_command == "preflight":
                payload = store.create_preflight(
                    workflow_id=args.workflow_id, execution_profile=args.execution_profile,
                    execution_reason=args.execution_reason,
                    execution_triggers=args.execution_trigger,
                    target_role=args.target_role, base_sha=args.base_sha,
                    current_target_sha=args.target_sha,
                    working_tree_status=args.working_tree_status,
                    canonical_input_paths=args.input, canonical_output_paths=args.output,
                    dependency_toolchain=args.toolchain,
                    shared_workspace_access=args.shared_access,
                    private_artifact_owner=args.private_owner,
                    private_artifact_publisher=args.private_publisher,
                    private_artifact_handoff_path=args.private_handoff,
                    candidate_artifact_path=args.candidate,
                    stable_artifact_path=args.stable,
                    expected_git_handoff=args.git_handoff,
                    unresolved_items=args.unresolved,
                )
            elif args.workflow_command == "freeze-contract":
                payload = store.freeze_contract(
                    args.workflow_id,
                    {
                        "required_now": json.loads(args.required_now_json),
                        "optional_hardening": json.loads(args.optional_hardening_json),
                        "deferred": json.loads(args.deferred_json),
                    },
                    args.approval_summary,
                )
            elif args.workflow_command == "approve-contract-change":
                payload = store.approve_contract_change(args.workflow_id, args.approval_summary)
            elif args.workflow_command == "set-authority":
                payload = store.set_effective_authority(
                    args.workflow_id, plan_revision=args.plan_revision,
                    plan_projection=json.loads(args.plan_projection_json),
                    contract_revision=args.contract_revision,
                    approved_decisions=[json.loads(item) for item in args.decision_json],
                )
            elif args.workflow_command == "review-assessment":
                payload = store.record_reviewer_assessment(
                    args.workflow_id, correctness_verdict=args.correctness,
                    proportionality_verdict=args.proportionality,
                    base_sha=args.base_sha, final_candidate_sha=args.candidate_sha,
                    cumulative_observations=args.observation,
                )
            elif args.workflow_command == "assess-semantic-repair":
                payload = store.assess_semantic_repair(
                    args.workflow_id, invariant_type=args.invariant_type,
                    directly_required=args.directly_required,
                    real_trigger_or_unacceptable_risk=args.real_risk,
                    cumulative_scope_proportionate=args.scope_proportionate,
                )
            elif args.workflow_command == "resolve-semantic-repair":
                payload = store.resolve_semantic_repair(
                    args.workflow_id, classification=args.classification,
                    authority_ref=args.authority_ref,
                    contract_revision=args.contract_revision,
                )
            else:
                payload = store.freeze_final_candidate(
                    args.workflow_id, repo_id=args.repo_id, branch=args.branch,
                    base_sha=args.base_sha, final_candidate_sha=args.candidate_sha,
                    commit_message=args.commit_message, remote_ref=args.remote_ref,
                    working_tree_clean=args.working_tree_clean,
                )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    except (BootstrapError, ControlError, LifecycleError, ProjectError, RefinementError, GitTransactionError, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__": raise SystemExit(main())
