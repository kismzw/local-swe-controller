"""Deterministic validation failure classification."""

from __future__ import annotations

from local_swe_controller.config import FailureClassificationConfig
from local_swe_controller.models import CommandResult, FailureClass


class FailureClassifier:
    """Classify command failures using timeout, policy, stage, and output patterns."""

    def __init__(self, config: FailureClassificationConfig | None) -> None:
        self.config = config or FailureClassificationConfig()

    def classify(self, result: CommandResult) -> FailureClass:
        if result.timed_out:
            return FailureClass.RESOURCE_EXHAUSTION

        output = f"{result.stdout}\n{result.stderr}".casefold()
        if self._matches(output, self.config.resource_exhaustion_patterns):
            return FailureClass.RESOURCE_EXHAUSTION
        if self._matches(output, self.config.security_patterns):
            return FailureClass.SECURITY
        if self._matches(output, self.config.environment_patterns):
            return FailureClass.ENVIRONMENT
        if self._matches(output, self.config.build_patterns):
            return FailureClass.BUILD

        stage_class = self._stage_failure_class(result.category)
        if stage_class is not None:
            return stage_class

        if self._matches(output, self.config.lint_patterns):
            return FailureClass.LINT
        if self._matches(output, self.config.typecheck_patterns):
            return FailureClass.TYPECHECK
        if self._matches(output, self.config.test_patterns):
            return FailureClass.TEST

        return FailureClass.UNKNOWN

    def _matches(self, output: str, patterns: list[str]) -> bool:
        return any(pattern.casefold() in output for pattern in patterns)

    def _stage_failure_class(self, category: str | None) -> FailureClass | None:
        mapping = {
            "format": FailureClass.FORMAT,
            "lint": FailureClass.LINT,
            "typecheck": FailureClass.TYPECHECK,
            "test": FailureClass.TEST,
            "security": FailureClass.SECURITY,
        }
        return mapping.get(category)
