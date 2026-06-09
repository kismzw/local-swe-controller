"""Safe explicit command execution helpers."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from local_swe_controller.exceptions import CommandSafetyError
from local_swe_controller.models import CommandResult, CommandSpec

_SHELL_META_TOKENS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "$(", "`"}
_SHELL_LAUNCHERS = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}
_SHELL_FLAGS = {"-c", "/c"}


class CommandSafetyChecker:
    """Policy-aware checks for explicit argv-based commands."""

    def __init__(self, forbidden_commands: list[str]) -> None:
        self.forbidden_commands = [item.strip() for item in forbidden_commands if item.strip()]

    def validate(self, spec: CommandSpec) -> None:
        self._ensure_explicit_argv(spec.command)
        self._ensure_no_shell_launcher(spec.command)
        self._ensure_no_shell_meta_tokens(spec.command)
        self._ensure_not_forbidden(spec.command)

    def _ensure_explicit_argv(self, command: list[str]) -> None:
        if len(command) == 1 and any(ch.isspace() for ch in command[0].strip()):
            raise CommandSafetyError(
                "Commands must be provided as explicit argument lists, not shell strings."
            )

    def _ensure_no_shell_launcher(self, command: list[str]) -> None:
        executable = Path(command[0]).name.lower()
        if executable in _SHELL_LAUNCHERS and any(flag in command[1:] for flag in _SHELL_FLAGS):
            raise CommandSafetyError(
                "Shell launcher commands are not allowed; commands must be explicit argv lists."
            )

    def _ensure_no_shell_meta_tokens(self, command: list[str]) -> None:
        for part in command:
            stripped = part.strip()
            if stripped in _SHELL_META_TOKENS:
                raise CommandSafetyError(
                    f"Shell meta token is not allowed in explicit commands: {stripped}"
                )

    def _ensure_not_forbidden(self, command: list[str]) -> None:
        normalized = " ".join(command).casefold()
        for forbidden in self.forbidden_commands:
            if forbidden.casefold() in normalized:
                raise CommandSafetyError(f"Command is forbidden by policy: {forbidden}")


class CommandRunner:
    """Run validated commands with captured artifacts."""

    def __init__(
        self,
        forbidden_commands: list[str],
        artifact_dir: Path | None = None,
        default_timeout_seconds: int | None = None,
        allowed_cwd_root: Path | None = None,
    ) -> None:
        self.safety_checker = CommandSafetyChecker(forbidden_commands)
        self.default_timeout_seconds = default_timeout_seconds
        self.allowed_cwd_root = allowed_cwd_root.resolve() if allowed_cwd_root else None
        self.artifact_dir = artifact_dir or Path(
            tempfile.mkdtemp(prefix="local-swe-command-artifacts-")
        )
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        spec: CommandSpec,
        *,
        cwd: Path | None = None,
        category: str | None = None,
    ) -> CommandResult:
        self.safety_checker.validate(spec)

        resolved_cwd = self._resolve_cwd(spec, cwd)
        env = os.environ.copy()
        env.update(spec.env)
        env["PATH"] = self._build_path(env)
        timeout_seconds = spec.timeout_seconds or self.default_timeout_seconds

        started_at = datetime.now(UTC)
        started = monotonic()
        stdout_text = ""
        stderr_text = ""
        exit_code = 1
        timed_out = False

        try:
            completed = subprocess.run(
                spec.command,
                cwd=resolved_cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
                check=False,
            )
            stdout_text = completed.stdout
            stderr_text = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout_text = self._coerce_output(exc.stdout)
            stderr_text = self._coerce_output(exc.stderr)
            exit_code = -1

        finished_at = datetime.now(UTC)
        duration_seconds = monotonic() - started
        stdout_artifact, stderr_artifact = self._write_artifacts(
            command=spec.command,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
        )

        return CommandResult(
            spec=spec,
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_seconds=duration_seconds,
            started_at=started_at,
            finished_at=finished_at,
            timed_out=timed_out,
            stdout_artifact=stdout_artifact,
            stderr_artifact=stderr_artifact,
            category=category,
        )

    def _resolve_cwd(self, spec: CommandSpec, cwd: Path | None) -> Path:
        if spec.cwd is None:
            resolved = cwd.resolve() if cwd is not None else Path.cwd()
            self._ensure_cwd_allowed(resolved)
            return resolved

        spec_cwd = Path(spec.cwd)
        if spec_cwd.is_absolute():
            resolved = spec_cwd.resolve()
            self._ensure_cwd_allowed(resolved)
            return resolved
        if cwd is None:
            resolved = spec_cwd.resolve()
            self._ensure_cwd_allowed(resolved)
            return resolved
        resolved = (cwd / spec_cwd).resolve()
        self._ensure_cwd_allowed(resolved)
        return resolved

    def _write_artifacts(
        self,
        *,
        command: list[str],
        stdout_text: str,
        stderr_text: str,
    ) -> tuple[Path, Path]:
        slug = self._command_slug(command)
        index = len(list(self.artifact_dir.glob(f"{slug}-*.stdout.txt"))) + 1
        stdout_path = self.artifact_dir / f"{slug}-{index}.stdout.txt"
        stderr_path = self.artifact_dir / f"{slug}-{index}.stderr.txt"
        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")
        return stdout_path, stderr_path

    def _command_slug(self, command: list[str]) -> str:
        safe_parts = []
        for part in command[:3]:
            text = "".join(ch if ch.isalnum() else "-" for ch in part.strip().lower())
            safe_parts.append(text.strip("-") or "arg")
        return "-".join(safe_parts) or "command"

    def _coerce_output(self, value: bytes | str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def _build_path(self, env: dict[str, str]) -> str:
        controller_bin = Path(__file__).resolve().parents[3] / ".venv" / "bin"
        existing = env.get("PATH", "")
        if controller_bin.is_dir():
            return f"{controller_bin}:{existing}" if existing else str(controller_bin)
        return existing

    def _ensure_cwd_allowed(self, resolved_cwd: Path) -> None:
        if self.allowed_cwd_root is None:
            return
        try:
            resolved_cwd.relative_to(self.allowed_cwd_root)
        except ValueError as exc:
            raise CommandSafetyError(
                f"Command cwd escapes the sandbox root: {resolved_cwd}"
            ) from exc
