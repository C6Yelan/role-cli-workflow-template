# Role CLI Workflow Template

A reusable Linux/WSL project template for one fixed six-role AI CLI workflow: Supervisor, Explorer, Implementer, Evaluator, Reviewer, and Doc Curator. Supervisor is the only user-facing role. TaskStore JSON is runtime truth; Markdown is a deterministic human-readable projection.

Codex has a built-in provider. Other AI CLI tools can be connected through the generic adapter contract described below. This is a community project and is not an official OpenAI product.

## Documentation

- [English](docs/en/README.md)
- [繁體中文](docs/zh-TW/README.md)

## Requirements

- Linux or WSL
- Python 3.12, `uv`, Git, and tmux
- An installed and authenticated AI CLI with MCP support, either through the built-in Codex provider or a custom adapter

## Install and start

```bash
uv tool install /path/to/role-cli-workflow-template

mkdir -p ~/projects/NewProject
git clone <repo-url> ~/projects/NewProject/main

role-cli-workflow init ~/projects/NewProject
role-cli-workflow doctor ~/projects/NewProject
role-cli-workflow open ~/projects/NewProject
role-cli-workflow attach ~/projects/NewProject
```

It can also be installed directly from GitHub with `uv tool install "git+https://github.com/C6Yelan/role-cli-workflow-template.git"`.

`init` displays the base branch, five role branches, worktree destinations, and main working-tree cleanliness before asking for `yes`. It never overwrites `main`, commits, pushes, resets, or cleans. For noninteractive isolated tests only, `init --yes` accepts the displayed local worktree creation.

## Standard layout

```text
<project-root>/
├── main/                  existing Git repository; Supervisor cwd
├── explorer/              linked worktree
├── implementer/           linked worktree
├── evaluator/             linked worktree
├── reviewer/              linked worktree
├── doc-curator/           linked worktree
├── shared_workspace/
│   ├── role_bridge/       canonical role maps and provider policy
│   ├── roles/             canonical role instructions
│   ├── workflow/          human-readable projections and task documents
│   ├── scripts/           fixed role launchers
│   └── runtime/           TaskStore and fixed tmux socket
└── .role-cli-workflow/
    ├── project.toml
    ├── project_instructions.md
    └── VERSION
```

The default role branches are `workflow/<role>`. To override one, add (for example) `implementer_branch = "feature/backend"` under `[git]`, then run `sync` and `doctor`. Existing branches are never reset or silently remapped.

## Commands

- `init`: inspect `main`, confirm and create five linked worktrees, then install the project instance. Does not start the configured CLI.
- `sync`: deterministically redeploy canonical role instructions, configs, rules, and fixed launchers. Runtime and workflow history are preserved.
- `doctor`: report PASS/WARNING/FAIL for layout, worktrees, configured CLI, optional login probe, tmux, uv, Python, role deployment, provider-specific policy checks, and six real stdio MCP handshakes.
- `open`: sync, require a doctor result without FAIL, then create the fixed six-window tmux server.
- `verify`: verify the live fixed panes without reading TUI content or running a product task.
- `attach`: attach directly to the Supervisor window.
- `status`: display runtime state plus latest Worker execution and callback metadata without parsing panes.
- `stop`: stop only the fixed tmux server and remove an exact stale socket. Worktrees, tasks, results, and reports remain.

After `attach`, use the mouse wheel for history, click the bottom window labels to inspect roles, `Ctrl+b [` for copy mode, `q` or `Esc` to leave copy mode, and `Ctrl+b d` to detach. Avoid typing in automated Worker composers while a task is active.

## Configuration

`.role-cli-workflow/project.toml` contains project identity, CLI provider settings, optional per-role launch overrides, base/branch policy, private paths, and optional `test`, `lint`, `build`, and `format` commands. Empty commands produce doctor warnings; init never guesses or runs package scripts. The workflow does not pin or require one Codex version.

Model and reasoning settings use one provider-neutral surface. Values under `[cli]` are defaults for every role; `[cli.roles.<role>]` overrides only that role. When a field is omitted at both levels, the launcher leaves the choice to the configured CLI. Values are passed through without a template-side allowlist, so model availability remains the responsibility of the installed CLI and account:

```toml
[cli]
provider = "codex"
command = "codex"
model = "your-default-model"
reasoning_effort = "medium"

[cli.roles.supervisor]
model = "your-supervisor-model"

[cli.roles.implementer]
model = "your-implementation-model"
reasoning_effort = "high"
```

