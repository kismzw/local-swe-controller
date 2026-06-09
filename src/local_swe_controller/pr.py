"""PR summary generation and optional GitHub PR creation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from local_swe_controller.exceptions import PRWorkflowError
from local_swe_controller.models import CommandResult, FailureClass, RunStatus, ValidationReport
from local_swe_controller.repair.controller import RepairResult
from local_swe_controller.storage import ArtifactStore, RunRecord


@dataclass(frozen=True, slots=True)
class PRSummaryResult:
    """Materialized PR summary artifact."""

    run_id: str
    path: Path
    content: str


@dataclass(frozen=True, slots=True)
class PullRequestResult:
    """Result from an optional GitHub pull request creation."""

    run_id: str
    summary_path: Path
    response_path: Path
    number: int
    url: str
    title: str


@dataclass(frozen=True, slots=True)
class _RunArtifacts:
    run: RunRecord
    goal: str | None
    summary: str
    status: RunStatus
    failure_class: FailureClass | None
    reports: list[ValidationReport]
    patch_path: Path | None
    generated_test_path: Path | None
    target_repo_changed: bool
    report_path: Path
    trace_path: Path
    summary_path: Path | None


class GitHubClient:
    """Minimal GitHub REST client used only for explicit PR creation."""

    def __init__(self, *, token: str, api_base_url: str = "https://api.github.com") -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")

    def create_pull_request(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        title: str,
        body: str,
        base: str,
        head: str,
    ) -> dict[str, Any]:
        return self._request_json(
            method="POST",
            path=f"/repos/{repo_owner}/{repo_name}/pulls",
            payload={
                "title": title,
                "body": body,
                "base": base,
                "head": head,
                "maintainer_can_modify": False,
            },
        )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=f"{self.api_base_url}{path}",
            data=raw,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "local-swe-controller",
            },
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise PRWorkflowError(
                f"GitHub API request failed with status {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise PRWorkflowError(f"GitHub API request failed: {exc.reason}") from exc

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PRWorkflowError("GitHub API returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise PRWorkflowError("GitHub API returned an unexpected response shape.")
        return data


class PRService:
    """Build local PR summaries and optionally create a remote GitHub PR."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        github_client_cls: type[GitHubClient] = GitHubClient,
    ) -> None:
        self.artifact_store = artifact_store
        self.github_client_cls = github_client_cls

    def generate_pr_summary(self, run_id: str) -> PRSummaryResult:
        artifacts = self._load_artifacts(run_id)
        content = self._render_summary(artifacts)
        path = artifacts.run.artifact_dir / "pr_summary.md"
        path.write_text(content, encoding="utf-8")
        return PRSummaryResult(run_id=run_id, path=path, content=content)

    def create_pull_request(
        self,
        run_id: str,
        *,
        repo_owner: str,
        repo_name: str,
        base: str,
        head: str | None,
        token_env_var: str = "GITHUB_TOKEN",
        title: str | None = None,
    ) -> PullRequestResult:
        artifacts = self._load_artifacts(run_id)
        if not head or not head.strip():
            raise PRWorkflowError(
                "Head branch must be provided explicitly. local-swe will not push a branch for you."
            )
        token = os.environ.get(token_env_var)
        if token is None or not token.strip():
            raise PRWorkflowError(
                f"GitHub token environment variable '{token_env_var}' is required for create-pr."
            )
        summary = self.generate_pr_summary(run_id)
        client = self.github_client_cls(token=token.strip())
        resolved_title = title or self._default_title(artifacts)
        response = client.create_pull_request(
            repo_owner=repo_owner,
            repo_name=repo_name,
            title=resolved_title,
            body=summary.content,
            base=base,
            head=head.strip(),
        )
        number = response.get("number")
        url = response.get("html_url")
        if not isinstance(number, int) or not isinstance(url, str) or not url:
            raise PRWorkflowError("GitHub API response did not include PR number and URL.")
        response_path = artifacts.run.artifact_dir / "github_pr.json"
        response_path.write_text(json.dumps(response, indent=2), encoding="utf-8")
        return PullRequestResult(
            run_id=run_id,
            summary_path=summary.path,
            response_path=response_path,
            number=number,
            url=url,
            title=resolved_title,
        )

    def _load_artifacts(self, run_id: str) -> _RunArtifacts:
        run = self.artifact_store.get_run(run_id)
        if run is None:
            raise PRWorkflowError(f"Run not found: {run_id}")
        if run.report_path is None or not run.report_path.exists():
            raise PRWorkflowError(f"Run report is missing for: {run_id}")

        payload = self._read_json(run.report_path)
        if run.run_type == "repair":
            result = RepairResult.model_validate(payload)
            reports = [result.baseline_report]
            if result.generated_test_validation_report is not None:
                reports.append(result.generated_test_validation_report)
            if result.validation_report is not None:
                reports.append(result.validation_report)
            return _RunArtifacts(
                run=run,
                goal=result.goal,
                summary=result.summary,
                status=result.status,
                failure_class=(
                    result.validation_report.failure_class
                    if result.validation_report and result.validation_report.failure_class
                    else result.baseline_report.failure_class
                ),
                reports=reports,
                patch_path=result.patch_path,
                generated_test_path=result.generated_test_patch_path,
                target_repo_changed=result.target_repo_changed,
                report_path=run.report_path,
                trace_path=result.trace_path,
                summary_path=result.summary_path,
            )

        report_payload = payload.get("report")
        if not isinstance(report_payload, dict):
            raise PRWorkflowError(f"Validation report payload is missing for: {run_id}")
        report = ValidationReport.model_validate(report_payload)
        return _RunArtifacts(
            run=run,
            goal=run.goal,
            summary=report.summary or "Validation completed.",
            status=report.status,
            failure_class=report.failure_class,
            reports=[report],
            patch_path=None,
            generated_test_path=None,
            target_repo_changed=report.target_repo_changed,
            report_path=run.report_path,
            trace_path=run.trace_path,
            summary_path=(
                Path(payload["summary_path"])
                if payload.get("summary_path")
                else run.summary_path
            ),
        )

    def _render_summary(self, artifacts: _RunArtifacts) -> str:
        commands = [command for report in artifacts.reports for command in report.commands]
        command_lines = [
            (
                f"- `{self._quote_command(command)}`"
                f" status={command.exit_code}"
                f" timed_out={str(command.timed_out).lower()}"
                f" category={command.category or 'unknown'}"
            )
            for command in commands
        ] or ["- none"]
        evidence_lines = [
            f"- Report JSON: `{artifacts.report_path}`",
            f"- Trace JSONL: `{artifacts.trace_path}`",
            f"- Stored Summary: `{artifacts.summary_path or 'n/a'}`",
        ]
        for index, report in enumerate(artifacts.reports, start=1):
            evidence_lines.append(
                "- Validation Phase "
                f"{index}: status=`{report.status.value}` "
                f"failure_class=`{report.failure_class.value if report.failure_class else 'NONE'}` "
                f"artifact_dir=`{report.artifact_dir or 'n/a'}` "
                f"worktree=`{report.worktree_path or 'n/a'}`"
            )
        security_lines = self._security_findings(artifacts.reports)
        manual_apply_lines = self._manual_apply_lines(
            repo_root=artifacts.run.repo_root,
            patch_path=artifacts.patch_path,
            generated_test_path=artifacts.generated_test_path,
        )
        return "\n".join(
            [
                f"# PR Summary: {artifacts.run.run_id}",
                "",
                "## Overview",
                artifacts.summary,
                "",
                "## Final Status",
                f"`{artifacts.status.value}`",
                "",
                "## Failure Class",
                f"`{artifacts.failure_class.value if artifacts.failure_class else 'NONE'}`",
                "",
                "## Commands Run",
                *command_lines,
                "",
                "## Validation Evidence",
                *evidence_lines,
                "",
                "## Patch Path",
                f"`{artifacts.patch_path or 'n/a'}`",
                "",
                "## Generated Test Path",
                f"`{artifacts.generated_test_path or 'n/a'}`",
                "",
                "## Security Findings",
                *security_lines,
                "",
                "## Target Repo Changed",
                f"`{str(artifacts.target_repo_changed).lower()}`",
                "",
                "## Manual Apply Instructions",
                *manual_apply_lines,
            ]
        )

    def _manual_apply_lines(
        self,
        *,
        repo_root: Path,
        patch_path: Path | None,
        generated_test_path: Path | None,
    ) -> list[str]:
        lines = [
            "- local-swe never pushes, merges, or modifies the target repository automatically.",
        ]
        if generated_test_path is not None:
            lines.extend(
                [
                    f"- `git -C {repo_root} apply --check {generated_test_path}`",
                    f"- `git -C {repo_root} apply {generated_test_path}`",
                ]
            )
        if patch_path is not None:
            lines.extend(
                [
                    f"- `git -C {repo_root} apply --check {patch_path}`",
                    f"- `git -C {repo_root} apply {patch_path}`",
                    f"- `local-swe validate --repo {repo_root}`",
                ]
            )
        else:
            lines.append("- No patch artifact exists for this run, so there is nothing to apply.")
        return lines

    def _default_title(self, artifacts: _RunArtifacts) -> str:
        if artifacts.goal:
            return f"local-swe: {artifacts.goal}"
        return f"local-swe run {artifacts.run.run_id}"

    def _security_findings(self, reports: list[ValidationReport]) -> list[str]:
        findings: list[str] = []
        for report in reports:
            if report.failure_class == FailureClass.SECURITY and report.summary:
                findings.append(f"- {report.summary}")
            for warning in report.warnings:
                lowered = warning.casefold()
                if "security" in lowered or "scanner" in lowered:
                    findings.append(f"- {warning}")
        return findings or ["- none"]

    def _quote_command(self, command: CommandResult) -> str:
        return " ".join(command.spec.command)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PRWorkflowError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise PRWorkflowError(f"Expected a JSON object in {path}.")
        return payload
