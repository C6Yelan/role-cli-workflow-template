from __future__ import annotations

import pytest

from role_cli_workflow.bridge.contracts import build_task_contract, validate_result_envelope
from role_cli_workflow.bridge.security import ValidationError
from role_cli_workflow.routing import EXECUTION_PROFILES, route_roles


def test_profiles_and_conditional_roles() -> None:
    assert EXECUTION_PROFILES == ("DIRECT", "VERIFY", "REVIEW")
    assert route_roles("DIRECT") == ("supervisor",)
    assert route_roles("VERIFY") == ("implementer", "evaluator", "supervisor")
    assert route_roles("REVIEW", needs_exploration=True, needs_docs=True) == (
        "explorer", "implementer", "evaluator", "reviewer", "doc-curator", "supervisor",
    )
    with pytest.raises(ValueError):
        route_roles("FULL")


def test_multiple_refs_need_no_authority_projection() -> None:
    sha = "a" * 40
    contract, warnings = build_task_contract(
        "wf-1", "evaluator", "task-eval", "validate", ["report"], ["passes"], [],
        ["task:explorer-1", "task:implementer-1", "decision:user-2"], sha,
    )
    assert warnings == []
    assert contract["authorized_refs"] == ["task:explorer-1", "task:implementer-1", "decision:user-2"]
    assert "effective_authority" not in contract
    with pytest.raises(ValidationError, match="not authorized"):
        validate_result_envelope(
            "wf-1", "evaluator", "task-eval", 0, "0" * 32, "done", [], [], [],
            {"validation_verdict": "PASS"}, ["task:other"], set(contract["authorized_refs"]), sha, sha,
        )


def test_candidate_bound_result_rejects_wrong_sha() -> None:
    old = "a" * 40
    new = "b" * 40
    with pytest.raises(ValidationError, match="wrong candidate"):
        validate_result_envelope(
            "wf-1", "reviewer", "task-review", 0, "0" * 32, "reviewed", [], [], [],
            {"correctness_verdict": "PASS", "proportionality_verdict": "PROPORTIONATE"},
            [], set(), old, new,
        )
