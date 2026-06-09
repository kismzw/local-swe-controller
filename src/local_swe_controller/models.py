"""Shared enums and schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunStatus(str, Enum):
    NO_ACTION_NEEDED = "NO_ACTION_NEEDED"
    BASELINE_FAILED = "BASELINE_FAILED"
    PATCH_PROPOSED = "PATCH_PROPOSED"
    PATCH_VALIDATED = "PATCH_VALIDATED"
    PATCH_REJECTED = "PATCH_REJECTED"
    SUCCESS = "SUCCESS"
    STOPPED_BY_BUDGET = "STOPPED_BY_BUDGET"
    STOPPED_BY_REPEATED_FAILURE = "STOPPED_BY_REPEATED_FAILURE"
    STOPPED_BY_POLICY = "STOPPED_BY_POLICY"
    STOPPED_BY_SECURITY = "STOPPED_BY_SECURITY"
    STOPPED_BY_ENVIRONMENT = "STOPPED_BY_ENVIRONMENT"
    ERROR = "ERROR"


class FailureClass(str, Enum):
    ENVIRONMENT = "ENVIRONMENT"
    BUILD = "BUILD"
    FORMAT = "FORMAT"
    LINT = "LINT"
    TYPECHECK = "TYPECHECK"
    TEST = "TEST"
    SECURITY = "SECURITY"
    RESOURCE_EXHAUSTION = "RESOURCE_EXHAUSTION"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    UNKNOWN = "UNKNOWN"


class MissingToolBehavior(str, Enum):
    FAIL = "FAIL"
    SKIP = "SKIP"


class CommandSpec(BaseModel):
    """Explicit command specification using argv form."""

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1)
    cwd: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    description: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    optional: bool = False
    tool_name: str | None = None
    missing_tool_behavior: MissingToolBehavior = MissingToolBehavior.FAIL

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if any(not part or not part.strip() for part in value):
            raise ValueError("command entries must be non-empty strings")
        return value

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not key or not key.strip():
                raise ValueError("environment variable names must be non-empty strings")
        return value


class CommandResult(BaseModel):
    """Execution result for a command."""

    model_config = ConfigDict(extra="forbid")

    spec: CommandSpec
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(ge=0)
    started_at: datetime
    finished_at: datetime
    timed_out: bool = False
    stdout_artifact: Path | None = None
    stderr_artifact: Path | None = None
    category: str | None = None


class ValidationReport(BaseModel):
    """Result summary for a validation run."""

    model_config = ConfigDict(extra="forbid")

    repo_root: Path
    status: RunStatus
    failure_class: FailureClass | None = None
    commands: list[CommandResult] = Field(default_factory=list)
    summary: str | None = None
    worktree_path: Path | None = None
    artifact_dir: Path | None = None
    warnings: list[str] = Field(default_factory=list)
    target_repo_dirty: bool = False
    target_repo_changed: bool = False
