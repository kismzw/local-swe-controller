from __future__ import annotations

from pathlib import Path

import pytest

from local_swe_controller.exceptions import ConfigError
from local_swe_controller.llm import ModelRouter, load_model_profiles


def test_valid_model_route(project_root: Path) -> None:
    config = load_model_profiles(project_root / "configs" / "model_profiles.example.yaml")
    router = ModelRouter(config)

    selection = router.resolve("patch_generation")

    assert selection.profile_name == "main_patch_model"


def test_missing_route(tmp_path: Path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  fake:
    provider: fake
    model: fake
    context_window: 1024
""".strip(),
        encoding="utf-8",
    )
    router = ModelRouter(load_model_profiles(config_path))

    with pytest.raises(ConfigError, match="No model route configured for task 'patch_generation'"):
        router.resolve("patch_generation")


def test_missing_model(tmp_path: Path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
routing:
  patch_generation: missing
models:
  fake:
    provider: fake
    model: fake
    context_window: 1024
""".strip(),
        encoding="utf-8",
    )
    router = ModelRouter(load_model_profiles(config_path))

    with pytest.raises(ConfigError, match="references missing model 'missing'"):
        router.resolve("patch_generation")


def test_context_window_violation(tmp_path: Path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
routing:
  patch_generation: tiny
models:
  tiny:
    provider: fake
    model: fake
    context_window: 20
    max_output_tokens: 16
""".strip(),
        encoding="utf-8",
    )
    router = ModelRouter(load_model_profiles(config_path))

    with pytest.raises(ConfigError, match="exceeds context window"):
        router.resolve("patch_generation", prompt_text="x" * 40, system_prompt="system")


def test_fake_route_usage(project_root: Path) -> None:
    config = load_model_profiles(project_root / "configs" / "model_profiles.example.yaml")
    router = ModelRouter(config)

    selection = router.resolve("patch_generation", use_test_routing=True)

    assert selection.profile_name == "fake"
    assert selection.used_test_routing is True


def test_missing_test_route_does_not_fall_back_to_primary(tmp_path: Path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
routing:
  patch_generation: local_model
models:
  local_model:
    provider: openai_compatible
    base_url: http://localhost:8000/v1
    model: local
    context_window: 1024
""".strip(),
        encoding="utf-8",
    )
    router = ModelRouter(load_model_profiles(config_path))

    with pytest.raises(
        ConfigError,
        match="No model route configured for task 'patch_generation' in test_routing",
    ):
        router.resolve("patch_generation", use_test_routing=True)


def test_non_local_endpoint_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
routing:
  patch_generation: remote_model
models:
  remote_model:
    provider: openai_compatible
    base_url: https://api.openai.com/v1
    model: remote
    context_window: 1024
""".strip(),
        encoding="utf-8",
    )
    router = ModelRouter(load_model_profiles(config_path))

    with pytest.raises(ConfigError, match="non-local endpoint"):
        router.resolve("patch_generation")
