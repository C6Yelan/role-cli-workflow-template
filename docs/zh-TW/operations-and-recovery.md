# 操作與 recovery

TaskStore v2 會跨 `stop`／`open` 保留；兩者都不取消 task。Pane ID、bridge process ID、callback delivery 永遠不是 task authority。

- `WAKEUP_PENDING`：修復／重建 pane，對同 task 呼叫 `retry_dispatch`。
- `CALLBACK_PENDING`：呼叫 `retry_callback`，result 仍有效。
- `PROJECTION_WARNING`：之後重建 Markdown；目前以 TaskStore/API 為準。
- stale socket、clipboard、WSL mount 或 optional host integration：doctor warning／degraded mode，不 invalidate task。
- dirty validation worktree：不 reset/clean，保留 local work 並明確處理。

Malformed legacy files 只 warning；malformed active v2 record 才 hard error。
