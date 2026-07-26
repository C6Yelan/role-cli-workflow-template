# Changelog

## 0.2.0 — 2026-07-26

- Renamed the distribution, Python module, CLI command, MCP server ID, environment variables, trigger markers, and project metadata directory to the single provider-neutral `role-cli-workflow` identity without a legacy alias.
- Added a generic CLI provider contract with per-role arguments, placeholders, and environment variables while retaining Codex as the default provider.
- Added provider-neutral default model and reasoning settings with per-role overrides.
- Removed the fixed Codex version requirement; doctor now reports the configured CLI version without comparing it to a pinned value.
- Replaced the private-use restriction with the MIT License and removed private project identifiers from public examples and tests.
- Added explicit cancellation plus retryable task wakeups and callbacks without introducing a background scheduler.
- Restored a single-sourced long-command lifecycle rule, tolerated pane recreation, reduced read-path projection writes, and limited semantic reassessment to `FULL` work.
- Made callback wakeups task-specific and kept terminal task IDs visible in the workflow dashboard.
- Added concise English and Traditional Chinese guides for setup, configuration, operations, and recovery.
- Replaced fixed all-role dispatch with one set of execution profiles, an Implementer–Evaluator feedback loop, sequential evidence-based review, and conditional documentation work.
- Enforced Evaluator and Reviewer verdict fields in Result Envelopes and normalized legacy preflight profiles at read time without restoring old CLI options.
- Added a clear rename error for the removed `--profile` option while keeping `--execution-profile` as the only preflight interface.
- Added read-only clean/dirty Git summaries to `status`; dirty worktrees remain untouched and individual query failures display `UNKNOWN`.
- Kept legacy authority warnings visible in status while suppressing repeated dispatch warnings for the same workflow without adding acknowledgement state.
- Increased the single tmux trigger submission delay to reduce the chance that Enter arrives before the CLI composer finishes processing the paste.
- Required Supervisor to reassess analysis-stage `REVIEW` after an approved Explorer PLAN and downgrade bounded internal no-consumer implementation to `VERIFY` when no review trigger remains.
- Made profile reassessment rerun preflight before downstream authority assembly, preserving only the original creation time and letting the fixed `set-authority` flow project current authority afterward.

## 0.1.3 — 2026-07-19

- Connected Reviewer assessment and semantic-repair reassessment to fixed CLI commands and the existing `send_rework` execution path.
- Required a current resolved proportionality checkpoint for second-or-later semantic repairs while leaving mechanical and recovery rework unchanged.
- Made successful Bridge rework dispatch update refinement events and retrospective counters automatically; advisory/deferred hardening is no longer dispatchable as semantic rework.

## 0.1.2 — 2026-07-19

- Added proportional defaults and required a concrete qualifying trigger and reason before selecting the high-impact workflow.
- Replaced the fixed eight-field contract ratchet with traceable critical invariants, a Completion Gate, applicable failure preservation, and invariant-linked negative tests; optional hardening is no longer frozen automatically.
- Added separate Reviewer correctness/proportionality guidance, semantic-repair reassessment, Implementer complexity-conflict reporting, and one effective authority projection with superseded history retained for audit only.

## 0.1.1 — 2026-07-19

- Added task-scoped preflight metadata, compact contract freeze, event classification, retrospective counters, and final candidate SHA freeze.
- Added a fixed Supervisor-only, single-approval add/commit/push transaction plan and executor with exact-scope invalidation and partial failure recovery.
- Separated pane/process telemetry, task execution, and MCP activity; unreliable probes now display UNKNOWN and compact callback status is the default.
- Preserved the six-role MCP tool matrix, Task Contract, Result Envelope, tmux transport, merge approval, and existing instance history.

## 0.1.0 — 2026-07-19

- Extracted a fixed six-role Supervisor workflow into a project-root-driven Linux/WSL template.
- Added init, sync, doctor, open, verify, attach, status, and stop commands.
- Preserved Task Contract, context refs, Result Envelope, callbacks, BLOCKED recovery, deterministic Markdown, observability, and role Git policy.
