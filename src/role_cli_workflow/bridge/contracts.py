"""Loose structural validation for task and result payloads."""

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
    "explorer": ("requirements", "scope", "api_contract", "acceptance_criteria", "risks"),
    "implementer": ("changes", "tests", "deviations", "remaining_work"),
    "evaluator": ("verdict", "test_evidence", "failures", "coverage_gaps"),
    "reviewer": ("verdict", "findings", "required_fixes", "risks"),
    "doc-curator": ("final_summary", "decisions", "validation_summary", "open_items", "artifact_index"),
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


def ref_type(ref_id: str) -> str:
    return ref_id.split(":", 1)[0]


def validate_context_refs(value: Any) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValidationError("context_refs must be a list")
    refs: list[dict[str, object]] = []
    seen: set[str] = set()
    for ref in value:
        if not isinstance(ref, dict) or set(ref) != {"ref_id", "label", "allowed_sections"}:
            raise ValidationError("context ref schema is invalid")
        ref_id = validate_ref_id(ref.get("ref_id"))  # type: ignore[arg-type]
        label = validate_text(ref.get("label"), field="label", maximum=MAX_PAYLOAD_BYTES)  # type: ignore[arg-type]
        sections = _string_list(ref.get("allowed_sections"), "allowed_sections")
        if not sections or ref_id in seen or len(sections) != len(set(sections)):
            raise ValidationError("context ref schema is invalid")
        seen.add(ref_id)
        refs.append({"ref_id": ref_id, "label": label, "allowed_sections": sections})
    return refs


def build_task_contract(
    workflow_id: str,
    role: str,
    task_id: str,
    objective: str,
    deliverables: Any,
    acceptance_criteria: Any,
    constraints: Any,
    context_refs: Any,
) -> tuple[dict[str, object], list[str]]:
    contract = {
        "workflow_id": validate_workflow_id(workflow_id),
        "task_id": validate_task_id(task_id),
        "role": validate_worker_role(role),
        "objective": validate_text(objective, field="objective", maximum=MAX_PAYLOAD_BYTES),
        "deliverables": _string_list(deliverables, "deliverables"),
        "acceptance_criteria": _string_list(acceptance_criteria, "acceptance_criteria"),
        "constraints": _string_list(constraints, "constraints"),
        "context_refs": validate_context_refs(context_refs),
    }
    size = json_size(contract)
    if size > MAX_PAYLOAD_BYTES:
        raise ValidationError("task contract exceeds the technical payload limit")
    warnings = ["TASK_CONTRACT_LARGE"] if size > LARGE_TASK_WARNING_BYTES else []
    return contract, warnings


def validate_source_refs(value: Any, authorized: dict[str, set[str]]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValidationError("source_refs must be a list")
    refs: list[dict[str, str]] = []
    for ref in value:
        if not isinstance(ref, dict) or set(ref) != {"ref_id", "section"}:
            raise ValidationError("source ref schema is invalid")
        ref_id = validate_ref_id(ref.get("ref_id"))  # type: ignore[arg-type]
        section = validate_text(ref.get("section"), field="section", maximum=MAX_PAYLOAD_BYTES)  # type: ignore[arg-type]
        if section not in authorized.get(ref_id, set()):
            raise ValidationError("source ref is not authorized by the active task")
        refs.append({"ref_id": ref_id, "section": section})
    return refs


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
    authorized_refs: dict[str, set[str]],
) -> dict[str, object]:
    if not isinstance(round_number, int) or not 0 <= round_number <= 100:
        raise ValidationError("round is invalid")
    if not isinstance(decisions, list) or not isinstance(open_issues, list) or not isinstance(evidence, list):
        raise ValidationError("decisions, open_issues, and evidence must be lists")
    if not isinstance(sections, dict) or any(not isinstance(key, str) for key in sections):
        raise ValidationError("sections must be a named object")
    envelope = {
        "workflow_id": validate_workflow_id(workflow_id),
        "task_id": validate_task_id(task_id),
        "role": validate_worker_role(role),
        "round": round_number,
        "nonce": validate_nonce(nonce),
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


def result_section(envelope: dict[str, object], section: str) -> object:
    if section in {"summary", "decisions", "open_issues", "evidence", "source_refs"}:
        return envelope[section]
    sections = envelope.get("sections")
    if not isinstance(sections, dict) or section not in sections:
        raise ValidationError("result section is unavailable")
    return sections[section]
