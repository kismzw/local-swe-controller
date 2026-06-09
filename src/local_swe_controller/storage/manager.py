"""Local artifact storage and run index management."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from local_swe_controller.config import DefaultPolicyConfig, OpenTelemetryConfig, load_config
from local_swe_controller.models import FailureClass, RunStatus, ValidationReport
from local_swe_controller.storage.events import validate_trace_event
from local_swe_controller.storage.telemetry import TelemetryClient

_SQLITE_TIMEOUT_SECONDS = 30.0
_SQLITE_BUSY_TIMEOUT_MS = 30_000


def default_artifact_root() -> Path:
    """Return the default artifact root outside target repositories."""

    env_root = os.environ.get("LOCAL_SWE_ARTIFACT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    candidates = [
        (Path.home() / ".local-swe").expanduser(),
        Path(tempfile.gettempdir()) / "local-swe",
    ]
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            if not os.access(candidate.parent, os.W_OK):
                continue
            return candidate.resolve()
        except OSError:
            continue
    return (Path(tempfile.gettempdir()) / "local-swe").resolve()


class RunRecord(BaseModel):
    """Indexed metadata for a stored controller run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_type: str
    repo_root: Path
    goal: str | None = None
    status: RunStatus
    summary: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    artifact_dir: Path
    trace_path: Path
    patch_path: Path | None = None
    policy_path: Path | None = None
    report_path: Path | None = None
    summary_path: Path | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class RunContext:
    """Filesystem paths for an active run."""

    run_id: str
    run_type: str
    repo_root: Path
    goal: str | None
    created_at: datetime
    artifact_dir: Path
    log_dir: Path
    trace_path: Path
    patch_path: Path
    generated_test_patch_path: Path
    policy_path: Path
    report_path: Path
    summary_path: Path


