# Configuration

Project settings live in `.role-cli-workflow/project.toml`. After editing the
file, run:

```bash
role-cli-workflow sync ~/projects/NewProject
role-cli-workflow doctor ~/projects/NewProject
```

## Model selection

The default model and reasoning effort are provider-neutral. A role-specific
section overrides the default for that role:

```toml
[cli]
provider = "codex"
command = "codex"
model = "your-default-model"
reasoning_effort = "medium"

[cli.roles.supervisor]
model = "your-supervisor-model"

[cli.roles.implementer]
model = "your-implementation-model"
reasoning_effort = "high"
```

Omit `model` or `reasoning_effort` to let the configured CLI choose its own
default. The template does not pin a Codex version or maintain a model allowlist.

## Other AI CLI tools

Use the generic provider when another CLI or a provider-specific adapter will
launch each role:

```toml
[cli]
provider = "generic"
command = "my-cli-workflow-adapter"
model = "your-default-model"
reasoning_effort = "medium"
args = [
  "--role", "{role}",
  "--repo", "{repo}",
  "--model", "{model}",
  "--instructions", "{role_instructions}",
  "--mcp-command", "{bridge_command}",
  "--mcp-cwd", "{bridge_cwd}",
  "--enabled-tools", "{enabled_tools}",
]
version_args = ["--version"]
login_check_args = []
```

The adapter must load the supplied role instructions, register the supplied
stdio MCP server, restrict tools to the supplied role matrix, and enforce the
provider's sandbox, approval, trust, authentication, and Git policy.

See the complete examples:

- [Codex configuration](../../examples/codex/project.toml)
- [Generic provider configuration](../../examples/generic/project.toml)

## Project commands and private paths

`[commands]` may define `test`, `lint`, `build`, and `format`. Empty values are
allowed and produce doctor warnings; the template does not guess commands.

`[paths].private` identifies files that must not enter approved Git
transactions. Keep secrets in ignored local files and never place credentials
in `project.toml`.

Previous: [Getting started](getting-started.md) ·
Next: [Operations and recovery](operations-and-recovery.md)
