from __future__ import annotations

import sys
from pathlib import Path

import pytest

from local_swe_controller.exceptions import CommandSafetyError
from local_swe_controller.models import CommandSpec
from local_swe_controller.sandbox.commands import CommandRunner


def test_successful_command(tmp_path: Path) -> None:
    runner = CommandRunner(forbidden_commands=[], artifact_dir=tmp_path)

    result = runner.run(
        CommandSpec(
            command=[sys.executable, "-c", "print('ok')"],
            timeout_seconds=5,
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.duration_seconds >= 0


def test_non_zero_exit(tmp_path: Path) -> None:
    runner = CommandRunner(forbidden_commands=[], artifact_dir=tmp_path)

    result = runner.run(
        CommandSpec(
            command=[sys.executable, "-c", "import sys; sys.exit(7)"],
            timeout_seconds=5,
        )
    )

    assert result.exit_code == 7
    assert result.timed_out is False


def test_timeout(tmp_path: Path) -> None:
    runner = CommandRunner(forbidden_commands=[], artifact_dir=tmp_path)

    result = runner.run(
        CommandSpec(
            command=[sys.executable, "-c", "import time; time.sleep(2)"],
            timeout_seconds=1,
        )
    )

    assert result.exit_code == -1
    assert result.timed_out is True


def test_forbidden_command(tmp_path: Path) -> None:
    runner = CommandRunner(forbidden_commands=["rm -rf"], artifact_dir=tmp_path)

    with pytest.raises(CommandSafetyError, match="forbidden by policy"):
        runner.run(CommandSpec(command=["rm", "-rf", "/tmp/example"]))


def test_stdout_stderr_capture_and_artifacts(tmp_path: Path) -> None:
    runner = CommandRunner(forbidden_commands=[], artifact_dir=tmp_path)

    result = runner.run(
        CommandSpec(
            command=[
                sys.executable,
                "-c",
                "import sys; print('hello'); print('oops', file=sys.stderr)",
            ],
            timeout_seconds=5,
        )
    )

    assert result.stdout.strip() == "hello"
    assert result.stderr.strip() == "oops"
    assert result.stdout_artifact is not None
    assert result.stderr_artifact is not None
    assert result.stdout_artifact.read_text(encoding="utf-8").strip() == "hello"
    assert result.stderr_artifact.read_text(encoding="utf-8").strip() == "oops"


def test_cwd_behavior(tmp_path: Path) -> None:
    runner = CommandRunner(forbidden_commands=[], artifact_dir=tmp_path / "artifacts")
    working_dir = tmp_path / "working"
    working_dir.mkdir()

    result = runner.run(
        CommandSpec(
            command=[sys.executable, "-c", "from pathlib import Path; print(Path.cwd())"],
            timeout_seconds=5,
        ),
        cwd=working_dir,
    )

    assert Path(result.stdout.strip()) == working_dir.resolve()


def test_shell_like_string_is_blocked(tmp_path: Path) -> None:
    runner = CommandRunner(forbidden_commands=[], artifact_dir=tmp_path)

    with pytest.raises(CommandSafetyError, match="explicit argument lists"):
        runner.run(CommandSpec(command=["rm -rf /tmp/example"]))


def test_cwd_cannot_escape_allowed_root(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    runner = CommandRunner(
        forbidden_commands=[],
        artifact_dir=tmp_path / "artifacts",
        allowed_cwd_root=sandbox_root,
    )

    with pytest.raises(CommandSafetyError, match="cwd escapes the sandbox root"):
        runner.run(
            CommandSpec(
                command=[sys.executable, "-c", "print('blocked')"],
                cwd=str(outside_root),
            )
        )
