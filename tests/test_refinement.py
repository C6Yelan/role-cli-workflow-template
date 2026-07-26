from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from role_cli_workflow.bootstrap import init_project
from role_cli_workflow.cli import main, parser
from role_cli_workflow.config import load_project
from role_cli_workflow.refinement import (
    RefinementError,
    WorkflowRefinementStore,
    classify_execution_profile,
    resolve_execution_profile,
)


RISK_REASON = "Stable replacement is irreversible for an external consumer."


def preflight(
    store: WorkflowRefinementStore,
    profile: str = "FULL",
    *,
    reason: str = RISK_REASON,
    triggers: list[str] | None = None,
) -> dict[str, object]:
    return store.create_preflight(
        workflow_id="wf-contract-1",
        execution_profile=profile,
        execution_reason=reason if profile == "FULL" else "",
        execution_triggers=(triggers or ["irreversible_publication"]) if profile == "FULL" else (triggers or []),
        target_role="implementer",
        base_sha="a" * 40,
        current_target_sha="b" * 40,
        working_tree_status="clean",
        canonical_input_paths=["spec/input.json"],
        canonical_output_paths=["artifacts/output.json"],
        dependency_toolchain=["python 3.12"],
        shared_workspace_access="READ_WRITE",
        private_artifact_owner="Supervisor",
        private_artifact_publisher="Supervisor",
        private_artifact_handoff_path="private/handoff.json",
        candidate_artifact_path="artifacts/output.json",
        stable_artifact_path="artifacts/current.json",
        expected_git_handoff="implementer branch and commit SHA",
        unresolved_items=[],
    )


def required_contract(*, strict: bool = False) -> dict[str, object]:
    required: dict[str, object] = {
        "critical_invariants": [
            {
                "invariant_id": "input-integrity",
                "requirement": "Declared input fingerprint and coverage must match.",
                "authority_ref": "plan:r1:completion-gate",
            }
        ],
        "acceptance_condition": {
            "summary": "The approved artifact is generated deterministically.",
            "authority_ref": "plan:r1:completion-gate",
        },
        "failure_preservation": [],
        "required_negative_tests": [
            {"invariant_ref": "input-integrity", "test": "Reject a mismatched fingerprint."}
        ],
    }
    if strict:
        required["strict_type_requirements"] = ["page count is an integer"]
    return {
        "required_now": required,
        "optional_hardening": {"exact_provenance": "Advisory only"},
        "deferred": {"publication_lifecycle": "No current consumer requires it"},
    }


def test_explicit_facts_promote_only_high_impact_cases_to_full() -> None:
    facts = {
        "private_artifact": True,
        "rebuildable": True,
        "single_internal_consumer": True,
        "schema_word_present": True,
        "stable_filename_present": True,
        "parser_status_present": True,
        "candidate_output_present": True,
        "public_api": False,
        "database_migration": False,
        "irreversible_publication": False,
        "security_authorization_boundary": False,
    }
    assert classify_execution_profile(facts) == "VERIFY"
    for trigger in (
        "public_api", "database_migration", "irreversible_publication",
        "security_authorization_boundary",
    ):
        assert classify_execution_profile({trigger: True}) == "FULL"


def test_legacy_execution_profiles_are_normalized_without_changing_current_values() -> None:
    assert resolve_execution_profile({"execution_profile": "REVIEW"}) == "REVIEW"
    assert resolve_execution_profile({"profile": "STANDARD"}) == "VERIFY"
    assert resolve_execution_profile({"task_profile": "CONTRACT_SENSITIVE"}) == "FULL"
    assert resolve_execution_profile({"profile": "UNKNOWN"}) == "UNSET"
    assert resolve_execution_profile({
        "execution_profile": "UNKNOWN", "profile": "CONTRACT_SENSITIVE"
    }) == "UNSET"


