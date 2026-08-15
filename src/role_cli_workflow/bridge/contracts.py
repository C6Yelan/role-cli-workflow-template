"""Task Contract and result validation without authority projections."""

from __future__ import annotations

import json
from typing import Any

from .security import (
    LARGE_TASK_WARNING_BYTES,
    MAX_PAYLOAD_BYTES,
    ValidationError,
    validate_nonce,
    validate_ref_id,
    validate_task_id,
    validate_text,
    validate_worker_role,
    validate_workflow_id,
)

SUGGESTED_ROLE_SECTIONS = {
    "explorer": ("requirements", "scope", "acceptance_criteria", "risks"),
    "implementer": ("changes", "tests", "deviations", "remaining_work"),
    "evaluator": ("validation_verdict", "test_evidence", "failures", "coverage_gaps"),
    "reviewer": ("correctness_verdict", "proportionality_verdict", "findings", "risks"),
    "doc-curator": ("documentation_changes", "validation_summary", "open_items"),
}


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("payload must be JSON serializable") from exc


def json_size(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8"))


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{field} must be a list of strings")
    return value


def validate_authorized_refs(value: Any) -> list[str]:
    refs = _string_list(value, "authorized_refs")
    validated = [validate_ref_id(item) for item in refs]
    if len(validated) != len(set(validated)):
        raise ValidationError("authorized_refs must be unique")
    return validated


def build_task_contract(
    workflow_id: str,
    role: str,
    task_id: str,
    objective: str,
    deliverables: Any,
    acceptance_criteria: Any,
    constraints: Any,
    authorized_refs: Any,
    input_candidate_sha: str = "",
) -> tuple[dict[str, object], list[str]]:
    if input_candidate_sha and (
        len(input_candidate_sha) < 40
        or any(character not in "0123456789abcdef" for character in input_candidate_sha.lower())
    ):
        raise ValidationError("input_candidate_sha must be a full Git commit SHA")
    validated_role = validate_worker_role(role)
    if validated_role in {"implementer", "evaluator", "reviewer", "doc-curator"} and not input_candidate_sha:
        raise ValidationError("writer and validator tasks require input_candidate_sha")
    contract = {
        "schema_version": 2,
        "workflow_id": validate_workflow_id(workflow_id),
        "task_id": validate_task_id(task_id),
        "role": validated_role,
        "objective": validate_text(objective, field="objective", maximum=MAX_PAYLOAD_BYTES),
        "deliverables": _string_list(deliverables, "deliverables"),
        "acceptance_criteria": _string_list(acceptance_criteria, "acceptance_criteria"),
        "constraints": _string_list(constraints, "constraints"),
        "authorized_refs": validate_authorized_refs(authorized_refs),
        "input_candidate_sha": input_candidate_sha.lower(),
    }
    size = json_size(contract)
    if size > MAX_PAYLOAD_BYTES:
        raise ValidationError("task contract exceeds the technical payload limit")
    return contract, (["TASK_CONTRACT_LARGE"] if size > LARGE_TASK_WARNING_BYTES else [])


def validate_source_refs(value: Any, authorized_refs: set[str]) -> list[str]:
    refs = _string_list(value, "source_refs")
    validated = [validate_ref_id(item) for item in refs]
    if any(item not in authorized_refs for item in validated):
        raise ValidationError("source ref is not authorized by the active task")
    return validated


def validate_result_envelope(
    workflow_id: str,
    role: str,
    task_id: str,
    round_number: int,
    nonce: str,
    summary: str,
    decisions: Any,
    open_issues: Any,
    evidence: Any,
    sections: Any,
    source_refs: Any,
    authorized_refs: set[str],
    input_candidate_sha: str,
    produced_candidate_sha: str,
) -> dict[str, object]:
    if not isinstance(round_number, int) or not 0 <= round_number <= 100:
        raise ValidationError("round is invalid")
    if not isinstance(decisions, list) or not isinstance(open_issues, list) or not isinstance(evidence, list):
        raise ValidationError("decisions, open_issues, and evidence must be lists")
    if not isinstance(sections, dict) or any(not isinstance(key, str) for key in sections):
        raise ValidationError("sections must be a named object")
    validated_role = validate_worker_role(role)
    if validated_role == "evaluator" and sections.get("validation_verdict") not in {"PASS", "FAIL", "NOT_VERIFIED"}:
        raise ValidationError("evaluator validation_verdict is invalid")
    if validated_role == "reviewer":
        if sections.get("correctness_verdict") not in {"PASS", "FAIL"}:
            raise ValidationError("reviewer correctness_verdict is invalid")
        if sections.get("proportionality_verdict") not in {"PROPORTIONATE", "OVERDESIGNED", "UNCERTAIN"}:
            raise ValidationError("reviewer proportionality_verdict is invalid")
    if validated_role in {"implementer", "doc-curator"} and not produced_candidate_sha:
        raise ValidationError("writer result requires produced_candidate_sha")
    bound_sha = input_candidate_sha.lower()
    if validated_role in {"evaluator", "reviewer"} and produced_candidate_sha.lower() != bound_sha:
        raise ValidationError("result evidence is for the wrong candidate SHA")
    if produced_candidate_sha and (
        len(produced_candidate_sha) < 40
        or any(character not in "0123456789abcdef" for character in produced_candidate_sha.lower())
    ):
        raise ValidationError("produced_candidate_sha must be a full Git commit SHA")
    envelope = {
        "schema_version": 2,
        "workflow_id": validate_workflow_id(workflow_id),
        "task_id": validate_task_id(task_id),
        "role": validated_role,
        "round": round_number,
        "nonce": validate_nonce(nonce),
        "input_candidate_sha": bound_sha,
        "produced_candidate_sha": produced_candidate_sha.lower(),
        "summary": validate_text(summary, field="summary", maximum=MAX_PAYLOAD_BYTES),
        "decisions": decisions,
        "open_issues": open_issues,
        "evidence": evidence,
        "sections": sections,
        "source_refs": validate_source_refs(source_refs, authorized_refs),
    }
    if json_size(envelope) > MAX_PAYLOAD_BYTES:
        raise ValidationError("result exceeds the technical payload limit")
    return envelope


def result_view(envelope: dict[str, object], view: str) -> object:
    if view == "summary":
        return {
            "summary": envelope["summary"],
            "produced_candidate_sha": envelope.get("produced_candidate_sha", ""),
            "sections": envelope.get("sections", {}),
        }
    if view == "full":
        return envelope
    raise ValidationError("view must be summary or full")
