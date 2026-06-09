from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from local_swe_controller.models import (
    CommandResult,
    CommandSpec,
    FailureClass,
    RunStatus,
    ValidationReport,
)


def test_validation_report_model() -> None:
    spec = CommandSpec(command=["uv", "run", "pytest"], timeout_seconds=30)
    now = datetime.now(UTC)
    result = CommandResult(
        spec=spec,
        exit_code=0,
        stdout="ok",
        stderr="",
        duration_seconds=0.5,
        started_at=now,
        finished_at=now,
    )

    report = ValidationReport(
        repo_root=Path("/tmp/repo"),
        status=RunStatus.SUCCESS,
        failure_class=FailureClass.UNKNOWN,
        commands=[result],
        summary="completed",
    )

    assert report.commands[0].exit_code == 0
    assert report.status is RunStatus.SUCCESS
