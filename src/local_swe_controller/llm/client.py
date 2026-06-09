"""Model profile loading and OpenAI-compatible client."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from local_swe_controller.config import load_config
from local_swe_controller.exceptions import ConfigError, LocalSweError


class LLMError(LocalSweError):
    """Raised when model interaction fails."""


class ModelDefaults(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str = "openai_compatible"
    timeout_seconds: int = Field(default=120, ge=1)
    max_retries: int = Field(default=0, ge=0)
    retry_backoff_seconds: int = Field(default=0, ge=0)
    stream: bool = False


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    description: str | None = None
    provider: str
    base_url: str = "http://localhost:8000/v1"
    api_key_env: str | None = None
    api_key_default: str | None = None
    model: str
    context_window: int | None = Field(default=None, ge=1)
    temperature: float = 0.0
    top_p: float | None = None
    max_output_tokens: int = Field(default=2048, ge=1)
    timeout_seconds: int = Field(default=120, ge=1)
    max_retries: int = Field(default=0, ge=0)
    retry_backoff_seconds: int = Field(default=0, ge=0)
    stream: bool = False

    def api_key(self) -> str | None:
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if value:
                return value
        return self.api_key_default


class ModelProfilesConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    defaults: ModelDefaults = Field(default_factory=ModelDefaults)
    models: dict[str, ModelProfile]
    routing: dict[str, str] = Field(default_factory=dict)
    test_routing: dict[str, str] = Field(default_factory=dict)
    fallback_routing: dict[str, list[str]] = Field(default_factory=dict)


class LLMClient(ABC):
    """Abstract model client used by repair orchestration."""

    def __init__(self, profile_name: str, profile: ModelProfile) -> None:
        self.profile_name = profile_name
        self.profile = profile

    @abstractmethod
    def generate_patch(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return a unified diff patch."""


class OpenAICompatibleClient(LLMClient):
    """Minimal OpenAI-compatible chat completions client."""

    def generate_patch(self, *, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.profile.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.profile.temperature,
            "max_tokens": self.profile.max_output_tokens,
            "stream": self.profile.stream,
        }
        if self.profile.top_p is not None:
            payload["top_p"] = self.profile.top_p

        request = urllib.request.Request(
            url=self.profile.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **(
                    {"Authorization": f"Bearer {self.profile.api_key()}"}
                    if self.profile.api_key()
                    else {}
                ),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.profile.timeout_seconds
            ) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LLMError(
                "Unable to reach OpenAI-compatible model endpoint at "
                f"{self.profile.base_url}: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMError("Model endpoint returned invalid JSON.") from exc

        try:
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Model endpoint response did not contain a chat message.") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("Model endpoint returned an empty patch response.")
        return content


def load_model_profiles(path: Path) -> ModelProfilesConfig:
    """Load and normalize model profiles."""

    config = load_config(path, ModelProfilesConfig)
    normalized_models: dict[str, ModelProfile] = {}
    for name, profile in config.models.items():
        payload = {
            "provider": config.defaults.provider,
            "timeout_seconds": config.defaults.timeout_seconds,
            "max_retries": config.defaults.max_retries,
            "retry_backoff_seconds": config.defaults.retry_backoff_seconds,
            "stream": config.defaults.stream,
            **profile.model_dump(),
        }
        normalized_models[name] = ModelProfile.model_validate(payload)
    return ModelProfilesConfig(
        defaults=config.defaults,
        models=normalized_models,
        routing=config.routing,
        test_routing=config.test_routing,
        fallback_routing=config.fallback_routing,
    )


def resolve_model_profile(
    config: ModelProfilesConfig,
    profile_name: str | None,
) -> tuple[str, ModelProfile]:
    """Resolve an explicit model profile or the default patch-generation route."""

    if profile_name is None:
        route_name = config.routing.get("patch_generation")
        if route_name is None:
            raise ConfigError("No patch_generation route configured for model profiles.")
        profile_name = route_name
    try:
        return profile_name, config.models[profile_name]
    except KeyError as exc:
        available = ", ".join(sorted(config.models))
        raise ConfigError(
            f"Unknown model profile '{profile_name}'. Available profiles: {available}"
        ) from exc
