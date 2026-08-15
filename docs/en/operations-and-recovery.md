# Operations and recovery

TaskStore v2 survives `stop`/`open`; neither lifecycle command cancels tasks. Pane IDs, bridge process IDs, and callback delivery are never task authority.

- `WAKEUP_PENDING`: fix/recreate the role pane and call `retry_dispatch` for the same task.
- `CALLBACK_PENDING`: call `retry_callback`; the result remains valid.
- `PROJECTION_WARNING`: regenerate Markdown later; inspect TaskStore/API now.
- stale socket, clipboard, WSL mount, or optional host integration: doctor warning/degraded mode, not task invalidation.
- dirty validation worktree: hard stop without reset/clean; preserve and resolve local work explicitly.

Malformed legacy files are warnings because TaskStore v2 does not parse them. A malformed active v2 record remains a hard error.
