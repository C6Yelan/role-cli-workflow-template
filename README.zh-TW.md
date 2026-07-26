# Role CLI Workflow Template

[English](README.md) | [繁體中文](README.zh-TW.md)

這是一套可重複使用的 Linux／WSL 專案模板，提供固定六角色 AI CLI 工作流：Supervisor、Explorer、Implementer、Evaluator、Reviewer 與 Doc Curator。Supervisor 是唯一面向使用者的角色。TaskStore JSON 是 runtime 真實來源；Markdown 是可確定重建、供人閱讀的投影。

Codex 是內建 provider。其他 AI CLI 工具可透過下方說明的 generic adapter contract 接入。本專案是社群專案，並非 OpenAI 官方產品。

## 文件

- [繁體中文文件](docs/zh-TW/README.md)
- [繁體中文快速開始](docs/zh-TW/getting-started.md)
- [繁體中文工作流路由](docs/zh-TW/workflow-routing.md)
- [English documentation](docs/en/README.md)

## 語言行為

Runtime templates 與 canonical role instructions 使用英文。Supervisor 的人類可讀回覆依下列順序選擇語言：

1. 使用者目前訊息明確要求的語言。
2. `.role-cli-workflow/project_instructions.md` 明確指定的輸出語言。
3. 使用者最新訊息使用的語言。
4. 無法判斷時使用英文。

Supervisor 會使用選定語言撰寫 Task Contract 中供人閱讀的 `objective`、`deliverables`、`acceptance_criteria` 與 task-specific `constraints`，讓 Workers 不需讀取原始對話也能維持相同語言。Workers 優先採用 Task Contract 明確要求的語言，否則依 `objective` 的語言輸出，無法判斷時使用英文。Doc Curator 收到明確的文件目標語言時，以該語言為準。

Code、commands、CLI options、paths、Git refs、identifiers、schema keys、Result Envelope keys、verdicts、status codes、event kinds、error codes、raw logs、raw diagnostics 與必須逐字保留的文字，維持英文或原文。`[WORKFLOW_STATUS]` 使用英文 canonical field labels；其中人類可讀的 values 可以使用選定語言。

這是 instruction-level policy。目前沒有 CLI language option、locale setting、translation service，也沒有在 project configuration、TaskStore、preflight metadata 或 Result Envelope 新增 language field。

## 系統需求

- Linux 或 WSL
- Python 3.12、`uv`、Git 與 tmux
- 已安裝、完成驗證且支援 MCP 的 AI CLI；可使用內建 Codex provider 或 custom adapter

## 安裝與啟動

```bash
uv tool install /path/to/role-cli-workflow-template

mkdir -p ~/projects/NewProject
git clone <repo-url> ~/projects/NewProject/main

role-cli-workflow init ~/projects/NewProject
role-cli-workflow doctor ~/projects/NewProject
role-cli-workflow open ~/projects/NewProject
role-cli-workflow attach ~/projects/NewProject
```

也可以直接從 GitHub 安裝：

```bash
uv tool install "git+https://github.com/C6Yelan/role-cli-workflow-template.git"
```

`init` 會顯示 base branch、五個角色 branches、worktree destinations 與 `main` working-tree cleanliness，之後才要求輸入 `yes`。它不會覆寫 `main`、commit、push、reset 或 clean。只有非互動的隔離測試才應使用 `init --yes` 接受畫面上顯示的本機 worktree 建立計畫。

## 標準目錄

```text
<project-root>/
├── main/                  existing Git repository; Supervisor cwd
├── explorer/              linked worktree
├── implementer/           linked worktree
├── evaluator/             linked worktree
├── reviewer/              linked worktree
├── doc-curator/           linked worktree
├── shared_workspace/
│   ├── role_bridge/       canonical role maps and provider policy
│   ├── roles/             canonical role instructions
│   ├── workflow/          human-readable projections and task documents
│   ├── scripts/           fixed role launchers
│   └── runtime/           TaskStore and fixed tmux socket
└── .role-cli-workflow/
    ├── project.toml
    ├── project_instructions.md
    └── VERSION
```

預設角色 branches 是 `workflow/<role>`。若要覆寫單一角色，可在 `[git]` 下加入例如 `implementer_branch = "feature/backend"`，再執行 `sync` 與 `doctor`。既有 branch 不會被 reset 或靜默重新 mapping。

## 指令

