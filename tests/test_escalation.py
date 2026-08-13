from __future__ import annotations

import json
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import BootstrapError, init_project, sync_project
from role_cli_workflow.cli import _start_escalation, parser
from role_cli_workflow.config import ESCALATION_CONTROLLER, load_project
from role_cli_workflow.control import ControlError, ControlStore
from role_cli_workflow.lifecycle import LifecycleError
from role_cli_workflow.lifecycle import open_workflow, role_processes_live


def enable_escalation(project_root: Path) -> None:
    path = project_root / ".role-cli-workflow/project.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace("[cli.escalation]\nenabled = false", "[cli.escalation]\nenabled = true")
    path.write_text(text, encoding="utf-8")
    sync_project(project_root)


def test_legacy_config_and_missing_control_state_remain_normal(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    path = project_root / ".role-cli-workflow/project.toml"
    text = path.read_text(encoding="utf-8")
    start = text.index("[cli.escalation]")
    end = text.index("\n[git]", start)
    path.write_text(text[:start] + text[end + 1 :], encoding="utf-8")

    config = load_project(project_root)

    assert config.escalation_enabled is False
    assert ControlStore(config.runtime_root).read().to_dict() == {
        "schema_version": 1,
        "mode": "NORMAL",
        "intervention_id": "",
        "reason": "",
        "changed_at": "",
        "release_summary": "",
        "owner": "supervisor",
    }


def test_disabled_init_has_six_roles_and_no_controller_assets(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    config = load_project(project_root)

    assert config.escalation_enabled is False
    assert not (project_root / "shared_workspace/controllers").exists()
    assert not (project_root / "shared_workspace/scripts/run_escalation_controller.sh").exists()
    role_map = (config.bridge_root / "config/roles.toml").read_text(encoding="utf-8")
    assert role_map.count("[roles.") == 6
    assert ESCALATION_CONTROLLER not in role_map


def test_enabled_sync_creates_controller_without_role_topology(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    config = load_project(project_root)

    assert (project_root / "shared_workspace/controllers/escalation-controller.md").is_file()
    runner = project_root / "shared_workspace/scripts/run_escalation_controller.sh"
    assert runner.is_file()
    assert "escalation start" in runner.read_text(encoding="utf-8")
    assert ESCALATION_CONTROLLER not in (
        config.bridge_root / "config/roles.toml"
    ).read_text(encoding="utf-8")
    for role in ("explorer", "implementer", "evaluator", "reviewer", "doc-curator"):
        assert (project_root / role / ".git").is_file()
    assert not (project_root / ESCALATION_CONTROLLER).exists()


def test_disabling_controller_removes_only_generated_controller_assets(
    project_root: Path,
) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    user_rule = project_root / ".codex/rules/user.rules"
    user_rule.write_text("# keep\n", encoding="utf-8")
    path = project_root / ".role-cli-workflow/project.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "[cli.escalation]\nenabled = true",
            "[cli.escalation]\nenabled = false",
        ),
        encoding="utf-8",
    )

    sync_project(project_root)

    assert user_rule.read_text(encoding="utf-8") == "# keep\n"
    assert not (
        project_root
        / ".codex/rules/role-cli-workflow-escalation-controller.rules"
    ).exists()
    assert not (
        project_root / "shared_workspace/scripts/run_escalation_controller.sh"
    ).exists()


def test_sync_cannot_remove_controller_assets_during_active_escalation(
    project_root: Path,
) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    config = load_project(project_root)
    ControlStore(config.runtime_root).acquire(
        "architecture-001", "Resolve one complex change directly"
    )
    path = project_root / ".role-cli-workflow/project.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "[cli.escalation]\nenabled = true",
            "[cli.escalation]\nenabled = false",
        ),
        encoding="utf-8",
    )

    with pytest.raises(BootstrapError, match="workflow escalation is active"):
        sync_project(project_root)
    assert (
        project_root / "shared_workspace/scripts/run_escalation_controller.sh"
    ).is_file()


