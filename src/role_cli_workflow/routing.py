"""Small, stateless workflow routing rules."""

from __future__ import annotations

from typing import Literal

ExecutionProfile = Literal["DIRECT", "VERIFY", "REVIEW"]
EXECUTION_PROFILES: tuple[ExecutionProfile, ...] = ("DIRECT", "VERIFY", "REVIEW")


def resolve_execution_profile(value: str) -> ExecutionProfile:
    normalized = value.strip().upper()
    if normalized not in EXECUTION_PROFILES:
        raise ValueError("execution profile must be DIRECT, VERIFY, or REVIEW")
    return normalized  # type: ignore[return-value]


def route_roles(profile: str, *, needs_exploration: bool = False, needs_docs: bool = False) -> tuple[str, ...]:
    """Return the ordinary role sequence; optional roles are explicit inputs."""
    resolved = resolve_execution_profile(profile)
    if resolved == "DIRECT":
        roles: list[str] = ["supervisor"]
    elif resolved == "VERIFY":
        roles = ["implementer", "evaluator", "supervisor"]
    else:
        roles = ["implementer", "evaluator", "reviewer", "supervisor"]
        if needs_exploration:
            roles.insert(0, "explorer")
    if needs_docs:
        roles.insert(-1, "doc-curator")
    return tuple(roles)
