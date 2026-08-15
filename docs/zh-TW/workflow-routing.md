# Workflow 路由

| Profile | 路徑 | 用途 |
| --- | --- | --- |
| `DIRECT` | Supervisor | 唯讀、workflow infrastructure、文件／metadata、極小可逆非行為工作 |
| `VERIFY` | Implementer → Evaluator → Supervisor | 一般 delegated development |
| `REVIEW` | optional Explorer → Implementer → Evaluator → Reviewer → Supervisor | 實質 scope ambiguity、安全/API/migration/cross-module 風險或正式 review |

Doc Curator 只在 maintained documentation 是 deliverable 時加入。Meaningful product behavior 不使用 `DIRECT`；有疑義時選 `VERIFY`。不可逆或 external action 不論 profile 都需 action-level approval。

Task Contract immutable 且可授權多個 upstream refs。同 scope rework 使用新 round／nonce；material scope change 建立新 task。
