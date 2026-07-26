# Operations and recovery

Runtime JSON under `shared_workspace/runtime/` is the source of truth.
Markdown under `shared_workspace/workflow/` is a human-readable projection.
Use `role-cli-workflow status <project-root>` before deciding that a role or
task has failed.

## Common states

| State | Meaning | Action |
| --- | --- | --- |
| `WAKEUP_PENDING` | The task was saved, but the Worker wakeup failed. | Ask Supervisor to use `retry_dispatch` for the same task. Do not create a duplicate. |
| Callback `PENDING` | A result or blocker was saved, but Supervisor was not notified. | Ask Supervisor to use `retry_callback`. |
| `BLOCKED` | A Worker needs recovery, user input, or additional authority. | Supervisor reads the exact task with `get_task_result`, explains the blocker, then uses `send_rework` when recoverable. |
| `*_NO_RECENT_ACTIVITY` | No recent bridge activity was observed. | Inspect the role and live command first. This is a display warning, not automatic failure. |
| `task state is busy; retry` | Another operation held the task lock for the bounded wait period. | Retry once after the active operation finishes. |

Callbacks identify the exact workflow, task, role, and terminal status. A
`DELIVERED` callback means the wakeup text reached the Supervisor pane; it does
not independently prove that a user-facing report was completed.

## Abandoning a running task

Use `cancel_task` only after the user or Supervisor has confirmed that the task
is abandoned or stale. Cancellation releases the role for a new task, but it
does not terminate an external child process that may still be running.

## Long-running commands

When a command returns a live session or cell ID, preserve that exact ID and
poll the same session until it reports an explicit exit code. Intermediate or
empty output is not completion. Do not start a second writer while the original
session is unresolved.

## Runtime restart

```bash
role-cli-workflow stop ~/projects/NewProject
role-cli-workflow doctor ~/projects/NewProject
role-cli-workflow open ~/projects/NewProject
```

Stopping the tmux runtime preserves worktrees, task state, results, decisions,
and workflow documents. If `doctor` reports a FAIL, resolve that specific
failure before reopening.

Previous: [Configuration](configuration.md)
