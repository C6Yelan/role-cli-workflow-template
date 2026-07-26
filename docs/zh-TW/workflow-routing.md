# 工作流路由

Supervisor 只選擇一個 execution profile。這個 profile 同時決定必要角色，
以及是否啟用高影響契約 Gate：

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

0.2 以前的 preflight profile 只會在讀取時正規化，避免既有安全 Gate 靜默
失效；這不會恢復已移除的 CLI 選項，也不會讓新 workflow 寫入舊欄位。
