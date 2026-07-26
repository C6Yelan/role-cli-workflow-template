from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from codex_role_workflow.bootstrap import init_project
from codex_role_workflow.config import ROLES, load_project
import asyncio

from codex_role_workflow.doctor import _handshake_matrix, run_doctor
from codex_role_workflow.lifecycle import LifecycleError, open_workflow, stop_workflow


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


def test_doctor_levels(project_root: Path, tmp_path: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("CODEX_HOME", str(fake_codex_home(tmp_path, project_root)))
    monkeypatch.setattr("codex_role_workflow.doctor._login_ok", lambda binary, args: True)
    checks = run_doctor(project_root, include_handshake=False)
    assert not [row for row in checks if row.level == "FAIL"]
    assert [row for row in checks if row.level == "WARNING" and row.name.startswith("command:")]


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
    monkeypatch.setattr("codex_role_workflow.doctor._login_ok", lambda binary, args: True)
    checks = run_doctor(project_root, include_handshake=False)
    version = next(row for row in checks if row.name == "CLI version")
    assert version.level == "PASS"
    assert "known-good" not in version.detail


def test_generic_provider_skips_codex_specific_checks(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    path = project_root / ".codex-workflow/project.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace('provider = "codex"', 'provider = "generic"')
    text = text.replace('command = "codex"', 'command = "adapter-cli"')
    text = text.replace('login_check_args = ["login", "status"]', "login_check_args = []")
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr("codex_role_workflow.doctor.shutil.which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr("codex_role_workflow.doctor._version", lambda binary, *args: (0, "adapter 1.0"))
    checks = run_doctor(project_root, include_handshake=False)
    names = {row.name for row in checks}
    assert "CLI safety policy" in names
    assert all(not name.startswith(("trust:", "config:", "config-autoload:", "rules:")) for name in names)
    assert all(f"provider:{role}" in names for role in ROLES)


def test_open_refuses_doctor_fail_and_stop_preserves_history(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setattr("codex_role_workflow.lifecycle.run_doctor", lambda root: [type("C", (), {"level": "FAIL", "name": "x", "detail": "x"})()])
    monkeypatch.setattr("codex_role_workflow.lifecycle.print_checks", lambda checks: 1)
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
    monkeypatch.setattr("codex_role_workflow.lifecycle.run_doctor", lambda root: [])
    monkeypatch.setattr("codex_role_workflow.lifecycle.print_checks", lambda checks: 0)
    monkeypatch.setattr("codex_role_workflow.lifecycle.is_live", lambda config: False)
    monkeypatch.setattr("codex_role_workflow.lifecycle._tmux", lambda config, *args, **kwargs: calls.append(tuple(args)) or Result())
    open_workflow(project_root)
    assert not config.socket.exists()
    assert sum("new-session" in call for call in calls) == 1
    assert sum("new-window" in call for call in calls) == 5
    assert any("supervisor" in call for call in calls)
    for role in ROLES[1:]:
        assert any(role in call for call in calls)