In this example, roles without an override use `your-default-model`; Supervisor and Implementer use their role-specific models. The same configuration works with the generic provider.

### Other CLI providers

Set `provider = "generic"` and point `command` at the CLI or a small provider-specific adapter:

```toml
[cli]
provider = "generic"
command = "my-cli-workflow-adapter"
model = "your-default-model"
reasoning_effort = "medium"
args = [
  "--role", "{role}",
  "--repo", "{repo}",
  "--model", "{model}",
  "--reasoning-effort", "{reasoning_effort}",
  "--instructions", "{role_instructions}",
  "--mcp-command", "{bridge_command}",
  "--mcp-cwd", "{bridge_cwd}",
  "--enabled-tools", "{enabled_tools}",
]
version_args = ["--version"]
login_check_args = []
```

Supported placeholders are `{project_root}`, `{repo}`, `{role}`, `{model}`, `{reasoning_effort}`, `{role_instructions}`, `{bridge_command}`, `{bridge_cwd}`, and `{enabled_tools}`. The same values are exported as `ROLE_CLI_WORKFLOW_PROJECT_ROOT`, `ROLE_CLI_WORKFLOW_REPO`, `ROLE_CLI_WORKFLOW_ROLE`, `ROLE_CLI_WORKFLOW_MODEL`, `ROLE_CLI_WORKFLOW_REASONING_EFFORT`, `ROLE_CLI_WORKFLOW_ROLE_INSTRUCTIONS`, `ROLE_CLI_WORKFLOW_MCP_COMMAND`, `ROLE_CLI_WORKFLOW_MCP_CWD`, and `ROLE_CLI_WORKFLOW_MCP_ENABLED_TOOLS`. Model-related variables are omitted when no model or reasoning effort is configured; using their placeholders without a configured value is an error.

The adapter must load the role instructions, register the supplied stdio MCP server, restrict tools to the supplied role matrix, and implement the intended sandbox, approval, trust, authentication, and Git policy. Those controls are provider-specific and cannot be inferred safely from one universal command line. See `examples/generic/project.toml`.

`.role-cli-workflow/project_instructions.md` contains conservative project-specific candidates and TODOs. Fixed Git, MCP, safety, role, and result rules remain canonical in `shared_workspace/roles` and `shared_workspace/role_bridge/config`.

With the Codex provider, each worktree receives a local `.codex/config.toml` and `.codex/rules/` deployment. `sync` adds `.codex/` to Git's local `info/exclude`; it does not change product `.gitignore`, `CODEX_HOME`, global Codex config/rules, authentication, history, sessions, or logs. Generic providers receive no guessed provider-local configuration.

## Roles and Git authority

| Role | Intended sandbox / approval | Git authority |
| --- | --- | --- |
| Supervisor | workspace-write over project root / on-request | read, fetch, pull `--ff-only`; direct add, commit, merge, push prompt; an exact approved task-branch transaction may combine add/commit/push once; only role allowed to merge |
| Implementer | workspace-write / never | add, commit, push own task branch; protected push and merge forbidden |
| Doc Curator | workspace-write / never | add, commit, push approved docs branch; protected push and merge forbidden |
| Explorer | read-only / never | read, fetch, pull `--ff-only`; publication and merge forbidden |
| Evaluator | workspace-write / never | read, fetch, pull `--ff-only`; writable output is for validation artifacts, not Git publication |
| Reviewer | read-only / never | read, fetch, pull `--ff-only`; publication and merge forbidden |

All role instructions forbid destructive reset/clean, force push, forced branch/worktree deletion, repository-wide overwrite, and shell command-string wrappers. Codex receives generated execpolicy rules. Generic providers must enforce equivalent boundaries in their adapter or CLI configuration. Rules are a command boundary, not a substitute for user review.

## Workflow data

Task Contracts contain the current objective, deliverables, acceptance criteria, task-only constraints, and authorized context refs. Workers pull tasks and context through MCP and return structured Result Envelopes. Evaluator and Reviewer may run in parallel. BLOCKED tasks resume through `send_rework` with the same task ID and a new round/nonce. Full upstream results are not inserted into downstream prompts.

