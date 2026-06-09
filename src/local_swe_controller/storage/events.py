"""Structured trace event schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EventName(str, Enum):
    RUN_STARTED = "run_started"
    POLICY_COMPILED = "policy_compiled"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_FINISHED = "validation_finished"
    MODEL_CALLED = "model_called"
    GENERATED_TESTS_CREATED = "generated_tests_created"
    PATCH_GENERATED = "patch_generated"
    PATCH_POLICY_CHECKED = "patch_policy_checked"
    PATCH_VALIDATION_STARTED = "patch_validation_started"
    PATCH_VALIDATION_FINISHED = "patch_validation_finished"
    SECURITY_GATE_STARTED = "security_gate_started"
    SECURITY_GATE_FINISHED = "security_gate_finished"
    RUN_FINISHED = "run_finished"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    event: EventName
    payload: dict[str, object]


class RunStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_root: str
    goal: str | None = None
    command_type: Literal["validate", "repair"]
    generate_tests: bool = False
    max_iters: int | None = None
    max_candidates: int | None = None
    max_diff_lines: int | None = None
    timeout_per_command: int | None = None
    max_total_runtime_seconds: int | None = None


class PolicyCompiledPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_path: str
    policy_hash: str


class ValidationStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str
    artifact_dir: str
    worktree_path: str | None = None


class ValidationFinishedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str
    status: str
    failure_class: str | None = None
    warnings: list[str] = Field(default_factory=list)
    target_repo_changed: bool
    failed_command: list[str] | None = None


class ModelCalledPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str
    model_profile: str
    estimated_input_tokens: int | None = None


class GeneratedTestsCreatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_path: str
    model_profile: str
    accepted: bool
    reasons: list[str] = Field(default_factory=list)


class PatchGeneratedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    candidate: int
    model_profile: str
    patch_path: str


class PatchPolicyCheckedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    candidate: int
    accepted: bool
    reasons: list[str] = Field(default_factory=list)


class PatchValidationStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    candidate: int
    patch_path: str
    worktree_path: str | None = None


class PatchValidationFinishedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    candidate: int
    status: str
    failure_class: str | None = None
    worktree_path: str
    target_repo_changed: bool


class SecurityGateStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str]
    tool_name: str | None = None
    optional: bool = False


class SecurityGateFinishedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str]
    tool_name: str | None = None
    status: str
    finding_summary: str | None = None
    warnings: list[str] = Field(default_factory=list)


class RunFinishedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str
    stop_reason: str | None = None
    failure_class: str | None = None
    target_repo_changed: bool
    duration_seconds: float
    summary_path: str


EVENT_PAYLOAD_MODELS: dict[EventName, type[BaseModel]] = {
    EventName.RUN_STARTED: RunStartedPayload,
    EventName.POLICY_COMPILED: PolicyCompiledPayload,
    EventName.VALIDATION_STARTED: ValidationStartedPayload,
    EventName.VALIDATION_FINISHED: ValidationFinishedPayload,
    EventName.MODEL_CALLED: ModelCalledPayload,
    EventName.GENERATED_TESTS_CREATED: GeneratedTestsCreatedPayload,
    EventName.PATCH_GENERATED: PatchGeneratedPayload,
    EventName.PATCH_POLICY_CHECKED: PatchPolicyCheckedPayload,
    EventName.PATCH_VALIDATION_STARTED: PatchValidationStartedPayload,
    EventName.PATCH_VALIDATION_FINISHED: PatchValidationFinishedPayload,
    EventName.SECURITY_GATE_STARTED: SecurityGateStartedPayload,
    EventName.SECURITY_GATE_FINISHED: SecurityGateFinishedPayload,
    EventName.RUN_FINISHED: RunFinishedPayload,
}


def validate_trace_event(
    *,
    event: str,
    payload: dict[str, object],
    timestamp: datetime,
) -> EventEnvelope:
    """Validate one structured trace event."""

    event_name = EventName(event)
    EVENT_PAYLOAD_MODELS[event_name].model_validate(payload)
    return EventEnvelope(timestamp=timestamp, event=event_name, payload=payload)
