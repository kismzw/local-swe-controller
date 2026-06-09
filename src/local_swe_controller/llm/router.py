"""Task-to-model routing for bounded local LLM usage."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from local_swe_controller.exceptions import ConfigError
from local_swe_controller.llm.client import ModelProfile, ModelProfilesConfig

TASK_KINDS = {
    "policy_extraction",
    "failure_classification",
    "failure_summary",
    "fault_localization",
    "patch_generation",
    "patch_review",
    "difficult_root_cause",
    "test_generation",
}


@dataclass(frozen=True, slots=True)
class RouteSelection:
    """Resolved route for one LLM task."""

    task_kind: str
    profile_name: str
    profile: ModelProfile
    used_test_routing: bool = False
    used_fallback: bool = False
    estimated_input_tokens: int | None = None


class ModelRouter:
    """Resolve task kinds to configured model profiles."""

    def __init__(self, config: ModelProfilesConfig) -> None:
        self.config = config

    def resolve(
        self,
        task_kind: str,
        *,
        profile_name: str | None = None,
        use_test_routing: bool = False,
        prompt_text: str | None = None,
        system_prompt: str | None = None,
    ) -> RouteSelection:
        self._validate_task_kind(task_kind)

        if profile_name is not None:
            resolved_name, profile, used_fallback = self._resolve_profile_name(
                requested_name=profile_name,
                task_kind=task_kind,
                explicit=True,
            )
        else:
            route_map_name = "test_routing" if use_test_routing else "routing"
            route_map = self.config.test_routing if use_test_routing else self.config.routing
            requested_name = route_map.get(task_kind)
            if requested_name is None:
                raise ConfigError(
                    f"No model route configured for task '{task_kind}' in {route_map_name}."
                )
            resolved_name, profile, used_fallback = self._resolve_profile_name(
                requested_name=requested_name,
                task_kind=task_kind,
                explicit=False,
            )

        estimated_input_tokens = None
        if prompt_text is not None or system_prompt is not None:
            estimated_input_tokens = self._estimate_tokens(
                "\n".join(
                    part for part in (system_prompt or "", prompt_text or "") if part
                )
            )
            self._ensure_context_window(
                profile_name=resolved_name,
                profile=profile,
                estimated_input_tokens=estimated_input_tokens,
            )

        self._ensure_safe_profile(
            profile_name=resolved_name,
            profile=profile,
            use_test_routing=use_test_routing,
        )

        return RouteSelection(
            task_kind=task_kind,
            profile_name=resolved_name,
            profile=profile,
            used_test_routing=use_test_routing and profile_name is None,
            used_fallback=used_fallback,
            estimated_input_tokens=estimated_input_tokens,
        )

    def _resolve_profile_name(
        self,
        *,
        requested_name: str,
        task_kind: str,
        explicit: bool,
    ) -> tuple[str, ModelProfile, bool]:
        if requested_name in self.config.models:
            return requested_name, self.config.models[requested_name], False

        fallbacks = self.config.fallback_routing.get(task_kind, [])
        for fallback_name in fallbacks:
            if fallback_name in self.config.models:
                return fallback_name, self.config.models[fallback_name], True

        available = ", ".join(sorted(self.config.models))
        if explicit:
            raise ConfigError(
                f"Unknown model profile '{requested_name}'. Available profiles: {available}"
            )
        raise ConfigError(
            f"Route for task '{task_kind}' references missing model '{requested_name}'. "
            f"Available profiles: {available}"
        )

    def _validate_task_kind(self, task_kind: str) -> None:
        if task_kind not in TASK_KINDS:
            available = ", ".join(sorted(TASK_KINDS))
            raise ConfigError(
                f"Unsupported task kind '{task_kind}'. Supported task kinds: {available}"
            )

    def _ensure_context_window(
        self,
        *,
        profile_name: str,
        profile: ModelProfile,
        estimated_input_tokens: int,
    ) -> None:
        if profile.context_window is None:
            return
        requested_tokens = estimated_input_tokens + profile.max_output_tokens
        if requested_tokens > profile.context_window:
            raise ConfigError(
                f"Prompt for model profile '{profile_name}' exceeds context window "
                f"({requested_tokens} > {profile.context_window} tokens including output budget)."
            )

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)

    def _ensure_safe_profile(
        self,
        *,
        profile_name: str,
        profile: ModelProfile,
        use_test_routing: bool,
    ) -> None:
        if use_test_routing and profile.provider != "fake":
            raise ConfigError(
                f"Test routing for profile '{profile_name}' must use provider 'fake'."
            )
        if profile.provider != "openai_compatible":
            return

        parsed = urlparse(profile.base_url)
        host = (parsed.hostname or "").casefold()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return
        raise ConfigError(
            f"Model profile '{profile_name}' uses a non-local endpoint: {profile.base_url}"
        )
