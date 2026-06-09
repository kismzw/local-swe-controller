"""Typed orchestration state for optional repair graphs."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from local_swe_controller.models import FailureClass, RunStatus, ValidationReport
from local_swe_controller.repair.controller import RepairResult


class GraphExecutionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class RepairGraphNode(str, Enum):
    INTAKE = "intake"
    COMPILE_POLICY = "compile_policy"
    BASELINE_VALIDATE = "baseline_validate"
    CLASSIFY_FAILURE = "classify_failure"
    GENERATE_TESTS = "generate_tests"
    GENERATE_PATCH = "generate_patch"
    PATCH_POLICY_CHECK = "patch_policy_check"
    VALIDATE_PATCH = "validate_patch"
    STORE_ARTIFACTS = "store_artifacts"
    ESCALATE = "escalate"


class RepairGraphResult(BaseModel):
    """Serializable final output from graph execution."""

    model_config = ConfigDict(extra="forbid")

    status: GraphExecutionStatus
    run_status: RunStatus
    stop_reason: str | None = None
    repair_result: RepairResult | None = None
    escalation_reason: str | None = None


class RepairGraphState(BaseModel):
    """Checkpoint-ready mutable state for graph execution."""

    model_config = ConfigDict(extra="forbid")

    repo_path: Path
    goal: str
    model_profile_name: str | None = None
    selected_python: Path | None = None
    generate_tests: bool = False
    max_iters: int = Field(ge=1, default=1)
    max_candidates: int | None = Field(default=None, ge=1)
    max_diff_lines: int | None = Field(default=None, ge=1)
    timeout_per_command: int | None = Field(default=None, ge=1)
    max_total_runtime_seconds: int | None = Field(default=None, ge=1)
    keep_worktree: bool = False

    execution_status: GraphExecutionStatus = GraphExecutionStatus.PENDING
    current_node: RepairGraphNode = RepairGraphNode.INTAKE
    completed_nodes: list[RepairGraphNode] = Field(default_factory=list)
    node_history: list[RepairGraphNode] = Field(default_factory=list)
    iteration: int = 0
    candidate_index: int = 0

    run_id: str | None = None
    resolved_model_profile: str | None = None
    policy_path: Path | None = None
    patch_path: Path | None = None
    generated_test_patch_path: Path | None = None
    worktree_path: Path | None = None
    stop_reason: str | None = None
    escalation_reason: str | None = None

    baseline_report: ValidationReport | None = None
    generated_test_validation_report: ValidationReport | None = None
    validation_report: ValidationReport | None = None
    failure_class: FailureClass | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    generated_test_rejection_reasons: list[str] = Field(default_factory=list)

    result: RepairGraphResult | None = None

    def serialize(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def deserialize(cls, payload: str | bytes | bytearray) -> "RepairGraphState":
        return cls.model_validate_json(payload)

    def checkpoint_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
