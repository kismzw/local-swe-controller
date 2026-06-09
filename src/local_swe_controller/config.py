"""Config loading and validation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from local_swe_controller.exceptions import ConfigError

T = TypeVar("T", bound=BaseModel)


class DefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(ge=1)
    max_total_runtime_seconds: int = Field(ge=1)
    max_iters: int = Field(ge=1)
    max_candidates: int = Field(ge=1)
    max_diff_lines: int = Field(ge=1)
    keep_worktree: bool
    require_approval_for_risky_changes: bool
    stop_on_security_failure: bool
    stop_on_policy_violation: bool
    stop_on_repeated_failure: bool


class ArtifactPathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str
    runs: str
    logs: str
    patches: str
    policies: str


class CommandPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefer_shell_false: bool
    allow_shell: bool
    allow_network_by_default: bool
    allow_global_installs: bool
    allow_target_repo_mutation: bool
    allow_model_generated_commands: bool


class LanguageToolCommandConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1)


class LanguageCommandGroupConfig(BaseModel):
    model_config = ConfigDict(extra="allow")


class LanguageConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    files: list[str] = Field(default_factory=list)


class FailureClassificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_patterns: list[str] = Field(default_factory=list)
    build_patterns: list[str] = Field(default_factory=list)
    lint_patterns: list[str] = Field(default_factory=list)
    typecheck_patterns: list[str] = Field(default_factory=list)
    test_patterns: list[str] = Field(default_factory=list)
    security_patterns: list[str] = Field(default_factory=list)
    resource_exhaustion_patterns: list[str] = Field(default_factory=list)


class OptionalSecurityScannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    pyproject_tools: list[str] = Field(default_factory=list)
    marker_files: list[str] = Field(default_factory=list)
    package_json_scripts: list[str] = Field(default_factory=list)
    description: str | None = None


class PatchRejectByDefaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_removal: bool = True
    policy_weakening: bool = True
    ci_weakening: bool = True
    security_weakening: bool = True
    dependency_change_without_approval: bool = True
    lockfile_change_without_approval: bool = True
    license_change_without_approval: bool = True


class PatchPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_diff_lines: int = Field(ge=1)
    dependency_files: list[str] = Field(default_factory=list)
    lockfiles: list[str] = Field(default_factory=list)
    license_files: list[str] = Field(default_factory=list)
    ci_files: list[str] = Field(default_factory=list)
    test_paths: list[str] = Field(default_factory=list)
    security_sensitive_paths: list[str] = Field(default_factory=list)
    secret_patterns: list[str] = Field(default_factory=list)
    reject_by_default: PatchRejectByDefaultConfig = Field(
        default_factory=PatchRejectByDefaultConfig
    )


class OpenTelemetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    service_name: str = "local-swe-controller"


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_telemetry: OpenTelemetryConfig = Field(default_factory=OpenTelemetryConfig)


class DefaultPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy_version: str
    defaults: DefaultsConfig
    artifact_paths: ArtifactPathsConfig
    command_policy: CommandPolicyConfig
    forbidden_commands: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    approval_required_operations: list[str] = Field(default_factory=list)
    hard_gates: list[str] = Field(default_factory=list)
    soft_gates: list[str] = Field(default_factory=list)
    language_inference: dict[str, Any] = Field(default_factory=dict)
    failure_classification: FailureClassificationConfig | None = None
    optional_security_scanners: dict[str, OptionalSecurityScannerConfig] = Field(
        default_factory=dict
    )
    patch_policy: PatchPolicyConfig
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its dictionary payload."""

    if not path.exists():
        raise ConfigError(f"Config file does not exist: {path}")
    if not path.is_file():
        raise ConfigError(f"Config path is not a file: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a top-level mapping: {path}")
    return data


def load_config(path: Path, model_type: type[T]) -> T:
    """Load and validate a YAML config file as a Pydantic model."""

    data = load_yaml_file(path)
    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid config in {path}: {exc}") from exc
