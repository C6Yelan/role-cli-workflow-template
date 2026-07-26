# Getting started

## Requirements

- Linux or WSL
- Python 3.12
- `uv`, Git, and tmux
- An installed and authenticated AI CLI with MCP support

## Install

From a local checkout:

```bash
uv tool install /path/to/role-cli-workflow-template
```

From GitHub:

```bash
uv tool install "git+https://github.com/C6Yelan/role-cli-workflow-template.git"
```

## Prepare a project

The project root is a container directory. Its `main/` child must already be a
Git repository:

```bash
mkdir -p ~/projects/NewProject
git clone <repo-url> ~/projects/NewProject/main

role-cli-workflow init ~/projects/NewProject
role-cli-workflow doctor ~/projects/NewProject
role-cli-workflow open ~/projects/NewProject
role-cli-workflow attach ~/projects/NewProject
```

`init` shows the planned branches and worktrees before asking for confirmation.
It does not commit, push, reset, clean, or overwrite `main`.

## Normal operation

After attaching, communicate only with the Supervisor. The other five windows
are fixed Worker roles and receive tasks through the role bridge.

Useful commands:

```bash
role-cli-workflow status ~/projects/NewProject
role-cli-workflow verify ~/projects/NewProject
role-cli-workflow sync ~/projects/NewProject
role-cli-workflow stop ~/projects/NewProject
```

- `status` reads durable runtime state and shows branch, HEAD, and clean/dirty
  counts for each fixed worktree. Dirty state is informational and is never
  cleaned or changed by this command.
- `verify` checks the fixed live panes without dispatching product work.
- `sync` redeploys template-owned files while preserving task history.
- `stop` stops tmux only; worktrees, task records, and reports remain.

Next: [Configuration](configuration.md) ·
[Operations and recovery](operations-and-recovery.md)
