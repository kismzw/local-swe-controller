"""Optional OpenTelemetry integration."""

from __future__ import annotations

import importlib
from typing import Any

from local_swe_controller.config import OpenTelemetryConfig


class TelemetryClient:
    """Best-effort telemetry sink."""

    def __init__(self, config: OpenTelemetryConfig) -> None:
        self._enabled = config.enabled
        self.warning: str | None = None
        self._tracer: Any | None = None

        if not config.enabled:
            return
        try:
            trace = importlib.import_module("opentelemetry.trace")
        except ImportError:
            self.warning = (
                "OpenTelemetry is enabled in config but the optional dependency is missing. "
                "Continuing without telemetry."
            )
            return
        self._tracer = trace.get_tracer(config.service_name)

    def record_event(self, event: str, payload: dict[str, object]) -> None:
        if not self._enabled or self._tracer is None:
            return
        with self._tracer.start_as_current_span(event) as span:
            for key, value in payload.items():
                if value is None:
                    continue
                span.set_attribute(f"local_swe.{key}", self._coerce(value))

    def _coerce(self, value: object) -> object:
        if isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, list):
            return [str(item) for item in value]
        return str(value)
