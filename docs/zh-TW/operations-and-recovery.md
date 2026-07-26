# 操作與異常恢復

`shared_workspace/runtime/` 內的 JSON 是 runtime source of truth；
`shared_workspace/workflow/` 內的 Markdown 是方便閱讀的投影。在判定角色或
任務失敗前，先執行 `codex-role-workflow status <project-root>`。

## 常見狀態

| 狀態 | 意義 | 處理方式 |
| --- | --- | --- |
| `WAKEUP_PENDING` | 任務已保存，但喚醒 Worker 失敗。 | 請 Supervisor 對同一任務使用 `retry_dispatch`，不要建立重複任務。 |
| Callback `PENDING` | 結果或 blocker 已保存，但尚未通知 Supervisor。 | 請 Supervisor 使用 `retry_callback`。 |
| `BLOCKED` | Worker 需要恢復、使用者輸入或額外授權。 | Supervisor 使用 `get_task_result` 讀取指定任務、向使用者說明，若可恢復再使用 `send_rework`。 |
| `*_NO_RECENT_ACTIVITY` | 最近沒有觀察到 bridge activity。 | 先檢查角色與仍在執行的命令；這是顯示警告，不是自動失敗。 |
| `task state is busy; retry` | 其他操作在有限等待期間內持有 task lock。 | 等目前操作結束後重試一次。 |

callback 會帶上明確的 workflow、task、role 與終態。`DELIVERED` 只代表喚醒
文字已送到 Supervisor pane，不能單獨證明已完成面向使用者的回報。

## 放棄執行中的任務

只有在使用者或 Supervisor 已確認任務遭放棄或失效後，才使用
`cancel_task`。取消會釋放角色以接收新任務，但不會終止可能仍在執行的
外部 child process。

## 長時間執行的命令

命令回傳 live session 或 cell ID 時，保留該 ID，並持續輪詢同一個 session，
直到取得明確 exit code。中間輸出或空輸出不代表完成。原 session 尚未確定
結束前，不得啟動第二個 writer。

## 重新啟動 runtime

```bash
codex-role-workflow stop ~/projects/NewProject
codex-role-workflow doctor ~/projects/NewProject
codex-role-workflow open ~/projects/NewProject
```

停止 tmux runtime 不會刪除 worktrees、task state、results、decisions 或
workflow documents。若 `doctor` 回報 FAIL，應先修正該項問題再重新開啟。

上一篇：[設定說明](configuration.md)