- `init`：檢查 `main`、要求確認、建立五個 linked worktrees，並安裝 project instance；不會啟動 configured CLI。
- `sync`：以 deterministic 方式重新部署 canonical role instructions、configs、rules 與固定 launchers；保留 runtime 與 workflow history。
- `doctor`：針對 layout、worktrees、configured CLI、optional login probe、tmux、uv、Python、role deployment、provider-specific policy checks 與六個真實 stdio MCP handshakes 回報 `PASS`／`WARNING`／`FAIL`。
- `open`：先執行 sync，要求 doctor 沒有 `FAIL`，再建立固定六視窗 tmux server。
- `verify`：檢查 live fixed panes，不讀取 TUI content，也不執行產品任務。
- `attach`：直接連接 Supervisor window。
- `status`：顯示 runtime/task metadata 與每個固定 worktree 的唯讀 Git 摘要，不解析 panes。`DIRTY` 只是資訊；status 不會 clean 或修改 worktree。
- `stop`：只停止固定 tmux server，並移除精確的 stale socket；保留 worktrees、tasks、results 與 reports。

Attach 後可用滑鼠滾輪查看歷史、點擊底部 window labels 檢視角色、使用 `Ctrl+b [` 進入 copy mode、使用 `q` 或 `Esc` 離開 copy mode、使用 `Ctrl+b d` detach。Active task 期間避免在自動化 Worker composer 中手動輸入。

## 設定

`.role-cli-workflow/project.toml` 包含 project identity、CLI provider settings、optional per-role launch overrides、base/branch policy、private paths，以及 optional `test`、`lint`、`build` 與 `format` commands。空白 command 會產生 doctor warning；init 不會猜測或執行 package scripts。本工作流不 pin 或要求特定 Codex version。

Model 與 reasoning settings 使用單一 provider-neutral surface。`[cli]` 是所有角色的 defaults；`[cli.roles.<role>]` 只覆寫該角色。若兩層都省略欄位，launcher 會讓 configured CLI 自行選擇。Template 不使用 allowlist 限制 values；model availability 由 installed CLI 與 account 負責：

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

此例中，沒有 override 的角色使用 `your-default-model`；Supervisor 與 Implementer 使用自己的 model。相同設定也適用 generic provider。

### 其他 CLI providers

