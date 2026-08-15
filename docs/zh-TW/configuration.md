# 設定

`.role-cli-workflow/project.toml` 包含 provider、Git policy、private paths 與 project commands。Git 設定刻意保持精簡：

```toml
[git]
base_branch = "dev"
role_branch_prefix = "workflow/"
feature_branch_pattern = "feature/*"
protected_branches = ["dev", "main"]
integration_mode = "approved_transaction"
```

Implementer product candidate 必須符合 `feature_branch_pattern`。Workers 不得寫 protected branches。Supervisor exact approved transaction 可將 feature 整合至 base，再將 base 整合至 `main`。

在 `[paths].private` 列出所有 private roots 與 secret file patterns。Built-in candidate check 也會拒絕 `.env`、`docs_local/`、key/certificate formats。高信心 secret value 會被拒絕且不印出；`token` 等普通 identifier 不會因此被擋。