Task state is durable even when a tmux wakeup fails. `WAKEUP_PENDING` means Supervisor should use `retry_dispatch`, not create a duplicate task. A running task that the user has explicitly abandoned can be released with `cancel_task`, after which a replacement receives a new task ID. Result and BLOCKED callbacks identify their exact workflow, task, role, and status and remain `PENDING` until delivered; Supervisor can use `retry_callback` and must read a BLOCKED result with `get_task_result` before explaining the blocker and required next action to the user.

Long-running child commands keep their exact live session or cell ID until an explicit exit code is observed. Intermediate output is not completion, and a second writer or retry must not start while the original session remains unresolved.

Runtime JSON under `shared_workspace/runtime` is the source of truth. `shared_workspace/workflow/current_task.md`, task reports, indexes, handoffs, and decision log are projections only.

Each workflow may add `tasks/<workflow-id>/data/preflight.json` plus a short `preflight.md` projection. `STANDARD` is the default, including private, rebuildable artifacts used by one internal consumer. Ordinary STANDARD rework does not require the contract-sensitive semantic checkpoint. `CONTRACT_SENSITIVE` requires a stated public, migration, cross-system, irreversible, security/provider, SHA-locked release, or regulatory risk. It freezes only traceable `required_now` invariants and their Completion Gate; optional hardening and deferred ideas remain advisory. Reviewer reports separate correctness and cumulative proportionality verdicts. Fixed workflow CLI commands record that assessment, assess a semantic repair, and resolve a required/advisory/deferred classification. For CONTRACT_SENSITIVE work, `send_rework` requires the current `invariant_type` and a current resolved checkpoint after the first semantic repair; successful dispatch records its event and metric automatically. Effective PLAN, contract and approved decision revisions are projected once; superseded wording remains historical-only. Existing workflows without this metadata remain readable with a legacy authority warning. Event metadata still separates semantic revisions from mechanical, permission/context, stale-SHA, verification and Git-approval events without changing task round or nonce.

## Approved Git transactions

Direct Supervisor Git writes keep their existing per-command prompts. To request one approval for an exact add/commit/push subset, create a read-only plan and show it to the user:

```bash
role-cli-workflow git plan ~/projects/NewProject tx-001 \
  --workflow-id wf-001 --task-id task-001 --repo-id main \
  --operation add --operation commit --operation push \
  --file path/to/file.py --commit-message "Implement approved task"
```

After the user explicitly approves that displayed transaction, Supervisor records the approval and executes the fixed command:

```bash
role-cli-workflow git approve ~/projects/NewProject tx-001 \
  --approval-summary "User approved the displayed tx-001 plan"
role-cli-workflow git execute ~/projects/NewProject tx-001
```

The executor accepts no arbitrary repo path, shell command or extra Git argument. It revalidates branch, HEAD, exact files and content, message, remote/ref, sensitive paths and fast-forward safety. Scope drift invalidates the approval. Push retry resumes only after a saved `PUSH_FAILED`; it does not redo add or commit. Merge and integration-branch push always remain separate approvals.

Status output treats pane/process telemetry (`ALIVE`, `DOWN`, `UNKNOWN`), task execution, and latest MCP activity as independent facts. Normal callbacks use a compact workflow block; full role tables are reserved for explicit status requests, parallel validation, blocked/unavailable roles, telemetry anomalies, partial Git failures and final acceptance.

## Codex trust and troubleshooting

Every fixed worktree must be trusted in the active global Codex configuration before `open`. `doctor` stops with FAIL and prints the affected paths when trust is missing. Open each fixed repository with Codex and approve project trust using the normal Codex prompt, then rerun:

```bash
role-cli-workflow sync ~/projects/NewProject
role-cli-workflow doctor ~/projects/NewProject
```

Codex versions are not pinned or compared. Rerun doctor and the compatibility checks after upgrades. A stale exact socket is reported and safely replaced by `open` only when no fixed live session exists. `stop` is idempotent.

For a generic provider, `doctor` validates the configured executable, optional version/login probes, role launchers, and MCP handshakes. It emits a warning because provider-specific sandbox, approval, trust, and Git enforcement cannot be verified generically.

## Remove the workflow but keep main

First run `stop`. Preserve any role commits you need, then manually remove the five linked worktrees with normal Git worktree commands. Finally remove `shared_workspace/` and `.role-cli-workflow/`. The template never automates this destructive removal and never deletes `main`.

## License

MIT. See `LICENSE`.
