# CLI compatibility

| Item | Support |
| --- | --- |
| Template version | 0.2.0 |
| Codex CLI | Built-in provider; no fixed version requirement |
| Other AI CLIs | Generic adapter contract |
| Verified date | 2026-07-26 |
| Platform | Linux / WSL |
| MCP handshake | stdio initialize and exact per-role tool matrix |
| tmux dispatch | fixed socket, stdin load-buffer, paste-buffer `-d`, 250 ms, one Enter |
| Codex role config | project-local `.codex`, gated by global project trust |
| Generic role config | adapter receives model choice, role instructions, MCP metadata, cwd, and enabled tools |
| Git rules | Codex execpolicy is deployed automatically; generic adapters must provide equivalent enforcement |
| Sandbox / approval | Codex uses the documented matrix; generic adapters own provider-specific enforcement |
| Synthetic workflow status | pane/process, task execution and MCP activity are separate; uncertain probes are UNKNOWN; no TUI parsing |
| Recovery | durable task state with explicit cancel, wakeup retry, and callback retry |

The workflow does not compare the configured CLI against a pinned version. `doctor` checks that the command exists and, when `version_args` is configured, that its version command succeeds. CLI upgrades should be followed by `sync`, `doctor`, and the provider's own compatibility checks.

The generic provider is deliberately an adapter boundary because AI CLI tools do not share one configuration, MCP, sandbox, approval, or authentication syntax. The adapter is responsible for consuming the documented arguments or `ROLE_WORKFLOW_*` environment variables and enforcing the intended role policy.

`cli.model` and `cli.reasoning_effort` provide provider-neutral defaults. Values under `cli.roles.<role>` override those defaults for one role; omitted values remain owned by the configured CLI.
