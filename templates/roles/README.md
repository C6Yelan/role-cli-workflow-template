# Role templates

The fixed six canonical role instruction files are rendered by `bootstrap.py` into each project's `shared_workspace/roles/` directory. The shared long-command lifecycle section is appended from `long-command-protocol.md` so it remains single-sourced. Project-only facts remain in `.role-cli-workflow/project_instructions.md`.

The optional on-demand escalation controller is maintained separately under `templates/controllers/`. It is not included in the fixed role map, worktrees, branches, or tmux windows.
