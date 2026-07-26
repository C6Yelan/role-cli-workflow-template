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

After the template is published:

```bash
uv tool install "git+https://github.com/<owner>/<repository>.git"
```

## Prepare a project

The project root is a container directory. Its `main/` child must already be a
Git repository:

```bash
mkdir -p ~/projects/NewProject
git clone <repo-url> ~/projects/NewProject/main

codex-role-workflow init ~/projects/NewProject
codex-role-workflow doctor ~/projects/NewProject
codex-role-workflow open ~/projects/NewProject
codex-role-workflow attach ~/projects/NewProject
```

`init` shows the planned branches and worktrees before asking for confirmation.
It does not commit, push, reset, clean, or overwrite `main`.

## Normal operation

After attaching, communicate only with the Supervisor. The other five windows
are fixed Worker roles and receive tasks through the role bridge.

Useful commands:

```bash
codex-role-workflow status ~/projects/NewProject
codex-role-workflow verify ~/projects/NewProject
codex-role-workflow sync ~/projects/NewProject
codex-role-workflow stop ~/projects/NewProject
```

- `status` reads durable runtime state; it does not parse terminal content.
- `verify` checks the fixed live panes without dispatching product work.
- `sync` redeploys template-owned files while preserving task history.
- `stop` stops tmux only; worktrees, task records, and reports remain.

Next: [Configuration](configuration.md) ·
[Operations and recovery](operations-and-recovery.md)
