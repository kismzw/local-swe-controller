from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from local_swe_controller.config import DefaultPolicyConfig, load_config
from local_swe_controller.exceptions import ConfigError
from local_swe_controller.models import CommandSpec, FailureClass, RunStatus


def test_load_valid_default_policy(project_root: Path) -> None:
    config = load_config(project_root / "configs" / "default_policy.yaml", DefaultPolicyConfig)

    assert config.policy_version == "0.1"
    assert config.defaults.max_iters == 1
    assert "rm -rf" in config.forbidden_commands


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "missing.yaml", DefaultPolicyConfig)


def test_malformed_config_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("defaults: [unterminated\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_config(config_path, DefaultPolicyConfig)


def test_invalid_config_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("policy_version: 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid config"):
        load_config(config_path, DefaultPolicyConfig)


def test_enums_validate() -> None:
    assert RunStatus.SUCCESS == "SUCCESS"
    assert FailureClass.TEST == "TEST"


def test_command_spec_validation() -> None:
    spec = CommandSpec(command=["uv", "run", "pytest"], timeout_seconds=30)

    assert spec.command == ["uv", "run", "pytest"]

    with pytest.raises(ValidationError):
        CommandSpec(command=["", "pytest"])