設定 `provider = "generic"`，並讓 `command` 指向 CLI 或小型 provider-specific adapter：

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
  "--reasoning-effort", "{reasoning_effort}",
  "--instructions", "{role_instructions}",
  "--mcp-command", "{bridge_command}",
  "--mcp-cwd", "{bridge_cwd}",
  "--enabled-tools", "{enabled_tools}",
]
version_args = ["--version"]
login_check_args = []
```

Supported placeholders 是 `{project_root}`、`{repo}`、`{role}`、`{model}`、`{reasoning_effort}`、`{role_instructions}`、`{bridge_command}`、`{bridge_cwd}` 與 `{enabled_tools}`。相同 values 會輸出為 `ROLE_CLI_WORKFLOW_PROJECT_ROOT`、`ROLE_CLI_WORKFLOW_REPO`、`ROLE_CLI_WORKFLOW_ROLE`、`ROLE_CLI_WORKFLOW_MODEL`、`ROLE_CLI_WORKFLOW_REASONING_EFFORT`、`ROLE_CLI_WORKFLOW_ROLE_INSTRUCTIONS`、`ROLE_CLI_WORKFLOW_MCP_COMMAND`、`ROLE_CLI_WORKFLOW_MCP_CWD` 與 `ROLE_CLI_WORKFLOW_MCP_ENABLED_TOOLS`。沒有設定 model 或 reasoning effort 時，相關 variables 不會出現；在缺少設定時使用對應 placeholder 會發生錯誤。

Adapter 必須載入 role instructions、註冊提供的 stdio MCP server、將 tools 限制為指定的 role matrix，並落實預期的 sandbox、approval、trust、authentication 與 Git policy。這些控制與 provider 有關，無法從一組通用 command line 安全推導。範例請見 `examples/generic/project.toml`。

`.role-cli-workflow/project_instructions.md` 保存保守產生的 project-specific candidates 與 TODOs。固定 Git、MCP、safety、role 與 result rules 仍以 `shared_workspace/roles` 和 `shared_workspace/role_bridge/config` 為 canonical。

使用 Codex provider 時，每個 worktree 會部署本機 `.codex/config.toml` 與 `.codex/rules/`。`sync` 會把 `.codex/` 加入 Git local `info/exclude`；不修改產品 `.gitignore`、`CODEX_HOME`、global Codex config/rules、authentication、history、sessions 或 logs。Generic providers 不會收到 template 猜測的 provider-local configuration。

## 角色與 Git authority

| Role | Intended sandbox / approval | Git authority |
| --- | --- | --- |
| Supervisor | workspace-write over project root / on-request | read、fetch、pull `--ff-only`；direct writes 需要 prompt；精確核准的 transaction 可一次組合 task-branch add/commit/push 或 integration merge/resulting push；只有此角色可 merge |
| Implementer | workspace-write / never | 可在自己的 task branch add、commit、push；禁止 protected push 與 merge |
| Doc Curator | workspace-write / never | 可在 approved docs branch add、commit、push；禁止 protected push 與 merge |
| Explorer | read-only / never | read、fetch、pull `--ff-only`；禁止 publication 與 merge |
| Evaluator | workspace-write / never | read、fetch、pull `--ff-only`；writable output 僅供 validation artifacts，不供 Git publication |
| Reviewer | read-only / never | read、fetch、pull `--ff-only`；禁止 publication 與 merge |

所有 role instructions 都禁止 destructive reset/clean、force push、forced branch/worktree deletion、repository-wide overwrite 與 shell command-string wrappers。Codex 會收到 generated execpolicy rules。Generic providers 必須在自己的 adapter 或 CLI configuration 落實等價邊界。Rules 是 command boundary，不能取代使用者審查。

## Execution profiles 與 workflow data

Task Contracts 包含目前的 objective、deliverables、acceptance criteria、task-only constraints 與 authorized context refs。Workers 透過 MCP 取得 task/context，再回傳 structured Result Envelopes。Supervisor 為每個工作選擇一種 execution profile：

- `DIRECT`：由 Supervisor 處理，不派遣 Worker。
- `VERIFY`：使用 Implementer 與 Evaluator。
- `REVIEW`：在 validation evidence 之後加入 Reviewer。
- `FULL`：只為有明確理由的 high-impact work 加入 frozen-contract gates。

Explorer 與 Doc Curator 是條件式角色。BLOCKED task 透過 `send_rework` 使用相同 task ID 與新的 round/nonce 恢復。完整 upstream result 不會插入 downstream prompt。

Evaluator 執行相關 checks，並回傳 `PASS`、`FAIL` 或 `NOT_VERIFIED` evidence。Failure 透過 Supervisor 返回 Implementer。Reviewer 接著審查完整 diff、requirements、design、risk、maintainability、proportionality 與 Evaluator evidence 是否充分；通常不重跑完整 test suite。Result Envelope validation 會要求 Evaluator verdicts 及 Reviewer correctness/proportionality verdicts。Reviewer 與 Evaluator 不會平行產生互相競爭的 final verdict。

Task state 在 tmux wakeup 失敗時仍會持久保存。`WAKEUP_PENDING` 表示 Supervisor 應使用 `retry_dispatch`，而不是建立 duplicate task。使用者明確放棄的 running task 可用 `cancel_task` 釋放，replacement 使用新的 task ID。Result 與 BLOCKED callbacks 會帶有精確 workflow、task、role 與 status，並在成功送達前維持 `PENDING`；Supervisor 可使用 `retry_callback`，且必須先以 `get_task_result` 讀取 BLOCKED result，再向使用者說明 blocker 與 required next action。

Long-running child commands 必須保留相同 live session 或 cell ID，直到觀察到 explicit exit code。Intermediate output 不代表完成；原 session 未解決時不得啟動第二個 writer 或 retry。

`shared_workspace/runtime` 下的 runtime JSON 是 source of truth。`shared_workspace/workflow/current_task.md`、task reports、indexes、handoffs 與 decision log 只是 projections。

Delegated workflow 可建立 `tasks/<workflow-id>/data/preflight.json` 及簡短的 `preflight.md` projection。`VERIFY` 是預設 delegated profile。`FULL` 必須記錄 public、migration、cross-system、irreversible、security/provider、SHA-locked release 或 regulatory reason 與 trigger。它只 freeze 可追溯的 `required_now` invariants 與 Completion Gate；optional hardening 和 deferred ideas 保持 advisory。

Reviewer 分別回報 correctness 與 cumulative proportionality verdicts。固定 workflow CLI commands 會記錄 assessment、評估 semantic repair，並解析 required/advisory/deferred classification。對 `FULL` 而言，第一次 semantic repair 後的 `send_rework` 需要目前的 `invariant_type` 與已解析的 checkpoint；其他 profiles 不使用此 checkpoint。Pre-0.2 preflight profile values 只在讀取時正規化；不恢復已移除的 CLI options。Effective PLAN、contract 與 approved decision revisions 只投影目前有效內容；superseded wording 僅保留為歷史。Event metadata 仍區分 semantic、mechanical、permission/context、stale-SHA、verification 與 Git-approval events，不改變 task round 或 nonce。

## 核准的 Git transactions

Supervisor 直接執行 Git writes 時，仍保留既有 per-command prompts。「完成所有 Git 操作」這類 open-ended request 不構成核准。若要為精確的 add/commit/push subset 取得一次核准，先建立 read-only plan 並展示給使用者：

```bash
role-cli-workflow git plan ~/projects/NewProject tx-001 \
  --workflow-id wf-001 --task-id task-001 --repo-id main \
  --operation add --operation commit --operation push \
  --file path/to/file.py --commit-message "Implement approved task"
