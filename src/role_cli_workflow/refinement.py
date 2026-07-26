"""Small task-scoped workflow metadata used by the fixed six-role flow."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .config import ProjectConfig, ROLES

TASK_PROFILES = ("STANDARD", "CONTRACT_SENSITIVE")
EVENT_KINDS = (
    "INITIAL_IMPLEMENTATION",
    "IMPLEMENTATION_REVISION",
    "CONTRACT_CHANGE",
    "MECHANICAL_REPAIR",
    "REPO_PATH_REPAIR",
    "BRANCH_RECOVERY",
    "PERMISSION_RECOVERY",
    "CONTEXT_RECOVERY",
    "PRIVATE_HANDOFF_RECOVERY",
    "VERIFICATION_RERUN",
    "STALE_SHA_RERUN",
    "GIT_APPROVAL_EVENT",
    "BLOCKED_RECOVERY",
)
CONTRACT_FIELDS = (
    "critical_invariants",
    "acceptance_condition",
    "failure_preservation",
    "required_negative_tests",
)
OPTIONAL_CONTRACT_FIELDS = (
    "strict_type_requirements",
    "provenance_requirements",
    "publication_rules",
    "migration_rules",
    "authorization_rules",
)
CONTRACT_SENSITIVE_TRIGGERS = frozenset({
    "public_api",
    "database_migration",
    "cross_system_exchange",
    "irreversible_publication",
    "unrecoverable_stable_replacement",
    "security_authorization_boundary",
    "external_provider_contract",
    "untrusted_high_impact_input",
    "commit_sha_locked_release",
    "regulatory_privacy_retention",
})
NON_SUFFICIENT_PROFILE_REASONS = frozenset({
    "has schema", "has private data", "has stable file", "needs tests",
})
WORKFLOW_ORDER = {
    "STANDARD": (
        "SUPERVISOR_PREFLIGHT",
        "EXPLORER_PLAN",
        "USER_PLAN_APPROVAL",
        "IMPLEMENTER",
        "EVALUATOR_REVIEWER_PARALLEL",
        "DOC_CURATOR",
    ),
    "CONTRACT_SENSITIVE": (
        "SUPERVISOR_PREFLIGHT",
        "EXPLORER_CONTRACT_PLAN",
        "USER_PLAN_CONTRACT_APPROVAL",
        "IMPLEMENTER_CANDIDATE",
        "REVIEWER_FOCUSED_PRECHECK",
        "IMPLEMENTER_FOCUSED_REPAIR_IF_REQUIRED",
        "FINAL_CANDIDATE_SHA_FREEZE",
        "EVALUATOR_LOCKED_GATE_REVIEWER_FINAL_CONFIRMATION",
        "DOC_CURATOR",
        "USER_INTEGRATION_APPROVAL",
    ),
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_METRIC_BY_EVENT = {
    "IMPLEMENTATION_REVISION": "semantic_revision_count",
    "CONTRACT_CHANGE": "semantic_revision_count",
    "MECHANICAL_REPAIR": "mechanical_repair_count",
    "REPO_PATH_REPAIR": "mechanical_repair_count",
    "BRANCH_RECOVERY": "mechanical_repair_count",
    "PERMISSION_RECOVERY": "permission_recovery_count",
    "CONTEXT_RECOVERY": "context_recovery_count",
    "PRIVATE_HANDOFF_RECOVERY": "permission_recovery_count",
    "VERIFICATION_RERUN": "verification_rerun_count",
    "STALE_SHA_RERUN": "stale_sha_verification_count",
    "GIT_APPROVAL_EVENT": "git_approval_interruption_count",
}
SEMANTIC_REPAIR_EVENTS = frozenset({"IMPLEMENTATION_REVISION", "CONTRACT_CHANGE"})


def event_metric(event_kind: str) -> str | None:
    if event_kind not in EVENT_KINDS:
        raise RefinementError("event_kind is invalid")
    return _METRIC_BY_EVENT.get(event_kind)


class RefinementError(RuntimeError):
    """Invalid or inconsistent refinement metadata."""


def classify_task_profile(facts: dict[str, bool]) -> str:
    """Classify explicit preflight facts; this intentionally does not inspect task prose."""
    if not isinstance(facts, dict) or any(not isinstance(key, str) or not isinstance(value, bool) for key, value in facts.items()):
        raise RefinementError("profile facts must be boolean flags")
    return "CONTRACT_SENSITIVE" if any(
        facts.get(trigger, False) for trigger in CONTRACT_SENSITIVE_TRIGGERS
    ) else "STANDARD"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise RefinementError(f"{label} is invalid")
    return value


def _relative_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RefinementError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RefinementError(f"{label} must be project-relative")
    return path.as_posix()


def _string_list(value: list[str], label: str, *, paths: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RefinementError(f"{label} must be a list of strings")
    return [_relative_path(item, label) for item in value] if paths else list(value)


def _profile_reason(profile: str, reason: str, triggers: list[str]) -> tuple[str, list[str]]:
    clean_reason = reason.strip() if isinstance(reason, str) else ""
    clean_triggers = _string_list(triggers, "profile triggers")
    if profile == "CONTRACT_SENSITIVE":
        if not clean_reason or clean_reason.lower().strip(" .:") in NON_SUFFICIENT_PROFILE_REASONS:
            raise RefinementError("CONTRACT_SENSITIVE requires a specific risk reason")
        if not set(clean_triggers) & CONTRACT_SENSITIVE_TRIGGERS:
            raise RefinementError("CONTRACT_SENSITIVE requires an applicable risk trigger")
    return clean_reason, clean_triggers


def _contract_projection(value: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, dict):
        raise RefinementError("contract must be an object")
    required = value.get("required_now", value)
    if not isinstance(required, dict):
        raise RefinementError("required_now must be an object")
    allowed = set(CONTRACT_FIELDS) | set(OPTIONAL_CONTRACT_FIELDS)
    unknown = set(required) - allowed
    if unknown:
        raise RefinementError("frozen contract contains unsupported fields")
    invariants = required.get("critical_invariants")
    acceptance = required.get("acceptance_condition")
    if not isinstance(invariants, list) or not invariants:
        raise RefinementError("critical_invariants must not be empty")
    ids: set[str] = set()
    normalized_invariants: list[dict[str, str]] = []
    for item in invariants:
        if not isinstance(item, dict):
            raise RefinementError("critical invariant is invalid")
        invariant_id = _safe_id(str(item.get("invariant_id", "")), "invariant_id")
        requirement = str(item.get("requirement", "")).strip()
        authority_ref = str(item.get("authority_ref", "")).strip()
        if not requirement or not authority_ref or invariant_id in ids:
            raise RefinementError("critical invariant must be unique and traceable")
        ids.add(invariant_id)
        normalized_invariants.append({
            "invariant_id": invariant_id,
            "requirement": requirement,
            "authority_ref": authority_ref,
        })
    if not isinstance(acceptance, dict):
        raise RefinementError("acceptance_condition is invalid")
    acceptance_summary = str(acceptance.get("summary", "")).strip()
    acceptance_ref = str(acceptance.get("authority_ref", "")).strip()
    if not acceptance_summary or not acceptance_ref:
        raise RefinementError("acceptance_condition must be traceable")
    preservation = required.get("failure_preservation", [])
    if not isinstance(preservation, list):
        raise RefinementError("failure_preservation must be a list")
    negatives = required.get("required_negative_tests", [])
    if not isinstance(negatives, list):
        raise RefinementError("required_negative_tests must be a list")
    normalized_negatives: list[dict[str, str]] = []
    for item in negatives:
        if not isinstance(item, dict):
            raise RefinementError("required negative test is invalid")
        invariant_ref = str(item.get("invariant_ref", ""))
        test = str(item.get("test", "")).strip()
        if invariant_ref not in ids or not test:
            raise RefinementError("required negative test must reference a critical invariant")
        normalized_negatives.append({"invariant_ref": invariant_ref, "test": test})
    frozen: dict[str, Any] = {
        "critical_invariants": normalized_invariants,
        "acceptance_condition": {"summary": acceptance_summary, "authority_ref": acceptance_ref},
        "failure_preservation": preservation,
        "required_negative_tests": normalized_negatives,
    }
    for field in OPTIONAL_CONTRACT_FIELDS:
        if required.get(field) not in (None, [], {}, ""):
            frozen[field] = required[field]
    advisory = {
        "optional_hardening": value.get("optional_hardening", {}),
        "deferred": value.get("deferred", {}),
    }
    return frozen, advisory


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefinementError(f"metadata is unavailable: {path.name}") from exc
    if not isinstance(value, dict):
        raise RefinementError(f"metadata is invalid: {path.name}")
    return value


@dataclass
class WorkflowRefinementStore:
    config: ProjectConfig

    def _data(self, workflow_id: str) -> Path:
        workflow_id = _safe_id(workflow_id, "workflow_id")
        return self.config.workflow_root / "tasks" / workflow_id / "data"

    def _path(self, workflow_id: str, name: str) -> Path:
        return self._data(workflow_id) / name

    def create_preflight(
        self,
        *,
        workflow_id: str,
        task_profile: str = "STANDARD",
        profile_reason: str = "",
        profile_triggers: list[str] | None = None,
        target_role: str,
        base_sha: str,
        current_target_sha: str,
        working_tree_status: str,
        canonical_input_paths: list[str],
        canonical_output_paths: list[str],
        dependency_toolchain: list[str],
        shared_workspace_access: str,
        private_artifact_owner: str,
        private_artifact_publisher: str,
        private_artifact_handoff_path: str,
        candidate_artifact_path: str,
        stable_artifact_path: str,
        expected_git_handoff: str,
        unresolved_items: list[str],
    ) -> dict[str, Any]:
        if task_profile not in TASK_PROFILES:
            raise RefinementError("task profile is invalid")
        profile_reason, profile_triggers = _profile_reason(
            task_profile, profile_reason, [] if profile_triggers is None else profile_triggers
        )
        if target_role not in ROLES:
            raise RefinementError("target role is invalid")
        if shared_workspace_access not in {"NONE", "READ", "READ_WRITE"}:
            raise RefinementError("shared workspace access is invalid")
        for value, label in ((base_sha, "base SHA"), (current_target_sha, "target SHA")):
            if not re.fullmatch(r"[0-9a-f]{7,64}", value):
                raise RefinementError(f"{label} is invalid")
        now = _now()
        existing_path = self._path(workflow_id, "preflight.json")
        created_at = _read_json(existing_path).get("created_at", now) if existing_path.exists() else now
        repo_id = "main" if target_role == "supervisor" else target_role
        payload = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "task_profile": task_profile,
            "profile": task_profile,
            "profile_reason": profile_reason or "STANDARD is the default proportional workflow.",
            "profile_triggers": profile_triggers,
            "standard_default_overridden": task_profile == "CONTRACT_SENSITIVE",
            "workflow_order": list(WORKFLOW_ORDER[task_profile]),
            "target_repo": repo_id,
            "target_worktree": str(self.config.repo(target_role).relative_to(self.config.root)),
            "base_branch": self.config.base_branch,
            "task_branch": self.config.role_branches.get(target_role, self.config.base_branch),
            "base_sha": base_sha,
            "current_target_sha": current_target_sha,
            "working_tree_status": working_tree_status,
            "canonical_input_paths": _string_list(canonical_input_paths, "input paths", paths=True),
            "canonical_output_paths": _string_list(canonical_output_paths, "output paths", paths=True),
            "dependency_toolchain": _string_list(dependency_toolchain, "dependency/toolchain"),
            "shared_workspace_access": shared_workspace_access,
            "shared_workspace_root": "shared_workspace",
            "private_artifact_owner": private_artifact_owner,
            "private_artifact_publisher": private_artifact_publisher,
            "private_artifact_handoff_path": _relative_path(private_artifact_handoff_path, "private handoff path") if private_artifact_handoff_path else "",
            "candidate_artifact_path": _relative_path(candidate_artifact_path, "candidate artifact path") if candidate_artifact_path else "",
            "stable_artifact_path": _relative_path(stable_artifact_path, "stable artifact path") if stable_artifact_path else "",
            "expected_git_handoff": expected_git_handoff,
            "unresolved_preflight_items": _string_list(unresolved_items, "unresolved preflight items"),
            "effective_plan_revision": None,
            "effective_contract_revision": None,
            "effective_decision_refs": [],
            "semantic_repair_count": 0,
            "proportionality_verdict": "UNCERTAIN",
            "created_at": created_at,
            "last_updated_at": now,
        }
        _atomic_json(existing_path, payload)
        self._project_preflight(payload)
        self._ensure_metrics(workflow_id)
        return payload

    def _project_preflight(self, payload: dict[str, Any]) -> None:
        lines = [
            "# Workflow Preflight",
            "",
            f"- Workflow ID: `{payload['workflow_id']}`",
            f"- Task profile: `{payload['task_profile']}`",
            f"- Profile reason: {payload.get('profile_reason', 'Legacy workflow; profile reason unavailable.')}",
            "- Profile triggers: " + (", ".join(f"`{item}`" for item in payload.get("profile_triggers", [])) or "None"),
            f"- Standard default overridden: `{str(payload.get('standard_default_overridden', False)).lower()}`",
            f"- Effective PLAN revision: `{payload.get('effective_plan_revision', 'UNSET')}`",
            f"- Effective contract revision: `{payload.get('effective_contract_revision', 'UNSET')}`",
            "- Active decision refs: " + (", ".join(payload.get("effective_decision_refs", [])) or "None"),
            f"- Semantic repair count: `{payload.get('semantic_repair_count', 0)}`",
            f"- Current proportionality verdict: `{payload.get('proportionality_verdict', 'UNCERTAIN')}`",
            f"- Target repo/worktree: `{payload['target_repo']}` / `{payload['target_worktree']}`",
            f"- Base/task branch: `{payload['base_branch']}` / `{payload['task_branch']}`",
            f"- Base/current SHA: `{payload['base_sha']}` / `{payload['current_target_sha']}`",
            f"- Working tree: {payload['working_tree_status']}",
            f"- Shared workspace access: `{payload['shared_workspace_access']}`",
            f"- Private owner/publisher: {payload['private_artifact_owner']} / {payload['private_artifact_publisher']}",
            f"- Expected Git handoff: {payload['expected_git_handoff']}",
            f"- Updated at: `{payload['last_updated_at']}`",
            "",
            "## Canonical paths",
            "",
            "- Inputs: " + (", ".join(f"`{item}`" for item in payload["canonical_input_paths"]) or "None"),
            "- Outputs: " + (", ".join(f"`{item}`" for item in payload["canonical_output_paths"]) or "None"),
            "- Private handoff: " + (f"`{payload['private_artifact_handoff_path']}`" if payload["private_artifact_handoff_path"] else "None"),
            "- Candidate/stable: " + (f"`{payload['candidate_artifact_path']}` / `{payload['stable_artifact_path']}`" if payload["candidate_artifact_path"] else "None"),
            "",
            "## Unresolved items",
            "",
            *([f"- {item}" for item in payload["unresolved_preflight_items"]] or ["- None"]),
            "",
        ]
        path = self.config.workflow_root / "tasks" / str(payload["workflow_id"]) / "preflight.md"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        text = "\n".join(lines)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        try:
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def freeze_contract(self, workflow_id: str, contract: dict[str, Any], approval_summary: str) -> dict[str, Any]:
        preflight = _read_json(self._path(workflow_id, "preflight.json"))
        if preflight.get("task_profile") != "CONTRACT_SENSITIVE":
            raise RefinementError("contract freeze requires CONTRACT_SENSITIVE profile")
        frozen_contract, advisory = _contract_projection(contract)
        path = self._path(workflow_id, "contract-freeze.json")
        now = _now()
        if path.exists():
            current = _read_json(path)
            if current.get("contract") == frozen_contract:
                return current
            current["status"] = "CONTRACT_CHANGE_REQUIRED"
            current["pending_change"] = {
                "original_contract": current.get("contract"),
                "proposed_contract": frozen_contract,
                "impact": "Existing implementation and verification may be stale.",
                "requested_at": now,
            }
            _atomic_json(path, current)
            self.record_event(workflow_id, "contract-freeze", "CONTRACT_CHANGE", 0, "metadata")
            return current
        payload = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "status": "CONTRACT_FROZEN",
            "revision": 1,
            "contract": frozen_contract,
            "advisory": advisory,
            "approval_summary": approval_summary,
            "frozen_at": now,
            "revision_history": [{"revision": 1, "approved_at": now, "approval_summary": approval_summary}],
        }
        _atomic_json(path, payload)
        self._update_preflight_summary(workflow_id, effective_contract_revision=1)
        return payload

    def approve_contract_change(self, workflow_id: str, approval_summary: str) -> dict[str, Any]:
        path = self._path(workflow_id, "contract-freeze.json")
        payload = _read_json(path)
        pending = payload.get("pending_change")
        if payload.get("status") != "CONTRACT_CHANGE_REQUIRED" or not isinstance(pending, dict):
            raise RefinementError("no contract change is awaiting approval")
        revision = int(payload.get("revision", 1)) + 1
        now = _now()
        payload.update({
            "status": "CONTRACT_FROZEN",
            "revision": revision,
            "contract": pending["proposed_contract"],
            "approval_summary": approval_summary,
            "frozen_at": now,
        })
        payload.pop("pending_change", None)
        history = payload.setdefault("revision_history", [])
        assert isinstance(history, list)
        history.append({"revision": revision, "approved_at": now, "approval_summary": approval_summary})
        _atomic_json(path, payload)
        self._update_preflight_summary(workflow_id, effective_contract_revision=revision)
        return payload

    def set_effective_authority(
        self,
        workflow_id: str,
        *,
        plan_revision: str,
        plan_projection: dict[str, Any],
        contract_revision: int | None = None,
        approved_decisions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Save one effective projection while retaining replaced authority for audit."""
        _safe_id(plan_revision, "plan revision")
        if not isinstance(plan_projection, dict):
            raise RefinementError("plan projection must be an object")
        decisions = [] if approved_decisions is None else approved_decisions
        if not isinstance(decisions, list):
            raise RefinementError("approved decisions must be a list")
        active_rules = plan_projection.get("rules", [])
        if not isinstance(active_rules, list):
            raise RefinementError("plan projection rules must be a list")
        by_ref: dict[str, dict[str, str]] = {}
        for rule in active_rules:
            if not isinstance(rule, dict):
                raise RefinementError("plan rule is invalid")
            authority_ref = str(rule.get("authority_ref", "")).strip()
            text = str(rule.get("text", "")).strip()
            if not authority_ref or not text or authority_ref in by_ref:
                raise RefinementError("plan rules must have unique authority refs")
            by_ref[authority_ref] = {"authority_ref": authority_ref, "text": text}
        normalized_decisions: list[dict[str, Any]] = []
        replacements: dict[str, str] = {}
        for decision in decisions:
            if not isinstance(decision, dict):
                raise RefinementError("approved decision is invalid")
            authority_ref = str(decision.get("authority_ref", "")).strip()
            replacement = str(decision.get("replacement", "")).strip()
            supersedes = decision.get("supersedes", [])
            approved_at = str(decision.get("approved_at", "")).strip()
            if not authority_ref or not replacement or not approved_at or not isinstance(supersedes, list):
                raise RefinementError("approved decision authority is incomplete")
            if any(not isinstance(item, str) or not item for item in supersedes):
                raise RefinementError("supersedes refs are invalid")
            for old_ref in supersedes:
                previous = replacements.get(old_ref)
                if previous is not None and previous != replacement:
                    raise RefinementError("AUTHORITY_CONFLICT")
                replacements[old_ref] = replacement
                by_ref.pop(old_ref, None)
            normalized_decisions.append({
                "authority_ref": authority_ref,
                "supersedes": list(supersedes),
                "replacement": replacement,
                "approved_at": approved_at,
            })
        previous_path = self._path(workflow_id, "authority.json")
        previous = _read_json(previous_path) if previous_path.exists() else {}
        history = list(previous.get("history", [])) if isinstance(previous.get("history", []), list) else []
        if previous.get("active"):
            history.append(previous["active"])
        contract_projection: dict[str, Any] = {}
        contract_path = self._path(workflow_id, "contract-freeze.json")
        if contract_revision is not None:
            if not contract_path.exists():
                raise RefinementError("effective contract revision is unavailable")
            contract_payload = _read_json(contract_path)
            if contract_payload.get("status") != "CONTRACT_FROZEN" or contract_payload.get("revision") != contract_revision:
                raise RefinementError("effective contract revision does not match")
            contract_projection = contract_payload.get("contract", {})
        active = {
            "effective_plan_revision": plan_revision,
            "effective_contract_revision": contract_revision,
            "effective_plan_projection": {**plan_projection, "rules": list(by_ref.values())},
            "effective_contract_projection": contract_projection,
            "effective_decision_refs": [item["authority_ref"] for item in normalized_decisions],
            "effective_decisions": normalized_decisions,
            "updated_at": _now(),
        }
        payload = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "status": "ACTIVE_AUTHORITY",
            "active": active,
            "history": history,
        }
        _atomic_json(previous_path, payload)
        self._update_preflight_summary(
            workflow_id,
            effective_plan_revision=plan_revision,
            effective_contract_revision=contract_revision,
            effective_decision_refs=active["effective_decision_refs"],
        )
        return payload

    def effective_authority(self, workflow_id: str) -> dict[str, Any]:
        path = self._path(workflow_id, "authority.json")
        if not path.exists():
            return {
                "status": "LEGACY_AUTHORITY_WARNING",
                "effective_plan_revision": None,
                "effective_contract_revision": None,
                "effective_decision_refs": [],
            }
        payload = _read_json(path)
        if payload.get("status") == "AUTHORITY_CONFLICT":
            raise RefinementError("AUTHORITY_CONFLICT")
        active = payload.get("active")
        if payload.get("status") != "ACTIVE_AUTHORITY" or not isinstance(active, dict):
            raise RefinementError("AUTHORITY_CONFLICT")
        return {"status": "ACTIVE_AUTHORITY", **active}

    def classify_reviewer_finding(
        self, workflow_id: str, finding: dict[str, Any]
    ) -> str:
        contract = _read_json(self._path(workflow_id, "contract-freeze.json")).get("contract", {})
        invariants = contract.get("critical_invariants", []) if isinstance(contract, dict) else []
        active_refs = {
            str(item.get("authority_ref", ""))
            for item in invariants if isinstance(item, dict)
        }
        acceptance = contract.get("acceptance_condition", {}) if isinstance(contract, dict) else {}
        if isinstance(acceptance, dict) and acceptance.get("authority_ref"):
            active_refs.add(str(acceptance["authority_ref"]))
        authority_path = self._path(workflow_id, "authority.json")
        if authority_path.exists():
            authority = _read_json(authority_path).get("active", {})
            if isinstance(authority, dict):
                active_refs.update(str(item) for item in authority.get("effective_decision_refs", []))
        authority_ref = str(finding.get("authority_ref", ""))
        gate = str(finding.get("affected_completion_gate", "")).strip()
        trigger = str(finding.get("real_trigger_or_risk", "")).strip()
        rationale = str(finding.get("why_advisory_is_insufficient", "")).strip()
        if authority_ref in active_refs and gate and trigger and rationale:
            return "REQUIRED_FIX"
        return "ADVISORY"

    def record_reviewer_assessment(
        self,
        workflow_id: str,
        *,
        correctness_verdict: str,
        proportionality_verdict: str,
        base_sha: str,
        final_candidate_sha: str,
        cumulative_observations: list[str],
    ) -> dict[str, Any]:
        if correctness_verdict not in {"PASS", "FAIL"}:
            raise RefinementError("correctness verdict is invalid")
        if proportionality_verdict not in {"PROPORTIONATE", "OVERDESIGNED", "UNCERTAIN"}:
            raise RefinementError("proportionality verdict is invalid")
        observations = _string_list(cumulative_observations, "cumulative observations")
        payload = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "correctness_verdict": correctness_verdict,
            "proportionality_verdict": proportionality_verdict,
            "base_sha": base_sha,
            "final_candidate_sha": final_candidate_sha,
            "cumulative_diff_reviewed": True,
            "cumulative_observations": observations,
            "recorded_at": _now(),
        }
        _atomic_json(self._path(workflow_id, "reviewer-assessment.json"), payload)
        self._update_preflight_summary(workflow_id, proportionality_verdict=proportionality_verdict)
        return payload

    def assess_semantic_repair(
        self,
        workflow_id: str,
        *,
        invariant_type: str,
        directly_required: bool,
        real_trigger_or_unacceptable_risk: bool,
        cumulative_scope_proportionate: bool,
    ) -> dict[str, Any]:
        metrics = self._ensure_metrics(workflow_id)
        semantic_count = int(metrics.get("semantic_revision_count", 0))
        path = self._path(workflow_id, "semantic-repair-assessment.json")
        previous = _read_json(path) if path.exists() else {"invariant_types": []}
        known = list(previous.get("invariant_types", []))
        is_new_type = invariant_type not in known
        requires_reassessment = semantic_count >= 1 and is_new_type
        classification = (
            "required"
            if directly_required and real_trigger_or_unacceptable_risk and cumulative_scope_proportionate
            else "advisory"
        )
        status = "PROPORTIONALITY_REASSESSMENT_REQUIRED" if requires_reassessment else "REPAIR_ALLOWED"
        if not is_new_type:
            status = "REPAIR_ALLOWED"
        payload = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "status": status,
            "semantic_repair_count": semantic_count,
            "invariant_type": invariant_type,
            "new_invariant_type": is_new_type,
            "suggested_classification": classification,
            "updated_at": _now(),
            "invariant_types": [*known, invariant_type] if is_new_type else known,
        }
        _atomic_json(path, payload)
        self._update_preflight_summary(workflow_id, semantic_repair_count=semantic_count)
        return payload

    def resolve_semantic_repair(
        self,
        workflow_id: str,
        *,
        classification: str,
        authority_ref: str = "",
        contract_revision: int | None = None,
    ) -> dict[str, Any]:
        path = self._path(workflow_id, "semantic-repair-assessment.json")
        payload = _read_json(path)
        if payload.get("status") != "PROPORTIONALITY_REASSESSMENT_REQUIRED":
            raise RefinementError("no proportionality reassessment is awaiting resolution")
        if classification not in {"required", "advisory", "deferred"}:
            raise RefinementError("semantic repair classification is invalid")
        if classification == "required":
            contract_path = self._path(workflow_id, "contract-freeze.json")
            if not contract_path.exists() or contract_revision is None or not authority_ref.strip():
                raise RefinementError("required repair needs approved frozen contract authority")
            contract = _read_json(contract_path)
            if contract.get("status") != "CONTRACT_FROZEN" or contract.get("revision") != contract_revision:
                raise RefinementError("required repair contract revision does not match")
            matching = [
                item
                for item in contract.get("contract", {}).get("critical_invariants", [])
                if isinstance(item, dict)
                and item.get("authority_ref") == authority_ref
                and item.get("invariant_id") == payload.get("invariant_type")
            ]
            if not matching:
                raise RefinementError("required repair is not in the active frozen contract")
            status = "REPAIR_ALLOWED"
        else:
            status = "NO_REWORK_REQUIRED"
        payload.update({
            "status": status,
            "resolution": classification,
            "authority_ref": authority_ref,
            "contract_revision": contract_revision,
            "resolved_at": _now(),
        })
        _atomic_json(path, payload)
        return payload

    def _update_preflight_summary(self, workflow_id: str, **changes: Any) -> None:
        path = self._path(workflow_id, "preflight.json")
        if not path.exists():
            return
        payload = _read_json(path)
        payload.update(changes)
        payload["last_updated_at"] = _now()
        _atomic_json(path, payload)
        self._project_preflight(payload)

    def freeze_final_candidate(
        self,
        workflow_id: str,
        *,
        repo_id: str,
        branch: str,
        base_sha: str,
        final_candidate_sha: str,
        commit_message: str,
        remote_ref: str,
        working_tree_clean: bool,
    ) -> dict[str, Any]:
        if repo_id not in ("main", *ROLES[1:]):
            raise RefinementError("repo_id is invalid")
        for value in (base_sha, final_candidate_sha):
            if not re.fullmatch(r"[0-9a-f]{7,64}", value):
                raise RefinementError("candidate SHA is invalid")
        contract_path = self._path(workflow_id, "contract-freeze.json")
        if contract_path.exists() and _read_json(contract_path).get("status") != "CONTRACT_FROZEN":
            raise RefinementError("contract change approval is required before SHA freeze")
        path = self._path(workflow_id, "final-candidate-sha.json")
        previous = _read_json(path) if path.exists() else None
        payload = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "status": "FINAL_CANDIDATE_SHA_FROZEN",
            "repo": repo_id,
            "repo_path": "main" if repo_id == "main" else repo_id,
            "branch": branch,
            "base_sha": base_sha,
            "final_candidate_sha": final_candidate_sha,
            "commit_message": commit_message,
            "remote_ref": remote_ref,
            "working_tree_clean": bool(working_tree_clean),
            "frozen_at": _now(),
        }
        if previous and previous.get("final_candidate_sha") != final_candidate_sha:
            verdict_path = self._path(workflow_id, "evaluator-verdict.json")
            if verdict_path.exists():
                verdict = _read_json(verdict_path)
                verdict["status"] = "STALE"
                verdict["stale_reason"] = "final candidate SHA changed"
                _atomic_json(verdict_path, verdict)
            self.record_event(workflow_id, "final-candidate", "STALE_SHA_RERUN", 0, "metadata")
        _atomic_json(path, payload)
        return payload

    def record_evaluator_verdict(self, workflow_id: str, candidate_sha: str, verdict: str) -> dict[str, Any]:
        frozen_path = self._path(workflow_id, "final-candidate-sha.json")
        if not frozen_path.exists():
            raise RefinementError("Evaluator requires a frozen final candidate SHA")
        frozen = _read_json(frozen_path)
        if frozen.get("status") != "FINAL_CANDIDATE_SHA_FROZEN":
            raise RefinementError("Evaluator requires a frozen final candidate SHA")
        if candidate_sha != frozen.get("final_candidate_sha"):
            raise RefinementError("Evaluator may only verify the frozen final candidate SHA")
        payload = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "candidate_sha": candidate_sha,
            "verdict": verdict,
            "status": "CURRENT",
            "recorded_at": _now(),
        }
        _atomic_json(self._path(workflow_id, "evaluator-verdict.json"), payload)
        return payload

    def record_event(
        self, workflow_id: str, task_id: str, event_kind: str, round_number: int, nonce: str
    ) -> dict[str, Any]:
        if event_kind not in EVENT_KINDS:
            raise RefinementError("event_kind is invalid")
        if not isinstance(round_number, int) or round_number < 0:
            raise RefinementError("round is invalid")
        event = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            "task_id": _safe_id(task_id, "task_id"),
            "event_kind": event_kind,
            "round": round_number,
            "nonce": nonce,
            "at": _now(),
        }
        path = self._path(workflow_id, "refinement-events.json")
        payload = _read_json(path) if path.exists() else {"workflow_id": workflow_id, "events": []}
        events = payload.setdefault("events", [])
        assert isinstance(events, list)
        events.append(event)
        _atomic_json(path, payload)
        self._update_metrics(workflow_id, event_kind)
        return event

    def _ensure_metrics(self, workflow_id: str) -> dict[str, Any]:
        path = self._path(workflow_id, "retrospective-metrics.json")
        if path.exists():
            return _read_json(path)
        payload = {
            "workflow_id": _safe_id(workflow_id, "workflow_id"),
            **{name: 0 for name in (
                "semantic_revision_count", "mechanical_repair_count",
                "permission_recovery_count", "context_recovery_count",
                "verification_rerun_count", "stale_sha_verification_count",
                "git_approval_interruption_count", "approved_git_transaction_count",
                "invalidated_git_transaction_count",
            )},
            "last_updated_at": _now(),
        }
        _atomic_json(path, payload)
        return payload

    def _update_metrics(self, workflow_id: str, event_kind: str) -> None:
        payload = self._ensure_metrics(workflow_id)
        metric = event_metric(event_kind)
        if metric:
            payload[metric] = int(payload.get(metric, 0)) + 1
        payload["last_updated_at"] = _now()
        _atomic_json(self._path(workflow_id, "retrospective-metrics.json"), payload)

    def increment_metric(self, workflow_id: str, metric: str) -> None:
        payload = self._ensure_metrics(workflow_id)
        if metric not in payload or not metric.endswith("_count"):
            raise RefinementError("metric is invalid")
        payload[metric] = int(payload[metric]) + 1
        payload["last_updated_at"] = _now()
        _atomic_json(self._path(workflow_id, "retrospective-metrics.json"), payload)
