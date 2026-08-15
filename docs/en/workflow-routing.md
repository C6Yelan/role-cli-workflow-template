# Workflow routing

| Profile | Route | Use |
| --- | --- | --- |
| `DIRECT` | Supervisor | Read-only, workflow infrastructure, docs/metadata, trivial reversible non-behavioral work |
| `VERIFY` | Implementer → Evaluator → Supervisor | Normal delegated development |
| `REVIEW` | optional Explorer → Implementer → Evaluator → Reviewer → Supervisor | Material ambiguity, security/API/migration/cross-module risk, or requested formal review |

Doc Curator is conditional when maintained documentation is a deliverable. Meaningful product behavior does not use `DIRECT`; ambiguous work uses `VERIFY`. Irreversible/external actions require action-level approval regardless of profile.

Task Contracts are immutable and authorize any number of upstream refs. Rework stays on the task with a new round and nonce. Material scope changes create a new task.
