"""LLM client interfaces."""

from .client import (
    LLMClient,
    ModelProfile,
    ModelProfilesConfig,
    OpenAICompatibleClient,
    load_model_profiles,
    resolve_model_profile,
)
from .fake import FakeLLMClient
from .router import TASK_KINDS, ModelRouter, RouteSelection

__all__ = [
    "FakeLLMClient",
    "LLMClient",
    "ModelRouter",
    "ModelProfile",
    "ModelProfilesConfig",
    "OpenAICompatibleClient",
    "RouteSelection",
    "TASK_KINDS",
    "load_model_profiles",
    "resolve_model_profile",
]
