from __future__ import annotations

from datetime import UTC, datetime

from local_swe_controller.config import FailureClassificationConfig
from local_swe_controller.models import CommandResult, CommandSpec, FailureClass
from local_swe_controller.validation.classifier import FailureClassifier


def test_failure_classifier_prefers_timeout() -> None:
    classifier = FailureClassifier(
        FailureClassificationConfig(
            environment_patterns=["command not found"],
            build_patterns=["SyntaxError"],
            lint_patterns=["ruff"],
            typecheck_patterns=["mypy"],
            test_patterns=["FAILED"],
            security_patterns=["vulnerability"],
            resource_exhaustion_patterns=["killed"],
        )
    )

    result = CommandResult(
        spec=CommandSpec(command=["pytest"]),
        exit_code=-1,
        stdout="",
        stderr="FAILED",
        duration_seconds=1.0,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        timed_out=True,
        category="test",
    )

    assert classifier.classify(result) == FailureClass.RESOURCE_EXHAUSTION
