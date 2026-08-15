# Configuration

The generated `.role-cli-workflow/project.toml` contains provider settings, Git policy, private paths, and project commands. The Git section is intentionally small:

```toml
[git]
base_branch = "dev"
role_branch_prefix = "workflow/"
feature_branch_pattern = "feature/*"
protected_branches = ["dev", "main"]
integration_mode = "approved_transaction"
```

Implementer product candidates must match `feature_branch_pattern`. Workers cannot write protected branches. A Supervisor-approved exact transaction may integrate a feature into the base branch, and the base branch into `main`.

Configure every private root and secret-file pattern under `[paths].private`. `.env`, `docs_local/`, key and certificate formats are denied by built-in candidate checks as well. High-confidence secret values are rejected without printing them; ordinary identifiers such as `token` are allowed.