def test_refinement_cli_defaults_verify_and_exposes_authority_assembly() -> None:
    preflight_args = parser().parse_args([
        "workflow", "preflight", "/tmp/project", "wf-1", "--target-role", "explorer",
        "--base-sha", "a" * 40, "--target-sha", "b" * 40,
        "--working-tree-status", "clean", "--git-handoff", "none",
    ])
    assert preflight_args.execution_profile == "VERIFY"
    authority_args = parser().parse_args([
        "workflow", "set-authority", "/tmp/project", "wf-1",
        "--plan-revision", "r1", "--plan-projection-json", '{"rules": []}',
    ])
    assert authority_args.workflow_command == "set-authority"
    review_args = parser().parse_args([
        "workflow", "review-assessment", "/tmp/project", "wf-1",
        "--correctness", "PASS", "--proportionality", "PROPORTIONATE",
        "--base-sha", "a" * 40, "--candidate-sha", "b" * 40,
    ])
    assert review_args.workflow_command == "review-assessment"
    semantic_args = parser().parse_args([
        "workflow", "assess-semantic-repair", "/tmp/project", "wf-1",
        "--invariant-type", "input-integrity", "--directly-required",
        "--real-risk", "--scope-proportionate",
    ])
    assert semantic_args.workflow_command == "assess-semantic-repair"
    resolve_args = parser().parse_args([
        "workflow", "resolve-semantic-repair", "/tmp/project", "wf-1",
        "--classification", "advisory",
    ])
    assert resolve_args.workflow_command == "resolve-semantic-repair"


