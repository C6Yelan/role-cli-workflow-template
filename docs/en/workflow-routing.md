# Workflow routing

Supervisor selects one execution profile. The profile determines both the
necessary roles and whether high-impact contract gates apply:

New preflight metadata uses the official `--execution-profile` option. The
removed `--profile` spelling returns a rename error and is not written as a
legacy metadata field.

| Depth | Default flow | Use when |
| --- | --- | --- |
| `DIRECT` | Supervisor | The task is small, explicit, reversible, or read-only |
| `VERIFY` | Implementer ↔ Evaluator → Supervisor | A localized change has executable acceptance criteria |
| `REVIEW` | Explorer if needed → Implementer ↔ Evaluator → Reviewer → Supervisor | Completeness, design, or wider impact needs review |
| `FULL` | Contract/discovery gates → Implementer ↔ Evaluator → Reviewer → conditional Doc Curator | The change has a justified public, migration, security, irreversible, or cross-system impact |

The optional escalation controller is outside these delegated profiles. It is
an explicit, temporary intervention for unusually complex implementation or
workflow recovery. It takes control only when no task or callback needs
attention, works directly without dispatching fixed Workers, and explicitly
returns control to Supervisor when the candidate is ready.

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

`REVIEW` may be a temporary analysis-stage classification when Explorer is
needed only to resolve scope. After the Explorer PLAN is approved, Supervisor
reassesses the remaining implementation before dispatching Implementer. A
bounded, internal, reversible or rebuildable change with executable acceptance
criteria and no public, external, cross-system, security/private-data, or other
production consumer trigger is downgraded to `VERIFY`; using Explorer earlier
does not by itself require Reviewer.

If a concrete review trigger remains—such as a production API/CLI/schema
consumer, non-local module impact, security or private-data boundary,
migration, compatibility, concurrency/lifecycle/resource risk, validation
gap, explicit review request, or a Completion Gate requiring Review or
Prune—`REVIEW` remains. A downgrade reruns the fixed preflight command with
`--execution-profile VERIFY` before downstream authority assembly and
Implementer dispatch, reusing resolved repository and handoff facts.

Pre-0.2 preflight profiles are normalized when read so existing safety Gates
remain active. This does not restore removed CLI options or write legacy fields
into new workflows.