```

使用者明確批准該 transaction 後，Supervisor 記錄 approval 並執行固定 command：

```bash
role-cli-workflow git approve ~/projects/NewProject tx-001 \
  --approval-summary "User explicitly approved the displayed tx-001 plan"
role-cli-workflow git execute ~/projects/NewProject tx-001
```

Integration plan 也可以包含一次精確 merge 與其 resulting push：

```bash
role-cli-workflow git plan ~/projects/NewProject tx-integration-001 \
  --workflow-id wf-001 --task-id task-001 --repo-id main \
  --operation merge --operation push \
  --source-branch feature/approved-change --target-branch main \
  --merge-method ff-only --remote origin \
  --destination-ref refs/heads/main
```

產生的 plan 會記錄 source branch/SHA、target branch/starting SHA、merge method、remote identity、destination ref、clean-worktree expectation、no-force rule 與 stop conditions。一次明確且指出 `tx-integration-001` 的核准可涵蓋已展示的 `merge → push` sequence，因此 merge 完成後不會再次要求 push approval。

Executor 不接受 arbitrary repo path、shell command 或額外 Git argument。每次 write 前都會重新驗證已核准的 branch、SHA、worktree、remote/ref 與 fast-forward safety。Scope drift、conflict、未核准的 resolution 或 modification、merge method 改變、force/non-fast-forward requirement，或 plan 外操作都會使 approval 失效。暫時性 push failure 會保留 partial state；只有精確條件不變時才能重試同一個 push。Tag、rebase、cherry-pick、deletion、cleanup 與 force 仍不屬於 transaction。

Status output 將 pane/process telemetry（`ALIVE`、`DOWN`、`UNKNOWN`）、task execution 與 latest MCP activity 視為獨立事實。一般 callbacks 使用 compact workflow block；只有 explicit status request、多個 active validation/review tasks、blocked/unavailable roles、telemetry anomaly、partial Git failure 與 final acceptance 才顯示完整 role table。

## Codex trust 與疑難排解

執行 `open` 前，active global Codex configuration 必須信任每個固定 worktree。Trust 缺失時，`doctor` 會以 `FAIL` 停止並列出 affected paths。使用 Codex 開啟每個固定 repository，透過正常 prompt 批准 project trust，然後重新執行：

```bash
role-cli-workflow sync ~/projects/NewProject
role-cli-workflow doctor ~/projects/NewProject
```

Codex versions 不會被 pin 或比較。升級後請重跑 doctor 與 compatibility checks。只有在不存在 fixed live session 時，`open` 才會安全取代精確 stale socket。`stop` 是 idempotent。

使用 generic provider 時，`doctor` 會檢查 configured executable、optional version/login probes、role launchers 與 MCP handshakes。由於無法通用驗證 provider-specific sandbox、approval、trust 與 Git enforcement，因此會產生 warning。

## 移除工作流但保留 main

先執行 `stop`。保留需要的 role commits，再以正常 Git worktree commands 手動移除五個 linked worktrees。最後移除 `shared_workspace/` 與 `.role-cli-workflow/`。Template 不會自動執行此 destructive removal，也不會刪除 `main`。

## License

MIT。請參閱 `LICENSE`。
