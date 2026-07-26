# 設定說明

專案設定位於 `.codex-workflow/project.toml`。修改後執行：

```bash
codex-role-workflow sync ~/projects/NewProject
codex-role-workflow doctor ~/projects/NewProject
```

## 模型設定

預設模型與 reasoning effort 採 provider-neutral 設定；個別角色區段可覆寫
共同預設值：

```toml
[cli]
provider = "codex"
command = "codex"
model = "your-default-model"
reasoning_effort = "medium"

[cli.roles.supervisor]
model = "your-supervisor-model"

[cli.roles.implementer]
model = "your-implementation-model"
reasoning_effort = "high"
```

省略 `model` 或 `reasoning_effort` 時，交由所設定的 CLI 使用自身預設值。
模板不綁定 Codex 版本，也不維護模型 allowlist。

## 使用其他 AI CLI

若由其他 CLI 或 provider-specific adapter 啟動角色，使用 generic provider：

```toml
[cli]
provider = "generic"
command = "my-cli-workflow-adapter"
model = "your-default-model"
reasoning_effort = "medium"
args = [
  "--role", "{role}",
  "--repo", "{repo}",
  "--model", "{model}",
  "--instructions", "{role_instructions}",
  "--mcp-command", "{bridge_command}",
  "--mcp-cwd", "{bridge_cwd}",
  "--enabled-tools", "{enabled_tools}",
]
version_args = ["--version"]
login_check_args = []
```

adapter 必須載入指定的角色指令、註冊 stdio MCP server、限制角色可用工具，
並落實該 provider 的 sandbox、approval、trust、authentication 與 Git policy。

完整範例：

- [Codex 設定](../../examples/codex/project.toml)
- [Generic provider 設定](../../examples/generic/project.toml)

## 專案指令與私有路徑

`[commands]` 可設定 `test`、`lint`、`build` 與 `format`。允許保留空值，
`doctor` 會提出警告；模板不會自行猜測指令。

`[paths].private` 用來標示不得進入核准式 Git transaction 的檔案。機密資料
應保留於已忽略的本機檔案，且不得將 credentials 寫入 `project.toml`。

上一篇：[快速開始](getting-started.md) ·
下一篇：[操作與異常恢復](operations-and-recovery.md)
