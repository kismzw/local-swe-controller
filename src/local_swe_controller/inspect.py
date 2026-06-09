"""Repository inspection and validation plan reporting."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from local_swe_controller.models import CommandSpec
from local_swe_controller.policy.compiler import PolicyCompiler
from local_swe_controller.policy.profiler import RepoProfile, RepoProfiler
from local_swe_controller.policy.schema import CompiledPolicy


class InspectReport(BaseModel):
    """Deterministic inspection payload for CLI and tests."""

    model_config = ConfigDict(extra="forbid")

    repo_root: Path
    repo_kind: str
    confidence: str | None = None
    detected_languages: list[str] = Field(default_factory=list)
    detected_scripts: dict[str, list[str]] = Field(default_factory=dict)
    workflow_signals: list[str] = Field(default_factory=list)
    workflow_sources: list[str] = Field(default_factory=list)
    ordered_script_stages: list[str] = Field(default_factory=list)
    stage_scripts: dict[str, list[str]] = Field(default_factory=dict)
    config_files: list[str] = Field(default_factory=list)
    ignored_readme_commands: list[str] = Field(default_factory=list)
    command_plan: dict[str, list[list[str]]] = Field(default_factory=dict)
    command_plan_details: dict[str, list[dict[str, object]]] = Field(default_factory=dict)
    readability_warnings: list[str] = Field(default_factory=list)
    planning_warnings: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    repair_actionable_if_validation_passes: bool = False
    weak_or_missing_readme_coverage: bool = False


class RepoInspector:
    """Build a non-mutating inspection summary from the same compiler/profiler path."""

    def __init__(self, default_policy_path: Path) -> None:
        self.policy_compiler = PolicyCompiler(default_policy_path)
        self.repo_profiler = RepoProfiler()

    def inspect(self, repo_path: Path) -> InspectReport:
        repo_root = repo_path.expanduser().resolve()
        policy = self.policy_compiler.compile(repo_root)
        profile = self.repo_profiler.profile(repo_root)
        return InspectReport(
            repo_root=repo_root,
            repo_kind=policy.repo_kind,
            confidence=profile.confidence,
            detected_languages=sorted(profile.scripts_by_language),
            detected_scripts={
                language: [str(path.relative_to(repo_root)) for path in paths]
                for language, paths in sorted(profile.scripts_by_language.items())
            },
            workflow_signals=[path.name for path in profile.workflow_files],
            workflow_sources=profile.workflow_sources,
            ordered_script_stages=profile.implicit_workflow_stages,
            stage_scripts={
                stage: [str(path.relative_to(repo_root)) for path in paths]
                for stage, paths in sorted(profile.workflow_stage_scripts.items())
            },
            config_files=[str(path.relative_to(repo_root)) for path in profile.config_files],
            ignored_readme_commands=profile.ignored_readme_commands,
            command_plan=self._command_plan(policy),
            command_plan_details=self._command_plan_details(policy),
            readability_warnings=profile.readability_warnings,
            planning_warnings=profile.planning_warnings,
            protected_paths=self._protected_paths(repo_root, policy, profile),
            repair_actionable_if_validation_passes=self._repair_actionable(policy, profile),
            weak_or_missing_readme_coverage=self._weak_or_missing_readme_coverage(profile),
        )

    def render_text(self, report: InspectReport) -> str:
        lines = [
            f"Repository: {report.repo_root}",
            f"Repo Kind: {report.repo_kind}",
        ]
        if report.confidence is not None:
            lines.append(f"Confidence: {report.confidence}")
        lines.append(
            "Detected Languages: "
            + (", ".join(report.detected_languages) if report.detected_languages else "none")
        )
        lines.append("Detected Scripts:")
        if report.detected_scripts:
            for language, paths in report.detected_scripts.items():
                lines.append(f"- {language}: {', '.join(paths)}")
        else:
            lines.append("- none")
        lines.append(
            "Workflow Signals: "
            + (", ".join(report.workflow_signals) if report.workflow_signals else "none")
        )
        lines.append(
            "Workflow Sources: "
            + (", ".join(report.workflow_sources) if report.workflow_sources else "none")
        )
        lines.append(
            "Ordered Script Stages: "
            + (
                " -> ".join(report.ordered_script_stages)
                if report.ordered_script_stages
                else "none"
            )
        )
        if report.stage_scripts:
            lines.append("Stage Scripts:")
            for stage, paths in report.stage_scripts.items():
                lines.append(f"- {stage}: {', '.join(paths)}")
        lines.append(
            "Config Files: " + (", ".join(report.config_files) if report.config_files else "none")
        )
        if report.ignored_readme_commands:
            lines.append("Ignored README Commands:")
            for command in report.ignored_readme_commands:
                lines.append(f"- {command}")
        lines.append("Command Plan:")
        for category in (
            "setup",
            "build",
            "format",
            "lint",
            "typecheck",
            "test",
            "smoke",
            "e2e",
            "security",
        ):
            commands = report.command_plan_details.get(category, [])
            if not commands:
                lines.append(f"- {category}: none")
                continue
            rendered = ", ".join(
                f"[{str(command['severity'])}] {' '.join(command['command'])}"
                for command in commands
            )
            lines.append(f"- {category}: {rendered}")
        lines.append("Readability Warnings:")
        if report.readability_warnings:
            for warning in report.readability_warnings:
                lines.append(f"- {warning}")
        else:
            lines.append("- none")
        lines.append("Planning Warnings:")
        if report.planning_warnings:
            for warning in report.planning_warnings:
                lines.append(f"- {warning}")
        else:
            lines.append("- none")
        lines.append("Protected Paths:")
        if report.protected_paths:
            for path in report.protected_paths:
                lines.append(f"- {path}")
        else:
            lines.append("- none")
        lines.append(
            "Repair Actionable If Validation Passes: "
            + ("yes" if report.repair_actionable_if_validation_passes else "no")
        )
        lines.append(
            "README Coverage: "
            + (
                "weak or missing"
                if report.weak_or_missing_readme_coverage
                else "adequate or not applicable"
            )
        )
        return "\n".join(lines)

    def _command_plan(self, policy: CompiledPolicy) -> dict[str, list[list[str]]]:
        return {
            "setup": self._commands(policy.setup_commands),
            "build": self._commands(policy.build_commands),
            "format": self._commands(policy.format_commands),
            "lint": self._commands(policy.lint_commands),
            "typecheck": self._commands(policy.typecheck_commands),
            "test": self._commands(policy.test_commands),
            "smoke": self._commands(policy.smoke_commands),
            "e2e": self._commands(policy.e2e_commands),
            "security": self._commands(policy.security_commands),
        }

    def _commands(self, specs: list[CommandSpec]) -> list[list[str]]:
        return [list(spec.command) for spec in specs]

    def _command_plan_details(self, policy: CompiledPolicy) -> dict[str, list[dict[str, object]]]:
        return {
            "setup": self._command_details(policy.setup_commands),
            "build": self._command_details(policy.build_commands),
            "format": self._command_details(policy.format_commands),
            "lint": self._command_details(policy.lint_commands),
            "typecheck": self._command_details(policy.typecheck_commands),
            "test": self._command_details(policy.test_commands),
            "smoke": self._command_details(policy.smoke_commands),
            "e2e": self._command_details(policy.e2e_commands),
            "security": self._command_details(policy.security_commands),
        }

    def _command_details(self, specs: list[CommandSpec]) -> list[dict[str, object]]:
        return [
            {"command": list(spec.command), "severity": spec.severity.value}
            for spec in specs
        ]

    def _protected_paths(
        self,
        repo_root: Path,
        policy: CompiledPolicy,
        profile: RepoProfile,
    ) -> list[str]:
        configured = {
            *policy.patch_policy.dependency_files,
            *policy.patch_policy.lockfiles,
            *policy.patch_policy.license_files,
            *policy.patch_policy.ci_files,
            *policy.patch_policy.security_sensitive_paths,
        }
        discovered = {
            str(path.relative_to(repo_root)) for path in profile.protected_artifact_files
        }
        return sorted({*configured, *discovered})

    def _repair_actionable(self, policy: CompiledPolicy, profile: RepoProfile) -> bool:
        if policy.repo_kind == "script_collection" and any(
            warning.startswith("Script readability:") for warning in profile.readability_warnings
        ):
            return True
        if "implicit_script_chain" in profile.workflow_sources and (
            profile.readability_warnings or profile.planning_warnings
        ):
            return True
        return False

    def _weak_or_missing_readme_coverage(self, profile: RepoProfile) -> bool:
        markers = (
            "README.md is missing",
            "README references a missing script",
            "README does not mention most detected scripts",
        )
        return any(
            marker in warning
            for warning in profile.readability_warnings
            for marker in markers
        )
