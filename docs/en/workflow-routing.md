# Workflow routing

Supervisor selects one execution profile. The profile determines both the
necessary roles and whether high-impact contract gates apply:

| Depth | Default flow | Use when |
| --- | --- | --- |
| `DIRECT` | Supervisor | The task is small, explicit, reversible, or read-only |
| `VERIFY` | Implementer ↔ Evaluator → Supervisor | A localized change has executable acceptance criteria |
| `REVIEW` | Explorer if needed → Implementer ↔ Evaluator → Reviewer → Supervisor | Completeness, design, or wider impact needs review |
| `FULL` | Contract/discovery gates → Implementer ↔ Evaluator → Reviewer → conditional Doc Curator | The change has a justified public, migration, security, irreversible, or cross-system impact |

Evaluator owns executable validation and evidence. It reports `PASS`, `FAIL`,
or `NOT_VERIFIED`; failed evidence returns through Supervisor to Implementer.
Reviewer runs after relevant validation and judges the complete diff,
requirements, design, risk, maintainability, proportionality, and whether the
evidence is sufficient. Reviewer normally does not rerun the full validation
suite. Result Envelopes reject missing or invalid Evaluator and Reviewer
verdicts.

Reviewer is required for public API, CLI, or schema changes; cross-module or
cross-service impact; security and authorization; migrations or irreversible
operations; concurrency, lifecycle, or resource risks; validation gaps;
compatibility risk; explicit review requests; or when Supervisor cannot accept
the complete diff from the available evidence.

Explorer is conditional on material ambiguity or discovery needs. Doc Curator
is conditional on documentation deliverables or changes to public behavior,
configuration, deployment, migration, runbooks, or formal artifact indexes.

Pre-0.2 preflight profiles are normalized when read so existing safety Gates
remain active. This does not restore removed CLI options or write legacy fields
into new workflows.