def test_control_acquire_release_and_matching_id(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = ControlStore(load_project(project_root).runtime_root)

    acquired = store.acquire("architecture-001", "Resolve one complex change directly")
    assert acquired.mode == "ESCALATION"
    assert acquired.owner == ESCALATION_CONTROLLER
    assert store.path.stat().st_mode & 0o777 == 0o600

    with pytest.raises(ControlError, match="does not match"):
        store.release("different-id", "No release")
    released = store.release("architecture-001", "Implementation and tests complete")
    assert released.mode == "NORMAL"
    assert released.owner == "supervisor"
    assert released.release_summary == "Implementation and tests complete"


@pytest.mark.parametrize(
    "task_fields",
    [
        {"status": "RUNNING", "callback_status": "NONE", "error_code": ""},
        {"status": "RESULT_READY", "callback_status": "PENDING", "error_code": ""},
        {"status": "RUNNING", "callback_status": "NONE", "error_code": "WAKEUP_PENDING"},
    ],
)
def test_acquire_rejects_active_or_attention_task(
    project_root: Path, task_fields: dict[str, str]
) -> None:
    init_project(project_root, assume_yes=True)
    store = ControlStore(load_project(project_root).runtime_root)
    store.metadata_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (store.metadata_dir / "task-live.json").write_text(
        json.dumps({"task_id": "task-live", **task_fields}), encoding="utf-8"
    )

    with pytest.raises(ControlError, match="ACTIVE_TASKS_PRESENT: task-live"):
        store.acquire("architecture-001", "Resolve one complex change directly")
    assert store.read().mode == "NORMAL"


def test_malformed_control_state_fails_closed(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = ControlStore(load_project(project_root).runtime_root)
    store.path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ControlError, match="control state is invalid"):
        store.read()
    with pytest.raises(ControlError, match="control state is invalid"):
        with store.mutation_guard("supervisor"):
            pass

    store.path.unlink()
    outside = project_root / "outside-control.json"
    outside.write_text("{}", encoding="utf-8")
    store.path.symlink_to(outside)
    with pytest.raises(ControlError, match="control state is invalid"):
        store.read()


def test_control_owner_excludes_other_manager(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = ControlStore(load_project(project_root).runtime_root)

    with pytest.raises(ControlError, match="belongs to supervisor"):
        with store.mutation_guard(ESCALATION_CONTROLLER):
            pass
    store.acquire("architecture-001", "Resolve one complex change directly")
    with pytest.raises(ControlError, match="belongs to escalation-controller"):
        with store.mutation_guard("supervisor"):
            pass
    with store.mutation_guard(ESCALATION_CONTROLLER):
        pass


def test_task_store_management_mutations_follow_control_owner(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    from role_cli_workflow.bridge import state

    config = load_project(project_root)
    monkeypatch.setattr(state, "METADATA_DIR", config.runtime_root / "metadata")
    supervisor = state.TaskStore.__new__(state.TaskStore)
    supervisor.caller_role = "supervisor"
    controller = state.TaskStore.__new__(state.TaskStore)
    controller.caller_role = ESCALATION_CONTROLLER

    with pytest.raises(ControlError, match="belongs to supervisor"):
        controller.record_decision(
            "decision-controller", "Title", "Decision", "Reason", "Scope", []
        )
    ControlStore(config.runtime_root).acquire(
        "architecture-001", "Resolve one complex change directly"
    )
    with pytest.raises(ControlError, match="belongs to escalation-controller"):
        supervisor.record_decision(
            "decision-supervisor", "Title", "Decision", "Reason", "Scope", []
        )
    with pytest.raises(state.StateError, match="tool is not allowed"):
        controller.assign_task(
            "wf-controller", "explorer", "task-controller", "Do not dispatch",
            ["none"], ["none"], [], [],
        )
    for call in (
        lambda: controller.cancel_task("task-controller", "Do not cancel"),
        lambda: controller.retry_dispatch("task-controller"),
        lambda: controller.retry_callback("task-controller"),
        lambda: controller.send_rework("task-controller"),
    ):
        with pytest.raises(state.StateError, match="tool is not allowed"):
            call()


def test_worker_tools_and_normal_open_fail_closed_during_escalation(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    from role_cli_workflow.bridge import state

    config = load_project(project_root)
    monkeypatch.setattr(state, "METADATA_DIR", config.runtime_root / "metadata")
    ControlStore(config.runtime_root).acquire(
        "architecture-001", "Resolve one complex change directly"
    )
    worker = state.TaskStore.__new__(state.TaskStore)
    worker.caller_role = "explorer"

    with pytest.raises(state.StateError, match="unavailable during workflow escalation"):
        worker.get_current_task()
    with pytest.raises(state.StateError, match="unavailable during workflow escalation"):
        worker.submit_result(
            "wf-worker", "task-worker", 0, "0" * 32, "Summary", [], [], [], {}, []
        )
    with pytest.raises(state.StateError, match="unavailable during workflow escalation"):
        worker.report_blocked(
            "wf-worker", "task-worker", 0, "0" * 32, "Blocked"
        )
    with pytest.raises(LifecycleError, match="workflow escalation is active"):
        open_workflow(project_root)


def test_escalation_cli_schema_is_explicit() -> None:
    start = parser().parse_args([
        "escalation", "start", "/tmp/project",
        "--intervention-id", "architecture-001",
        "--reason", "Complex implementation",
    ])
    release = parser().parse_args([
        "escalation", "release", "/tmp/project",
        "--intervention-id", "architecture-001",
        "--summary", "Complete",
    ])
    assert start.escalation_command == "start"
    assert release.escalation_command == "release"


def test_controller_start_is_disabled_by_default(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    with pytest.raises(LifecycleError, match="escalation controller is disabled"):
        _start_escalation(
            str(project_root), "architecture-001", "Resolve one complex change directly"
        )
    assert not ControlStore(load_project(project_root).runtime_root).path.exists()


def test_controller_cannot_start_inside_fixed_tmux(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    config = load_project(project_root)
    monkeypatch.setenv("TMUX", f"{config.socket},123,0")

    with pytest.raises(LifecycleError, match="outside the fixed workflow tmux"):
        _start_escalation(
            str(project_root), "architecture-001", "Resolve one complex change directly"
        )
    assert not ControlStore(config.runtime_root).path.exists()


def test_controller_launch_uses_project_root_and_configured_overrides(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    path = project_root / ".role-cli-workflow/project.toml"
    text = path.read_text(encoding="utf-8").replace(
        "enabled = true\n# model",
        'enabled = true\nmodel = "controller-model"\nreasoning_effort = "high"\n# model',
    )
    path.write_text(text, encoding="utf-8")
    changed_directory: list[Path] = []
    arguments: list[str] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr("role_cli_workflow.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "role_cli_workflow.cli.os.chdir", lambda value: changed_directory.append(Path(value))
    )
    monkeypatch.setattr(
        "role_cli_workflow.cli.os.execvpe",
        lambda binary, values, environment: (
            arguments.extend(values), environments.append(environment.copy())
        ),
    )

    assert _start_escalation(
        str(project_root), "architecture-001", "Resolve one complex change directly"
    ) == 0

    assert changed_directory == [project_root.resolve()]
    assert environments[0]["ROLE_CLI_WORKFLOW_REPO"] == str(project_root.resolve())
    assert arguments[arguments.index("-m") + 1] == "controller-model"
    assert 'model_reasoning_effort="high"' in arguments
    assert "features.multi_agent=true" in arguments
    assert "assign_task" not in " ".join(arguments)
    assert "release_control" in " ".join(arguments)
    assert ControlStore(load_project(project_root).runtime_root).read().mode == "ESCALATION"


def test_controller_launch_failure_keeps_control_fail_closed(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    monkeypatch.setattr(
        "role_cli_workflow.cli._launch_cli",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("launch failed")),
    )

    with pytest.raises(OSError, match="launch failed"):
        _start_escalation(
            str(project_root), "architecture-001", "Resolve one complex change directly"
        )

    state = ControlStore(load_project(project_root).runtime_root).read()
    assert state.mode == "ESCALATION"
    assert state.release_summary == ""


def test_controller_stops_and_verifies_fixed_runtime_before_launch(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    events: list[str] = []
    monkeypatch.setattr(
        "role_cli_workflow.cli.stop_workflow",
        lambda root: events.append("stop"),
    )
    monkeypatch.setattr(
        "role_cli_workflow.cli.is_live",
        lambda config: events.append("verify-stopped") or False,
    )
    monkeypatch.setattr(
        "role_cli_workflow.cli.role_processes_live",
        lambda config: events.append("verify-processes") or False,
    )
    monkeypatch.setattr(
        "role_cli_workflow.cli._launch_cli",
        lambda *args, **kwargs: events.append("launch") or 0,
    )

    assert _start_escalation(
        str(project_root), "architecture-001", "Resolve one complex change directly"
    ) == 0
    assert events == ["stop", "verify-stopped", "verify-processes", "launch"]


def test_runtime_stop_failure_keeps_escalation_active(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    monkeypatch.setattr(
        "role_cli_workflow.cli.stop_workflow",
        lambda root: (_ for _ in ()).throw(OSError("stop failed")),
    )

    with pytest.raises(LifecycleError, match="escalation remains active"):
        _start_escalation(
            str(project_root), "architecture-001", "Resolve one complex change directly"
        )
    assert ControlStore(load_project(project_root).runtime_root).read().mode == "ESCALATION"


def test_release_refuses_live_fixed_runtime(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    enable_escalation(project_root)
    config = load_project(project_root)
    ControlStore(config.runtime_root).acquire(
        "architecture-001", "Resolve one complex change directly"
    )
    monkeypatch.setattr("role_cli_workflow.cli.is_live", lambda config: True)

    from role_cli_workflow.cli import main

    assert main([
        "escalation", "release", str(project_root),
        "--intervention-id", "architecture-001", "--summary", "Complete",
    ]) == 1
    assert ControlStore(config.runtime_root).read().mode == "ESCALATION"


def test_controller_mcp_release_refuses_live_fixed_runtime(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    config = load_project(project_root)
    ControlStore(config.runtime_root).acquire(
        "architecture-001", "Resolve one complex change directly"
    )
    from role_cli_workflow.bridge import state

    monkeypatch.setattr(state, "METADATA_DIR", config.runtime_root / "metadata")
    monkeypatch.setattr("role_cli_workflow.lifecycle.is_live", lambda config: True)
    controller = state.TaskStore.__new__(state.TaskStore)
    controller.caller_role = ESCALATION_CONTROLLER

    with pytest.raises(state.StateError, match="runtime must be stopped"):
        controller.release_control("architecture-001", "Complete")
    assert ControlStore(config.runtime_root).read().mode == "ESCALATION"


def test_role_process_scan_uses_project_and_fixed_role_environment(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(project_root, assume_yes=True)
    config = load_project(project_root)
    proc = project_root / "proc"
    (proc / "101").mkdir(parents=True)
    (proc / "101/environ").write_bytes(
        f"ROLE_CLI_WORKFLOW_PROJECT_ROOT={config.root}\0"
        "ROLE_CLI_WORKFLOW_ROLE=implementer\0".encode()
    )
    monkeypatch.setattr("role_cli_workflow.lifecycle.Path", lambda value: proc if value == "/proc" else Path(value))

    assert role_processes_live(config) is True
