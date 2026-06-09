"""Schemas for local benchmark cases and results."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from local_swe_controller.models import CommandResult, CommandSpec, FailureClass, RunStatus


class BenchmarkMode(str, Enum):
    VALIDATE = "validate"
    REPAIR = "repair"


class BenchmarkCase(BaseModel):
    """One benchmark case loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    repo: Path
    mode: BenchmarkMode
    goal: str | None = None
    model_profile: str | None = None
    generate_tests: bool = False
    orchestrator: str = "deterministic"
    max_iters: int = Field(default=1, ge=1)
    max_candidates: int | None = Field(default=None, ge=1)
    expected_status: RunStatus
    acceptance_commands: list[CommandSpec] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_case(self) -> "BenchmarkCase":
        if self.mode == BenchmarkMode.REPAIR and not self.goal:
            raise ValueError("repair benchmark cases require a goal")
        if self.mode == BenchmarkMode.VALIDATE and self.goal is not None:
            raise ValueError("validate benchmark cases must not define a goal")
        return self


class BenchmarkCaseResult(BaseModel):
    """Recorded result for one benchmark case."""

    model_config = ConfigDict(extra="forbid")

    name: str
    case_path: Path
    repo: Path | None = None
    mode: BenchmarkMode | None = None
    expected_status: RunStatus | None = None
    actual_status: RunStatus | None = None
    passed: bool
    expected_status_matched: bool = False
    acceptance_passed: bool = False
    controller_run_id: str | None = None
    controller_artifact_dir: Path | None = None
    failure_class: FailureClass | None = None
    duration_seconds: float = Field(ge=0)
    target_repo_changed: bool = False
    repo_status_changed: bool = False
    acceptance_results: list[CommandResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: str | None = None


class BenchmarkRunResult(BaseModel):
    """Top-level benchmark run artifact."""

    model_config = ConfigDict(extra="forbid")

    bench_run_id: str
    created_at: datetime
    finished_at: datetime
    artifact_dir: Path
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    failure_class_counts: dict[str, int] = Field(default_factory=dict)
    duration_seconds: float = Field(ge=0)
    cases: list[BenchmarkCaseResult] = Field(default_factory=list)