def test_preflight_cli_accepts_execution_profile_and_writes_only_current_field(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init_project(project_root, assume_yes=True)
    result = main([
        "workflow", "preflight", str(project_root), "wf-cli",
        "--execution-profile", "REVIEW",
        "--target-role", "implementer",
        "--base-sha", "a" * 40,
        "--target-sha", "b" * 40,
        "--working-tree-status", "clean",
        "--git-handoff", "branch and SHA",
    ])
    assert result == 0
    assert json.loads(capsys.readouterr().out)["execution_profile"] == "REVIEW"
    payload = json.loads(
        (
            load_project(project_root).workflow_root
            / "tasks/wf-cli/data/preflight.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["execution_profile"] == "REVIEW"
    assert "profile" not in payload
    assert "task_profile" not in payload


@pytest.mark.parametrize("argument", ["--profile", "--profile=FULL"])
def test_preflight_cli_rejects_renamed_profile_with_clear_error(
    argument: str, capsys: pytest.CaptureFixture[str]
) -> None:
    result = main([
        "workflow", "preflight", "/tmp/project", "wf-1", argument, "FULL",
    ])
    captured = capsys.readouterr()
    assert result == 2
    assert "--profile was renamed to --execution-profile" in captured.err
    assert "--profile" not in parser().format_help()


def test_preflight_uses_one_execution_profile_and_matching_order(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    review = preflight(store, "REVIEW")
    assert review["execution_profile"] == "REVIEW"
    assert review["workflow_order"][-2:] == [
        "DOC_CURATOR_IF_DOCUMENTATION_REQUIRED", "SUPERVISOR_ACCEPTANCE"
    ]
    full = preflight(store)
    assert full["execution_reason"] == RISK_REASON
    assert "REVIEWER_DESIGN_PRECHECK_IF_REQUIRED" in full["workflow_order"]
    assert full["workflow_order"].index("EVALUATOR_LOCKED_GATE") < full["workflow_order"].index(
        "REVIEWER_FINAL_CONFIRMATION"
    )
    projection = (store._path("wf-contract-1", "preflight.json").parents[1] / "preflight.md").read_text()
    assert "Execution profile" in projection
    assert "Effective PLAN revision" in projection


def test_full_requires_specific_reason_and_real_trigger(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    with pytest.raises(RefinementError, match="specific high-impact reason"):
        preflight(store, reason="has private data")
    with pytest.raises(RefinementError, match="applicable high-impact trigger"):
        preflight(store, reason="Private artifact is rebuildable.", triggers=["private_artifact"])


def test_contract_freezes_only_required_now_and_optional_fields_are_optional(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store)
    frozen = store.freeze_contract("wf-contract-1", required_contract(), "approved required_now")
    assert frozen["status"] == "CONTRACT_FROZEN"
    assert set(frozen["contract"]) == {
        "critical_invariants", "acceptance_condition", "failure_preservation", "required_negative_tests"
    }
    assert "exact_provenance" not in frozen["contract"]
    assert "publication_lifecycle" not in frozen["contract"]
    assert frozen["advisory"]["optional_hardening"]
    assert frozen["advisory"]["deferred"]


def test_contract_freeze_accepts_legacy_full_preflight(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store)
    path = store._path("wf-contract-1", "preflight.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("execution_profile")
    payload["profile"] = "CONTRACT_SENSITIVE"
    path.write_text(json.dumps(payload), encoding="utf-8")
    frozen = store.freeze_contract("wf-contract-1", required_contract(), "approved")
    assert frozen["status"] == "CONTRACT_FROZEN"


def test_negative_tests_must_reference_a_critical_invariant(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store)
    value = required_contract()
    value["required_now"]["required_negative_tests"] = [  # type: ignore[index]
        {"invariant_ref": "hypothetical-type", "test": "Try a float"}
    ]
    with pytest.raises(RefinementError, match="reference a critical invariant"):
        store.freeze_contract("wf-contract-1", value, "approved")


def test_contract_change_and_final_sha_gates_remain(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store)
    store.freeze_contract("wf-contract-1", required_contract(), "approved")
    changed = required_contract(strict=True)
    pending = store.freeze_contract("wf-contract-1", changed, "not approved")
    assert pending["status"] == "CONTRACT_CHANGE_REQUIRED"
    approved = store.approve_contract_change("wf-contract-1", "approved revision 2")
    assert approved["revision"] == 2
    with pytest.raises(RefinementError, match="frozen final"):
        store.record_evaluator_verdict("wf-contract-1", "c" * 40, "PASS")
    store.freeze_final_candidate(
        "wf-contract-1", repo_id="implementer", branch="workflow/implementer",
        base_sha="a" * 40, final_candidate_sha="c" * 40,
        commit_message="candidate", remote_ref="origin/workflow/implementer",
        working_tree_clean=True,
    )
    store.record_evaluator_verdict("wf-contract-1", "c" * 40, "PASS")
    store.freeze_final_candidate(
        "wf-contract-1", repo_id="implementer", branch="workflow/implementer",
        base_sha="a" * 40, final_candidate_sha="d" * 40,
        commit_message="candidate fix", remote_ref="origin/workflow/implementer",
        working_tree_clean=True,
    )
    verdict = json.loads(store._path("wf-contract-1", "evaluator-verdict.json").read_text())
    assert verdict["status"] == "STALE"


def test_reviewer_required_fix_needs_active_authority_and_full_basis(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store)
    store.freeze_contract("wf-contract-1", required_contract(), "approved")
    strict_type = {
        "authority_ref": "plan:r1:strict-type-not-frozen",
        "affected_completion_gate": "artifact is generated",
        "real_trigger_or_risk": "hypothetical float",
        "why_advisory_is_insufficient": "",
    }
    assert store.classify_reviewer_finding("wf-contract-1", strict_type) == "ADVISORY"
    provenance = {**strict_type, "authority_ref": "plan:r1:exact-provenance"}
    assert store.classify_reviewer_finding("wf-contract-1", provenance) == "ADVISORY"
    required = {
        "authority_ref": "plan:r1:completion-gate",
        "affected_completion_gate": "input integrity",
        "real_trigger_or_risk": "the current producer can emit a mismatched fingerprint",
        "why_advisory_is_insufficient": "accepting it violates the approved gate",
    }
    assert store.classify_reviewer_finding("wf-contract-1", required) == "REQUIRED_FIX"


def test_reviewer_verdicts_are_separate_and_cover_cumulative_diff(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store)
    assessment = store.record_reviewer_assessment(
        "wf-contract-1",
        correctness_verdict="PASS",
        proportionality_verdict="OVERDESIGNED",
        base_sha="a" * 40,
        final_candidate_sha="b" * 40,
        cumulative_observations=[
            "Validation is repeated in build, serialization, publication and staged reread.",
            "Tests primarily cover hypothetical types with no real producer.",
            "Exact provenance metadata has no consumer.",
            "A publication lifecycle exceeds the Completion Gate.",
        ],
    )
    assert assessment["correctness_verdict"] == "PASS"
    assert assessment["proportionality_verdict"] == "OVERDESIGNED"
    assert assessment["cumulative_diff_reviewed"] is True


def test_second_new_semantic_invariant_requires_proportionality_reassessment(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store)
    store.freeze_contract("wf-contract-1", required_contract(), "approved")
    first = store.assess_semantic_repair(
        "wf-contract-1", invariant_type="input-integrity", directly_required=True,
        real_trigger_or_unacceptable_risk=True, cumulative_scope_proportionate=True,
    )
    assert first["status"] == "REPAIR_ALLOWED"
    store.record_event("wf-contract-1", "task-1", "IMPLEMENTATION_REVISION", 1, "nonce")
    second = store.assess_semantic_repair(
        "wf-contract-1", invariant_type="exact-provenance", directly_required=False,
        real_trigger_or_unacceptable_risk=False, cumulative_scope_proportionate=False,
    )
    assert second["status"] == "PROPORTIONALITY_REASSESSMENT_REQUIRED"
    assert second["suggested_classification"] == "advisory"
    with pytest.raises(RefinementError, match="active frozen contract"):
        store.resolve_semantic_repair(
            "wf-contract-1", classification="required",
            authority_ref="plan:r1:completion-gate", contract_revision=1,
        )
    assert store.resolve_semantic_repair(
        "wf-contract-1", classification="advisory"
    )["status"] == "NO_REWORK_REQUIRED"
    store.record_event("wf-contract-1", "task-1", "MECHANICAL_REPAIR", 2, "nonce")
    store.record_event("wf-contract-1", "task-1", "PERMISSION_RECOVERY", 3, "nonce")
    metrics = json.loads(store._path("wf-contract-1", "retrospective-metrics.json").read_text())
    assert metrics["semantic_revision_count"] == 1


def test_effective_authority_removes_superseded_rule_and_preserves_history(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store)
    store.freeze_contract("wf-contract-1", required_contract(), "approved")
    first = store.set_effective_authority(
        "wf-contract-1", plan_revision="r1", contract_revision=1,
        plan_projection={"rules": [
            {"authority_ref": "plan:r1:toolchain:old-only", "text": "Use old tool only"},
            {"authority_ref": "plan:r1:completion-gate", "text": "Generate the approved artifact"},
        ]},
    )
    assert len(first["active"]["effective_plan_projection"]["rules"]) == 2
    second = store.set_effective_authority(
        "wf-contract-1", plan_revision="r2", contract_revision=1,
        plan_projection=first["active"]["effective_plan_projection"],
        approved_decisions=[{
            "authority_ref": "decision:toolchain-update",
            "supersedes": ["plan:r1:toolchain:old-only"],
            "replacement": "The approved replacement tool may be used.",
            "approved_at": "2026-07-19T00:00:00Z",
        }],
    )
    active_text = json.dumps(second["active"], sort_keys=True)
    assert "Use old tool only" not in active_text
    assert second["history"]
    assert "Use old tool only" in json.dumps(second["history"], sort_keys=True)
    assert store.effective_authority("wf-contract-1")["effective_plan_revision"] == "r2"


def test_authority_context_exposes_active_projection_not_history(project_root: Path, monkeypatch) -> None:
    init_project(project_root, assume_yes=True)
    config = load_project(project_root)
    store = WorkflowRefinementStore(config)
    preflight(store)
    store.freeze_contract("wf-contract-1", required_contract(), "approved")
    store.set_effective_authority(
        "wf-contract-1", plan_revision="r1", contract_revision=1,
        plan_projection={"rules": [{"authority_ref": "plan:r1:gate", "text": "Active gate"}]},
    )
    monkeypatch.setenv("ROLE_CLI_WORKFLOW_PROJECT_ROOT", str(project_root))
    state = importlib.import_module("role_cli_workflow.bridge.state")
    monkeypatch.setattr(state, "WORKFLOW_ROOT", config.workflow_root)
    bridge_store = state.TaskStore.__new__(state.TaskStore)
    payload, sections = bridge_store._workflow_metadata_sections("authority:wf-contract-1")
    assert payload["status"] == "ACTIVE_AUTHORITY"
    assert sections["effective_plan"]["rules"][0]["text"] == "Active gate"
    assert "history" not in sections


def test_legacy_authority_warns_and_conflict_blocks(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    preflight(store, "VERIFY")
    assert store.effective_authority("wf-contract-1")["status"] == "LEGACY_AUTHORITY_WARNING"
    path = store._path("wf-contract-1", "authority.json")
    path.write_text(json.dumps({"status": "AUTHORITY_CONFLICT"}), encoding="utf-8")
    with pytest.raises(RefinementError, match="AUTHORITY_CONFLICT"):
        store.effective_authority("wf-contract-1")


def test_role_instructions_enforce_proportionality_and_complexity_block() -> None:
    root = Path(__file__).parents[1] / "templates" / "roles"
    supervisor = (root / "supervisor.md").read_text(encoding="utf-8")
    explorer = (root / "explorer.md").read_text(encoding="utf-8")
    implementer = (root / "implementer.md").read_text(encoding="utf-8")
    evaluator = (root / "evaluator.md").read_text(encoding="utf-8")
    reviewer = (root / "reviewer.md").read_text(encoding="utf-8")
    doc_curator = (root / "doc-curator.md").read_text(encoding="utf-8")
    assert "`VERIFY` is the default delegated workflow" in supervisor
    assert all(f"`{depth}`" in supervisor for depth in ("DIRECT", "VERIFY", "REVIEW", "FULL"))
    assert "minimum execution profile" in supervisor
    assert "PROPORTIONALITY_REASSESSMENT_REQUIRED" in supervisor
    assert "required_now" in explorer and "optional_hardening" in explorer and "deferred" in explorer
    assert "CONTRACT_COMPLEXITY_CONFLICT" in implementer
    assert "validation_verdict" in evaluator
    assert "final merge-readiness decisions" in evaluator
    assert "correctness_verdict" in reviewer and "proportionality_verdict" in reviewer
    assert "cumulative base-to-final diff" in reviewer
    assert "use Evaluator evidence as an input" in reviewer
    assert "do not act as a second Evaluator or repeat the full validation suite" in reviewer
    assert "dispatched only when documentation is a deliverable" in doc_curator


def test_preflight_rejects_arbitrary_absolute_paths(project_root: Path) -> None:
    init_project(project_root, assume_yes=True)
    store = WorkflowRefinementStore(load_project(project_root))
    with pytest.raises(RefinementError, match="project-relative"):
        store.create_preflight(
            workflow_id="wf-contract-1", target_role="explorer",
            base_sha="a" * 40, current_target_sha="b" * 40, working_tree_status="clean",
            canonical_input_paths=["/etc/passwd"], canonical_output_paths=[], dependency_toolchain=[],
            shared_workspace_access="READ", private_artifact_owner="", private_artifact_publisher="",
            private_artifact_handoff_path="", candidate_artifact_path="", stable_artifact_path="",
            expected_git_handoff="none", unresolved_items=[],
        )
