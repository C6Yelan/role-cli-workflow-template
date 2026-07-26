# 快速開始

## 系統需求

- Linux 或 WSL
- Python 3.12
- `uv`、Git 與 tmux
- 已安裝、完成登入且支援 MCP 的 AI CLI

## 安裝

從本機 checkout 安裝：

```bash
uv tool install /path/to/role-cli-workflow-template
```

從 GitHub 安裝：

```bash
uv tool install "git+https://github.com/C6Yelan/role-cli-workflow-template.git"
```

## 準備專案

project root 是容器資料夾，其中的 `main/` 必須已經是 Git repository：

```bash
mkdir -p ~/projects/NewProject
git clone <repo-url> ~/projects/NewProject/main

role-cli-workflow init ~/projects/NewProject
role-cli-workflow doctor ~/projects/NewProject
role-cli-workflow open ~/projects/NewProject
role-cli-workflow attach ~/projects/NewProject
```

`init` 會先顯示預計建立的 branches 與 worktrees，再要求確認。它不會
commit、push、reset、clean 或覆寫 `main`。

## 日常操作

attach 後只與 Supervisor 互動。其他五個視窗是固定 Worker 角色，透過
role bridge 接收任務。

常用指令：

```bash
role-cli-workflow status ~/projects/NewProject
role-cli-workflow verify ~/projects/NewProject
role-cli-workflow sync ~/projects/NewProject
role-cli-workflow stop ~/projects/NewProject
```

- `status` 讀取持久化 runtime state，並顯示每個固定 worktree 的 branch、
  HEAD 與 clean/dirty 變更數。Dirty 只表示觀測結果，不會觸發清理或修改。
- `verify` 檢查固定 panes，不會派發產品任務。
- `sync` 重新部署模板管理的檔案，並保留任務歷史。
- `stop` 只停止 tmux；worktrees、任務紀錄與報告都會保留。

下一篇：[設定說明](configuration.md) ·
[操作與異常恢復](operations-and-recovery.md)
