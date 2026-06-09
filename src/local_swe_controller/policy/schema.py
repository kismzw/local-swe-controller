"""Compiled policy schema."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from local_swe_controller.config import PatchPolicyConfig
from local_swe_controller.models import CommandSpec


class CompiledPolicy(BaseModel):
    """Machine-readable repository policy."""

    model_config = ConfigDict(extra="forbid")

    repo_root: Path
    policy_version: str
    source_files: list[Path] = Field(default_factory=list)
    setup_commands: list[CommandSpec] = Field(default_factory=list)
    format_commands: list[CommandSpec] = Field(default_factory=list)
    lint_commands: list[CommandSpec] = Field(default_factory=list)
    typecheck_commands: list[CommandSpec] = Field(default_factory=list)
    test_commands: list[CommandSpec] = Field(default_factory=list)
    security_commands: list[CommandSpec] = Field(default_factory=list)
    hard_gates: list[str] = Field(default_factory=list)
    soft_gates: list[str] = Field(default_factory=list)
    forbidden_commands: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    approval_required_operations: list[str] = Field(default_factory=list)
    patch_policy: PatchPolicyConfig
    generated_at: datetime