class ArtifactStore:
    """Manage `.local-swe` artifacts and the SQLite run index."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_artifact_root()).expanduser().resolve()
        self.runs_dir = self.root / "runs"
        self.patches_dir = self.root / "patches"
        self.logs_dir = self.root / "logs"
        self.policies_dir = self.root / "policies"
        self.index_path = self.runs_dir / "index.sqlite3"
        self.telemetry = TelemetryClient(self._load_otel_config())
        self._layout_lock = threading.Lock()
        self._ensure_layout()

    def create_run(
        self,
        *,
        run_type: str,
        repo_root: Path,
        goal: str | None = None,
    ) -> RunContext:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        created_at = datetime.now(UTC)
        artifact_dir = self.runs_dir / run_id
        log_dir = self.logs_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        log_dir.mkdir(parents=True, exist_ok=False)
        context = RunContext(
            run_id=run_id,
            run_type=run_type,
            repo_root=repo_root.resolve(),
            goal=goal,
            created_at=created_at,
            artifact_dir=artifact_dir,
            log_dir=log_dir,
            trace_path=artifact_dir / "trace.jsonl",
            patch_path=self.patches_dir / f"{run_id}.patch",
            generated_test_patch_path=self.patches_dir / f"{run_id}.generated-tests.patch",
            policy_path=self.policies_dir / f"{run_id}.compiled.json",
            report_path=artifact_dir / "run.json",
            summary_path=artifact_dir / "summary.md",
        )
        self._upsert_record(
            RunRecord(
                run_id=context.run_id,
                run_type=context.run_type,
                repo_root=context.repo_root,
                goal=context.goal,
                status=RunStatus.ERROR,
                created_at=context.created_at,
                artifact_dir=context.artifact_dir,
                trace_path=context.trace_path,
                patch_path=context.patch_path,
                policy_path=context.policy_path,
                report_path=context.report_path,
                summary_path=context.summary_path,
            )
        )
        return context

    def append_trace(self, context: RunContext, event: str, payload: dict[str, Any]) -> None:
        timestamp = datetime.now(UTC)
        envelope = validate_trace_event(event=event, payload=payload, timestamp=timestamp)
        record = {
            "timestamp": envelope.timestamp.isoformat(),
            "event": envelope.event.value,
            "payload": envelope.payload,
        }
        with context.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=self._json_default) + "\n")
        self.telemetry.record_event(event, payload)

    def write_policy_snapshot(self, context: RunContext, policy_json: str) -> Path:
        context.policy_path.write_text(policy_json, encoding="utf-8")
        return context.policy_path

    def write_patch(self, context: RunContext, patch_text: str) -> Path:
        context.patch_path.write_text(patch_text, encoding="utf-8")
        return context.patch_path

    def write_generated_test_patch(self, context: RunContext, patch_text: str) -> Path:
        context.generated_test_patch_path.write_text(patch_text, encoding="utf-8")
        return context.generated_test_patch_path

    def write_report(self, context: RunContext, payload: dict[str, Any]) -> Path:
        context.report_path.write_text(
            json.dumps(payload, indent=2, default=self._json_default),
            encoding="utf-8",
        )
        return context.report_path

    def write_summary(self, context: RunContext, content: str) -> Path:
        context.summary_path.write_text(content, encoding="utf-8")
        return context.summary_path

    def finalize_run(
        self,
        context: RunContext,
        *,
        status: RunStatus,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        record = RunRecord(
            run_id=context.run_id,
            run_type=context.run_type,
            repo_root=context.repo_root,
            goal=context.goal,
            status=status,
            summary=summary,
            created_at=context.created_at,
            finished_at=datetime.now(UTC),
            artifact_dir=context.artifact_dir,
            trace_path=context.trace_path,
            patch_path=context.patch_path if context.patch_path.exists() else None,
            policy_path=context.policy_path if context.policy_path.exists() else None,
            report_path=context.report_path if context.report_path.exists() else None,
            summary_path=context.summary_path if context.summary_path.exists() else None,
            metadata=metadata or {},
        )
        self._upsert_record(record)
        return record

    def list_runs(self) -> list[RunRecord]:
        if not self.index_path.exists():
            return []
        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT run_id, run_type, repo_root, goal, status, summary, created_at,
                       finished_at, artifact_dir, trace_path, patch_path, policy_path,
                       report_path, summary_path, metadata
                FROM runs
                WHERE finished_at IS NOT NULL
                ORDER BY created_at DESC
                """
            )
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_run(self, run_id: str) -> RunRecord | None:
        if not self.index_path.exists():
            return None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT run_id, run_type, repo_root, goal, status, summary, created_at,
                       finished_at, artifact_dir, trace_path, patch_path, policy_path,
                       report_path, summary_path, metadata
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def compiled_policy_output_path(self, repo_root: Path) -> Path:
        slug = self._slug(repo_root.resolve())
        return self.policies_dir / f"{slug}.compiled.json"

    def _ensure_layout(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.policies_dir.mkdir(parents=True, exist_ok=True)
        with self._layout_lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        run_type TEXT NOT NULL,
                        repo_root TEXT NOT NULL,
                        goal TEXT,
                        status TEXT NOT NULL,
                        summary TEXT,
                        created_at TEXT NOT NULL,
                        finished_at TEXT,
                        artifact_dir TEXT NOT NULL,
                        trace_path TEXT NOT NULL,
                        patch_path TEXT,
                        policy_path TEXT,
                        report_path TEXT,
                        summary_path TEXT,
                        metadata TEXT NOT NULL
                    )
                    """
                )
                existing_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
                }
                if "summary_path" not in existing_columns:
                    try:
                        connection.execute("ALTER TABLE runs ADD COLUMN summary_path TEXT")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc).casefold():
                            raise
                connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path, timeout=_SQLITE_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _upsert_record(self, record: RunRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, run_type, repo_root, goal, status, summary, created_at,
                    finished_at, artifact_dir, trace_path, patch_path, policy_path,
                    report_path, summary_path, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    run_type = excluded.run_type,
                    repo_root = excluded.repo_root,
                    goal = excluded.goal,
                    status = excluded.status,
                    summary = excluded.summary,
                    created_at = excluded.created_at,
                    finished_at = excluded.finished_at,
                    artifact_dir = excluded.artifact_dir,
                    trace_path = excluded.trace_path,
                    patch_path = excluded.patch_path,
                    policy_path = excluded.policy_path,
                    report_path = excluded.report_path,
                    summary_path = excluded.summary_path,
                    metadata = excluded.metadata
                """,
                (
                    record.run_id,
                    record.run_type,
                    str(record.repo_root),
                    record.goal,
                    record.status.value,
                    record.summary,
                    record.created_at.isoformat(),
                    record.finished_at.isoformat() if record.finished_at else None,
                    str(record.artifact_dir),
                    str(record.trace_path),
                    str(record.patch_path) if record.patch_path else None,
                    str(record.policy_path) if record.policy_path else None,
                    str(record.report_path) if record.report_path else None,
                    str(record.summary_path) if record.summary_path else None,
                    json.dumps(record.metadata, default=self._json_default),
                ),
            )
            connection.commit()

    def _row_to_record(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            run_type=row["run_type"],
            repo_root=Path(row["repo_root"]),
            goal=row["goal"],
            status=RunStatus(row["status"]),
            summary=row["summary"],
            created_at=datetime.fromisoformat(row["created_at"]),
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
            artifact_dir=Path(row["artifact_dir"]),
            trace_path=Path(row["trace_path"]),
            patch_path=Path(row["patch_path"]) if row["patch_path"] else None,
            policy_path=Path(row["policy_path"]) if row["policy_path"] else None,
            report_path=Path(row["report_path"]) if row["report_path"] else None,
            summary_path=Path(row["summary_path"]) if row["summary_path"] else None,
            metadata=json.loads(row["metadata"]),
        )

    def build_validate_summary(
        self,
        *,
        context: RunContext,
        report: ValidationReport,
        policy_path: Path,
    ) -> str:
        return self._build_summary(
            run_id=context.run_id,
            command_type="validate",
            repo_root=context.repo_root,
            goal=context.goal,
            status=report.status,
            failure_class=report.failure_class,
            policy_path=policy_path,
            reports=[report],
            patch_path=None,
            generated_test_path=None,
            target_repo_changed=report.target_repo_changed,
            created_at=context.created_at,
            finished_at=datetime.now(UTC),
        )

    def build_repair_summary(
        self,
        *,
        context: RunContext,
        result_payload: dict[str, Any],
    ) -> str:
        baseline_report = ValidationReport.model_validate(result_payload["baseline_report"])
        validation_report = (
            ValidationReport.model_validate(result_payload["validation_report"])
            if result_payload.get("validation_report") is not None
            else None
        )
        generated_test_validation_report = (
            ValidationReport.model_validate(result_payload["generated_test_validation_report"])
            if result_payload.get("generated_test_validation_report") is not None
            else None
        )
        reports = [baseline_report]
        if generated_test_validation_report is not None:
            reports.append(generated_test_validation_report)
        if validation_report is not None:
            reports.append(validation_report)
        return self._build_summary(
            run_id=context.run_id,
            command_type="repair",
            repo_root=context.repo_root,
            goal=result_payload.get("goal"),
            status=RunStatus(result_payload["status"]),
            failure_class=(
                FailureClass(result_payload["validation_report"]["failure_class"])
                if result_payload.get("validation_report")
                and result_payload["validation_report"].get("failure_class")
                else baseline_report.failure_class
            ),
            policy_path=Path(result_payload["policy_path"]),
            reports=reports,
            patch_path=(
                Path(result_payload["patch_path"])
                if result_payload.get("patch_path")
                else None
            ),
            generated_test_path=(
                Path(result_payload["generated_test_patch_path"])
                if result_payload.get("generated_test_patch_path")
                else None
            ),
            target_repo_changed=bool(result_payload.get("target_repo_changed", False)),
            created_at=context.created_at,
            finished_at=datetime.now(UTC),
            extra_reasons=list(result_payload.get("rejection_reasons", [])),
        )

    def latest_run_id(self) -> str | None:
        records = self.list_runs()
        return records[0].run_id if records else None

    def _slug(self, path: Path) -> str:
        text = str(path)
        return "".join(ch if ch.isalnum() else "-" for ch in text).strip("-").lower()

    def _json_default(self, value: Any) -> str:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")

    def _load_otel_config(self) -> OpenTelemetryConfig:
        config_path = Path(__file__).resolve().parents[2] / "configs" / "default_policy.yaml"
        try:
            config = load_config(config_path, DefaultPolicyConfig)
        except Exception:
            return OpenTelemetryConfig()
        return config.observability.open_telemetry

    def _build_summary(
        self,
        *,
        run_id: str,
        command_type: str,
        repo_root: Path,
        goal: str | None,
        status: RunStatus,
        failure_class: FailureClass | None,
        policy_path: Path,
        reports: list[ValidationReport],
        patch_path: Path | None,
        generated_test_path: Path | None,
        target_repo_changed: bool,
        created_at: datetime,
        finished_at: datetime,
        extra_reasons: list[str] | None = None,
    ) -> str:
        commands = [command for report in reports for command in report.commands]
        failed_command = next(
            (
                command
                for command in reversed(commands)
                if command.exit_code != 0 or command.timed_out
            ),
            None,
        )
        security_findings = [
            report.summary
            for report in reports
            if report.failure_class == FailureClass.SECURITY and report.summary
        ]
        scanner_warnings = [
            warning
            for report in reports
            for warning in report.warnings
            if "scanner" in warning.casefold() or "security" in warning.casefold()
        ]
        policy_hash = self._policy_hash(policy_path)
        git_commit = self._git_commit(repo_root)
        duration_seconds = max((finished_at - created_at).total_seconds(), 0.0)
        command_lines = [
            (
                f"- `{ ' '.join(command.spec.command) }`"
                f" [{command.category or 'unknown'}]"
                f" stdout={command.stdout_artifact or 'n/a'}"
                f" stderr={command.stderr_artifact or 'n/a'}"
            )
            for command in commands
        ] or ["- none"]
        next_manual_action = self._next_manual_action(status, failure_class, patch_path)
        reasons = extra_reasons or []
        reason_lines = [f"- {reason}" for reason in reasons] or ["- none"]
        security_lines = [f"- {item}" for item in security_findings] or ["- none"]
        warning_lines = [f"- {item}" for item in scanner_warnings] or ["- none"]
        failed_command_text = (
            f"`{' '.join(failed_command.spec.command)}`" if failed_command is not None else "none"
        )
        failed_stdout = failed_command.stdout_artifact if failed_command is not None else "n/a"
        failed_stderr = failed_command.stderr_artifact if failed_command is not None else "n/a"
        return "\n".join(
            [
                f"# Run Summary: {run_id}",
                "",
                f"- Run ID: `{run_id}`",
                f"- Command Type: `{command_type}`",
                f"- Repo Path: `{repo_root}`",
                f"- Git Commit: `{git_commit}`",
                f"- Goal: `{goal or 'n/a'}`",
                f"- Final Status: `{status.value}`",
                f"- Failure Class: `{failure_class.value if failure_class else 'NONE'}`",
                f"- Policy Hash: `{policy_hash}`",
                f"- Patch Path: `{patch_path or 'n/a'}`",
                f"- Generated Test Path: `{generated_test_path or 'n/a'}`",
                f"- Target Repo Changed: `{str(target_repo_changed).lower()}`",
                f"- Duration Seconds: `{duration_seconds:.3f}`",
                f"- Failed Command: {failed_command_text}",
                f"- Failed Stdout Path: `{failed_stdout}`",
                f"- Failed Stderr Path: `{failed_stderr}`",
                "",
                "## Commands Run",
                *command_lines,
                "",
                "## Security Findings",
                *security_lines,
                "",
                "## Scanner Warnings",
                *warning_lines,
                "",
                "## Rejection Reasons",
                *reason_lines,
                "",
                "## Next Manual Action",
                next_manual_action,
            ]
        )

    def _policy_hash(self, policy_path: Path) -> str:
        if not policy_path.exists():
            return "missing"
        return sha256(policy_path.read_bytes()).hexdigest()

    def _git_commit(self, repo_root: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return "unknown"
        return result.stdout.strip() or "unknown"

    def _next_manual_action(
        self,
        status: RunStatus,
        failure_class: FailureClass | None,
        patch_path: Path | None,
    ) -> str:
        if status == RunStatus.NO_ACTION_NEEDED:
            return "No action needed."
        if status == RunStatus.SUCCESS and patch_path is not None:
            return f"Review `{patch_path}` and apply it manually if desired."
        if failure_class == FailureClass.SECURITY:
            return "Review the security findings and resolve them before retrying."
        if failure_class == FailureClass.ENVIRONMENT:
            return "Fix the environment or missing tool issue and rerun."
        if failure_class == FailureClass.POLICY_VIOLATION:
            return "Review the policy violation and request approval if appropriate."
        return "Inspect the trace, command logs, and artifacts before the next run."
