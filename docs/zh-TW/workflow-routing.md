# 工作流路由

Supervisor 只選擇一個 execution profile。這個 profile 同時決定必要角色，
以及是否啟用高影響契約 Gate：

新的 preflight metadata 只使用正式的 `--execution-profile` 參數。已移除的
`--profile` 會回傳改名提示，不會重新寫成 legacy metadata 欄位。

| 深度 | 預設流程 | 適用情況 |
| --- | --- | --- |
| `DIRECT` | Supervisor | 小型、明確、可逆或唯讀任務 |
| `VERIFY` | Implementer ↔ Evaluator → Supervisor | 局部變更且有可執行的驗收條件 |
| `REVIEW` | 視需要 Explorer → Implementer ↔ Evaluator → Reviewer → Supervisor | 需要完整性、設計或較廣影響審查 |
| `FULL` | 契約／探索 Gate → Implementer ↔ Evaluator → Reviewer → 條件式 Doc Curator | 有明確公開、migration、安全、不可逆或跨系統影響 |

Evaluator 負責執行驗證與整理證據，回報 `PASS`、`FAIL` 或
`NOT_VERIFIED`；失敗證據由 Supervisor 送回 Implementer。Reviewer 在相關
驗證完成後，審查完整 diff、需求、設計、風險、可維護性、比例原則及證據
是否充分，原則上不重跑完整驗證套件。Result Envelope 會拒絕缺少或無效的
Evaluator／Reviewer verdict。

公開 API、CLI 或 schema、跨模組或跨服務影響、安全與授權、migration 或
不可逆操作、並行與生命週期或資源風險、驗證缺口、相容性風險、使用者明確
要求 review，或 Supervisor 無法依完整 diff 與現有證據完成驗收時，才需要
Reviewer。

只有範圍有實質歧義或需要探索時才使用 Explorer。只有文件是交付內容，或
公開行為、設定、部署、migration、runbook、正式 artifact index 有變更時，
才使用 Doc Curator。

當 Explorer 只是為了解除範圍歧義時，`REVIEW` 可以只是分析階段的暫時分類。
Explorer PLAN 獲得批准後，Supervisor 必須在派遣 Implementer 前重新評估剩餘
實作。若修改已 bounded、屬於內部、可逆或可重建、有可執行的驗收條件，且
沒有公開、外部、跨系統、安全／私人資料或其他 production consumer trigger，
就降級為 `VERIFY`；分析階段使用過 Explorer，本身不代表一定需要 Reviewer。

若仍有具體 review trigger，例如 production API／CLI／schema consumer、非局部
模組影響、安全或私人資料邊界、migration、相容性、並行／生命週期／資源風險、
驗證缺口、使用者明確要求 review，或 Completion Gate 要求 Review／Prune，則
保留 `REVIEW`。降級時須在 downstream authority 組裝與 Implementer 派遣前，
以固定 preflight 指令和 `--execution-profile VERIFY` 更新，並沿用已解析的
repository 與 handoff 資訊。

0.2 以前的 preflight profile 只會在讀取時正規化，避免既有安全 Gate 靜默
失效；這不會恢復已移除的 CLI 選項，也不會讓新 workflow 寫入舊欄位。
