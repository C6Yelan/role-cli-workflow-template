# Role CLI Workflow

A small Linux/WSL template for six ordinary roles: Supervisor, Explorer, Implementer, Evaluator, Reviewer, and Doc Curator. TaskStore JSON is runtime truth. Markdown is a best-effort projection; tmux panes, wakeups, and callbacks are transport only.

## Routing

- `DIRECT`: Supervisor only, for read-only, workflow infrastructure, documentation/metadata, or trivial reversible non-behavioral work.
- `VERIFY`: Implementer → Evaluator → Supervisor. This is the normal delegated product-development path.
- `REVIEW`: optional Explorer → Implementer → Evaluator → Reviewer → Supervisor.
- Doc Curator is inserted only when documentation is a real deliverable or maintained public/configuration/runbook material changed.

There is no high-risk profile. `REVIEW` supplies additional engineering review; the genuinely irreversible or external action still requires an exact action-level approval.

## Candidate handoff

On writer assignment the bridge verifies the input SHA is the current configured base and makes that exact object available from the local Supervisor repo without switching or cleaning writer files. Implementer creates `feature/<task>` from it, commits locally, and reports the full Git SHA. The bridge validates branch, expected base ancestry, exact HEAD, clean handoff, configured private paths, and high-confidence secret material. Evaluator and Reviewer use clean isolated worktrees detached at that exact SHA. New commits naturally make old evidence stale.

No candidate-freeze file or per-file commit transaction exists. Local candidate add/commit needs no user approval.

## Durable task model

New records live under `shared_workspace/runtime/taskstore-v2/` and contain only task identity, immutable contract hash, input/produced SHA, result hash, notice state, lifecycle state, and timestamps. Lifecycle values are:

```text
DISPATCHED → RUNNING → RESULT_READY → ACCEPTED
                       ↘ BLOCKED
Any non-terminal task may be CANCELLED.
```

Focused rework keeps the same immutable contract and task ID, with a new round and nonce. A material objective or acceptance change creates a new task. Context authorization is ref-level and accepts multiple `task:*` and `decision:*` refs; `summary` and `full` are views, not separate authority.

Legacy authority, contract-freeze, semantic-repair, candidate-freeze, control-state, and old task metadata may remain as history. New TaskStore records never parse them as gates.

## Recovery and safety

Pane recreation, runtime restart, wake failure, callback failure, stale Markdown, uncertain telemetry, and unavailable optional host integration do not invalidate tasks. Retry dispatch/callback using the same task identity; regenerate projections when convenient.

Hard rejection remains for wrong workflow/task/role/round/nonce, overlapping writers, wrong or stale candidate SHA, dirty validation worktrees, private paths, high-confidence secrets, destructive or force Git, unauthorized protected-branch writes, non-fast-forward publication, exact integration drift, and missing user approval.

## Git policy

Projects configure:

```toml
[git]
base_branch = "dev"
feature_branch_pattern = "feature/*"
protected_branches = ["dev", "main"]
integration_mode = "approved_transaction"
```

Workers never merge or push protected branches. Protected integration uses a compact exact plan with source branch/SHA, target branch/starting SHA, remote/ref, merge method, operations, and `force_allowed = false`. One explicit approval naming that plan covers its displayed ordered operations. Any material drift invalidates approval.

## Commands

```bash
role-cli-workflow init ~/projects/MyProject --yes
role-cli-workflow sync ~/projects/MyProject
role-cli-workflow doctor ~/projects/MyProject
role-cli-workflow open ~/projects/MyProject
role-cli-workflow verify ~/projects/MyProject
role-cli-workflow status ~/projects/MyProject
role-cli-workflow stop ~/projects/MyProject
role-cli-workflow route VERIFY
```

Protected integration:

```bash
role-cli-workflow git plan-integration ~/projects/MyProject tx-123 \
  --workflow-id wf-123 --task-id task-123 \
  --source-branch feature/example --target-branch dev \
  --remote origin --destination-ref refs/heads/dev --merge-method ff-only --push
role-cli-workflow git approve ~/projects/MyProject tx-123 \
  --approval-summary "approve exact tx-123"
role-cli-workflow git execute ~/projects/MyProject tx-123
```

`init` expects the canonical Git repository at `main/` and creates isolated role worktrees without resetting existing work. `sync` preserves runtime history. Configure private paths and project commands in `.role-cli-workflow/project.toml` before use.
