# Role CLI Workflow

這是一套精簡的 Linux／WSL 六角色工作流：Supervisor、Explorer、Implementer、Evaluator、Reviewer、Doc Curator。TaskStore JSON 是 runtime 真實來源；Markdown 只是可重建投影，tmux pane、wakeup 與 callback 只是 transport。

## 路由

- `DIRECT`：僅 Supervisor，處理唯讀、workflow infrastructure、文件／metadata 或極小且可逆的非行為修改。
- `VERIFY`：Implementer → Evaluator → Supervisor；一般 delegated product development 預設使用此路徑。
- `REVIEW`：需要時 Explorer → Implementer → Evaluator → Reviewer → Supervisor。
- Doc Curator 只在文件是真正 deliverable，或 public behavior／configuration／runbook 文件需要維護時加入。

沒有另一個 high-risk profile。高風險工程可用 `REVIEW`，真正不可逆或 external action 仍在動作前取得 exact user approval。

## Candidate 與 TaskStore

Writer assignment 會先確認 input SHA 是目前 configured base，並只從本機 Supervisor repo 讓該 exact object 在 writer repo 可用，不切 branch、不清理 worktree。Implementer 再從該 SHA 建立 `feature/<task>`、建立普通 local commit 並回報 full Git SHA。Bridge 驗證 branch、base ancestry、exact HEAD、乾淨 handoff、private paths 與高信心 secret；Evaluator／Reviewer 的乾淨隔離 worktree 會 detached 到同一 SHA。新 commit 會自然使舊 evidence stale，不再保存 candidate-freeze metadata。

新 task 位於 `shared_workspace/runtime/taskstore-v2/`，只保存 identity、immutable Task Contract hash、input／produced SHA、result hash、notice state、lifecycle 與 timestamps。狀態只有 `DISPATCHED`、`RUNNING`、`RESULT_READY`、`BLOCKED`、`ACCEPTED`、`CANCELLED`。同 scope rework 使用同 task、新 round／nonce；objective 或 acceptance criteria 實質改變就建立新 task。

Context authorization 以 ref 為主，可同時授權多個 `task:*` 與 `decision:*`；`summary`／`full` 只是 view。Legacy authority、contract freeze、semantic repair、candidate freeze、control 與舊 task metadata 可保留成歷史，但新 task 不讀取它們作 gate。

## Recovery 與安全

pane recreation、runtime restart、wakeup/callback failure、stale Markdown、telemetry uncertainty、optional host integration failure 都不會 invalidate task；用原 task identity retry 即可。

wrong workflow/task/role/round/nonce、writer overlap、wrong/stale SHA、dirty validator、private path、高信心 secret、destructive/force Git、Worker protected-branch write、non-fast-forward publication、integration drift 與缺少 exact approval 仍 hard reject。

## Studydy 型 Git 設定

```toml
[git]
base_branch = "dev"
feature_branch_pattern = "feature/*"
protected_branches = ["dev", "main"]
integration_mode = "approved_transaction"
```

Workers 不得 merge/push protected branch。Protected integration plan 只包含 source branch/SHA、target branch/starting SHA、remote/ref、merge method、ordered operations 與 `force_allowed = false`。明確點名該 plan 的一次 approval 可涵蓋顯示的完整順序；任何 material drift 都會失效。

## 常用命令

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

`init` 預期 canonical repository 位於 `main/`，並在不 reset 現有工作的前提下建立隔離 role worktrees。`sync` 保留 runtime history。使用前請在 `.role-cli-workflow/project.toml` 設定 private paths 與 project commands。
