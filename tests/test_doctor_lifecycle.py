from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.config import ROLES, load_project
import asyncio

from role_cli_workflow.doctor import _handshake_matrix, run_doctor
from role_cli_workflow.lifecycle import LifecycleError, open_workflow, stop_workflow


def fake_codex_home(tmp_path: Path, project_root: Path) -> Path:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text("{}", encoding="utf-8")
    rows = []
    config = load_project(project_root)
    for role in ROLES:
        rows.append(f'[projects."{config.repo(role)}"]\ntrust_level = "trusted"\n')
    (home / "config.toml").write_text("\n".join(rows), encoding="utf-8")
    return home


def fake_codex_cli(tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "codex"
    repo_roles = {
        str(load_project(project_root).repo(role)): role
        for role in ROLES
    }
    binary.write_text(
        f"""#!{sys.executable}
import json
import sys

repo_roles = {repo_roles!r}
args = sys.argv[1:]

if args == ["--version"]:
    print("codex-cli 1.0.0")
elif args == ["login", "status"]:
    pass
elif len(args) == 4 and args[0] == "-C" and args[2:] == ["debug", "prompt-input"]:
    role = repo_roles.get(args[1])
    if role is None:
        raise SystemExit(1)
    print(f"ROLE_CLI_WORKFLOW_CONFIG_PROBE:{{role}}")
elif args[:2] == ["execpolicy", "check"]:
    print(json.dumps({{"decision": "allow"}}))
else:
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return binary


def test_doctor_levels(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("CODEX_HOME", str(fake_codex_home(tmp_path, project_root)))
    binary = fake_codex_cli(tmp_path, project_root, monkeypatch)
    checks = run_doctor(project_root, include_handshake=False)
    assert not [row for row in checks if row.level == "FAIL"]
    assert [row for row in checks if row.level == "WARNING" and row.name.startswith("command:")]
    rows = {row.name: row for row in checks}
    assert rows["CLI"].level == "PASS"
    assert rows["CLI"].detail == f"codex: {binary}"
    assert rows["CLI version"].level == "PASS"
    assert rows["CLI login"].level == "PASS"
    for role in ROLES:
        assert rows[f"trust:{role}"].level == "PASS"
        assert rows[f"config:{role}"].level == "PASS"
        assert rows[f"config-autoload:{role}"].level == "PASS"
        assert rows[f"rules:{role}"].level == "PASS"


def test_real_stdio_matrix(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    matrix = asyncio.run(_handshake_matrix(project_root))
    blocked = {role: type(value).__name__ for role, value in matrix.items() if isinstance(value, BaseException)}
    if blocked:
        pytest.skip(f"managed environment blocked stdio handshake: {blocked}")
    worker = {"get_current_task", "get_context", "submit_result", "report_blocked"}
    supervisor = {
        "list_roles", "assign_task", "cancel_task", "retry_dispatch",
        "retry_callback", "get_task_result", "send_rework", "record_decision",
    }
    for role, tools in matrix.items():
        assert tools == (supervisor if role == "supervisor" else worker)


def test_version_is_reported_without_a_pin(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("CODEX_HOME", str(fake_codex_home(tmp_path, project_root)))
    fake_codex_cli(tmp_path, project_root, monkeypatch)
    checks = run_doctor(project_root, include_handshake=False)
    version = next(row for row in checks if row.name == "CLI version")
    assert version.level == "PASS"
    assert version.detail == "codex-cli 1.0.0"
    assert "known-good" not in version.detail


def test_generic_provider_skips_codex_specific_checks(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    path = project_root / ".role-cli-workflow/project.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace('provider = "codex"', 'provider = "generic"')
    text = text.replace('command = "codex"', 'command = "adapter-cli"')
    text = text.replace('login_check_args = ["login", "status"]', "login_check_args = []")
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr("role_cli_workflow.doctor.shutil.which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr("role_cli_workflow.doctor._version", lambda binary, *args: (0, "adapter 1.0"))
    checks = run_doctor(project_root, include_handshake=False)
    names = {row.name for row in checks}
    assert "CLI safety policy" in names
    assert all(not name.startswith(("trust:", "config:", "config-autoload:", "rules:")) for name in names)
    assert all(f"provider:{role}" in names for role in ROLES)


def test_open_refuses_doctor_fail_and_stop_preserves_history(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setattr("role_cli_workflow.lifecycle.run_doctor", lambda root: [type("C", (), {"level": "FAIL", "name": "x", "detail": "x"})()])
    monkeypatch.setattr("role_cli_workflow.lifecycle.print_checks", lambda checks: 1)
    with pytest.raises(LifecycleError, match="doctor"):
        open_workflow(project_root)
    keep = project_root / "shared_workspace/workflow/tasks/keep/result.json"
    keep.parent.mkdir(parents=True)
    keep.write_text("keep")
    stop_workflow(project_root)
    assert keep.read_text() == "keep"


def test_fake_tmux_open_uses_six_fixed_windows_and_cleans_stale_socket(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    config = load_project(project_root)
    config.socket.parent.mkdir(parents=True, exist_ok=True)
    config.socket.write_text("stale")
    calls: list[tuple[str, ...]] = []
    class Result:
        returncode = 1
        stdout = ""
        stderr = ""
    monkeypatch.setattr("role_cli_workflow.lifecycle.run_doctor", lambda root: [])
    monkeypatch.setattr("role_cli_workflow.lifecycle.print_checks", lambda checks: 0)
    monkeypatch.setattr("role_cli_workflow.lifecycle.is_live", lambda config: False)
    monkeypatch.setattr("role_cli_workflow.lifecycle._tmux", lambda config, *args, **kwargs: calls.append(tuple(args)) or Result())
    open_workflow(project_root)
    assert not config.socket.exists()
    assert sum("new-session" in call for call in calls) == 1
    assert sum("new-window" in call for call in calls) == 5
    assert any("supervisor" in call for call in calls)
    for role in ROLES[1:]:
        assert any(role in call for call in calls)
