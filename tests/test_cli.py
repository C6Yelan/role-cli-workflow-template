from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.cli import _role_launch, parser
from role_cli_workflow.config import ProjectConfigError, load_project


def test_distribution_exposes_only_the_provider_neutral_command() -> None:
    root = Path(__file__).parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["name"] == "role-cli-workflow"
    assert project["scripts"] == {
        "role-cli-workflow": "role_cli_workflow.cli:main",
    }
    assert (root / "src/role_cli_workflow").is_dir()
    assert not (root / "src/codex_role_workflow").exists()


def test_git_plan_cli_accepts_exact_integration_fields() -> None:
    args = parser().parse_args(
        [
            "git",
            "plan",
            "/tmp/project",
            "tx-integration",
            "--workflow-id",
            "wf-1",
            "--task-id",
            "task-1",
            "--repo-id",
            "main",
            "--operation",
            "merge",
            "--operation",
            "push",
            "--source-branch",
            "feature/change",
            "--target-branch",
            "main",
            "--merge-method",
            "ff-only",
            "--remote",
            "origin",
            "--destination-ref",
            "refs/heads/main",
        ]
    )
    assert args.operation == ["merge", "push"]
    assert args.source_branch == "feature/change"
    assert args.target_branch == "main"
    assert args.destination_ref == "refs/heads/main"


def capture_launch(project_root: Path, role: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    captured: list[str] = []
    monkeypatch.setattr("role_cli_workflow.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("role_cli_workflow.cli.os.chdir", lambda path: None)
    monkeypatch.setattr(
        "role_cli_workflow.cli.os.execvpe",
        lambda binary, arguments, environment: captured.extend(arguments),
    )
    assert _role_launch(str(project_root), role) == 0
    return captured


def test_role_launch_uses_cli_defaults_when_overrides_are_omitted(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    arguments = capture_launch(project_root, "supervisor", monkeypatch)
    assert "-m" not in arguments
    assert not any(item.startswith("model_reasoning_effort=") for item in arguments)


def test_role_launch_uses_configured_model_and_reasoning_effort(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    path = project_root / ".role-cli-workflow/project.toml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\n[cli.roles.supervisor]\nmodel = "example-model"\nreasoning_effort = "high"\n',
        encoding="utf-8",
    )
    arguments = capture_launch(project_root, "supervisor", monkeypatch)
    assert arguments[arguments.index("-m") + 1] == "example-model"
    assert 'model_reasoning_effort="high"' in arguments


def test_role_launch_uses_global_model_defaults_and_role_override(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    path = project_root / ".role-cli-workflow/project.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        'command = "codex"',
        'command = "codex"\nmodel = "default-model"\nreasoning_effort = "medium"',
    )
    text += '\n[cli.roles.implementer]\nmodel = "implementation-model"\n'
    path.write_text(text, encoding="utf-8")
    supervisor = capture_launch(project_root, "supervisor", monkeypatch)
    implementer = capture_launch(project_root, "implementer", monkeypatch)
    assert supervisor[supervisor.index("-m") + 1] == "default-model"
    assert 'model_reasoning_effort="medium"' in supervisor
    assert implementer[implementer.index("-m") + 1] == "implementation-model"
    assert 'model_reasoning_effort="medium"' in implementer


def test_unknown_role_settings_are_rejected(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    path = project_root / ".role-cli-workflow/project.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + '\n[cli.roles.typo]\nmodel = "example-model"\n',
        encoding="utf-8",
    )
    with pytest.raises(ProjectConfigError, match="unknown cli role settings: typo"):
        load_project(project_root)


def test_generic_provider_expands_adapter_contract(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    path = project_root / ".role-cli-workflow/project.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace('provider = "codex"', 'provider = "generic"')
    text = text.replace('command = "codex"', 'command = "adapter-cli"')
    text = text.replace('args = []', 'model = "review-model"\nargs = []', 1)
    text = text.replace(
        'args = []',
        'args = ["--role", "{role}", "--model", "{model}", "--instructions", "{role_instructions}", "--mcp", "{bridge_command}"]',
        1,
    )
    text = text.replace('login_check_args = ["login", "status"]', "login_check_args = []")
    path.write_text(text, encoding="utf-8")
    arguments = capture_launch(project_root, "reviewer", monkeypatch)
    assert arguments[0] == "/usr/bin/adapter-cli"
    assert arguments[arguments.index("--role") + 1] == "reviewer"
    assert arguments[arguments.index("--model") + 1] == "review-model"
    assert arguments[arguments.index("--instructions") + 1].endswith("shared_workspace/roles/reviewer.md")
    assert arguments[arguments.index("--mcp") + 1].endswith("shared_workspace/scripts/run_bridge_server.sh")
