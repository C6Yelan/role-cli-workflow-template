from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from role_cli_workflow.bootstrap import init_project


@pytest.fixture
def tmux_module(project_root, monkeypatch):
    init_project(project_root, assume_yes=True)
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    sys.modules.pop("role_cli_workflow.bridge.tmux_client", None)
    sys.modules.pop("role_cli_workflow.bridge.config", None)
    module = importlib.import_module("role_cli_workflow.bridge.tmux_client")
    yield module
    sys.modules.pop("role_cli_workflow.bridge.tmux_client", None)
    sys.modules.pop("role_cli_workflow.bridge.config", None)


def _client(monkeypatch, tmux_module, *, fail_on: str = ""):
    events: list[tuple[object, ...]] = []
    client = tmux_module.TmuxClient()

    def run(arguments, *, input_text=None, timeout=10.0, check=True):
        events.append(("tmux", *arguments, input_text, check))
        if arguments[0] == fail_on:
            raise tmux_module.TmuxError("tmux operation failed")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(client, "_run", run)
    monkeypatch.setattr(
        tmux_module.time,
        "sleep",
        lambda delay: events.append(("sleep", delay)),
    )
    return client, events


def test_paste_uses_one_delayed_enter_and_cleans_the_named_buffer(
    monkeypatch, tmux_module
) -> None:
    client, events = _client(monkeypatch, tmux_module)

    client.paste("%1", "ROLE_CLI_WORKFLOW_TASK_AVAILABLE", "crw-0123456789abcdef")

    assert events == [
        (
            "tmux", "load-buffer", "-b", "crw-0123456789abcdef", "-",
            "ROLE_CLI_WORKFLOW_TASK_AVAILABLE", True,
        ),
        (
            "tmux", "paste-buffer", "-d", "-b", "crw-0123456789abcdef",
            "-t", "%1", None, True,
        ),
        ("sleep", tmux_module.SUBMISSION_DELAY_SECONDS),
        ("tmux", "send-keys", "-t", "%1", "Enter", None, True),
        (
            "tmux", "delete-buffer", "-b", "crw-0123456789abcdef",
            None, False,
        ),
    ]
    assert tmux_module.SUBMISSION_DELAY_SECONDS == 0.750
    assert sum(event[:2] == ("tmux", "paste-buffer") for event in events) == 1
    assert sum(
        event[:2] == ("tmux", "send-keys") and "Enter" in event
        for event in events
    ) == 1


@pytest.mark.parametrize(
    ("failure", "expected_commands"),
    [
        ("paste-buffer", ["load-buffer", "paste-buffer", "delete-buffer"]),
        (
            "send-keys",
            ["load-buffer", "paste-buffer", "send-keys", "delete-buffer"],
        ),
    ],
)
def test_paste_failure_still_deletes_the_named_buffer(
    monkeypatch, tmux_module, failure: str, expected_commands: list[str]
) -> None:
    client, events = _client(monkeypatch, tmux_module, fail_on=failure)

    with pytest.raises(tmux_module.TmuxError, match="tmux operation failed"):
        client.paste("%1", "TRIGGER", "crw-fedcba9876543210")

    commands = [str(event[1]) for event in events if event[0] == "tmux"]
    assert commands == expected_commands
    assert commands.count("paste-buffer") == 1
    assert commands.count("send-keys") <= 1
    assert commands[-1] == "delete-buffer"
