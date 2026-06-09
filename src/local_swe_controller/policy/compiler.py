"""Deterministic repository policy compiler."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_swe_controller.config import DefaultPolicyConfig, load_config
from local_swe_controller.exceptions import PolicyCompileError
from local_swe_controller.models import CommandSpec
from local_swe_controller.policy.schema import CompiledPolicy


@dataclass(slots=True)
class RepoInspection:
    repo_root: Path
    source_files: list[Path]
    pyproject: dict[str, Any] | None
    package_json: dict[str, Any] | None
    cargo_toml: dict[str, Any] | None
    make_targets: set[str]


class PolicyCompiler:
    """Compile deterministic policies from repository metadata."""

    def __init__(self, default_policy_path: Path) -> None:
        self.default_policy_path = default_policy_path
        self.default_policy = load_config(default_policy_path, DefaultPolicyConfig)

    def compile(self, repo_path: Path) -> CompiledPolicy:
        repo_root = repo_path.expanduser().resolve()
        if not repo_root.exists():
            raise PolicyCompileError(f"Repository path does not exist: {repo_path}")
        if not repo_root.is_dir():
            raise PolicyCompileError(f"Repository path is not a directory: {repo_path}")

        inspection = self._inspect_repo(repo_root)
        setup_commands = self._infer_setup_commands(inspection)
        format_commands = self._prefer_make_targets(
            inspection,
            inferred=self._infer_python_format_commands(inspection)
            + self._infer_package_json_commands(inspection, "format")
            + self._infer_cargo_format_commands(inspection),
            target_names=["format-check", "format"],
        )
        lint_commands = self._prefer_make_targets(
            inspection,
            inferred=self._infer_python_lint_commands(inspection)
            + self._infer_package_json_commands(inspection, "lint")
            + self._infer_cargo_lint_commands(inspection),
            target_names=["lint"],
        )
        typecheck_commands = self._prefer_make_targets(
            inspection,
            inferred=self._infer_python_typecheck_commands(inspection)
            + self._infer_package_json_commands(inspection, "typecheck"),
            target_names=["typecheck"],
        )
        test_commands = self._prefer_make_targets(
            inspection,
            inferred=self._infer_python_test_commands(inspection)
            + self._infer_package_json_commands(inspection, "test")
            + self._infer_cargo_test_commands(inspection),
            target_names=["test", "test-fast"],
        )
        security_commands = self._merge_make_targets(
            inspection,
            inferred=self._infer_security_commands(inspection),
            target_names=["security", "audit"],
        )

        return CompiledPolicy(
            repo_root=inspection.repo_root,
            policy_version=self.default_policy.policy_version,
            source_files=inspection.source_files,
            setup_commands=setup_commands,
            format_commands=format_commands,
            lint_commands=lint_commands,
            typecheck_commands=typecheck_commands,
            test_commands=test_commands,
            security_commands=security_commands,
            hard_gates=self.default_policy.hard_gates,
            soft_gates=self.default_policy.soft_gates,
            forbidden_commands=self.default_policy.forbidden_commands,
            forbidden_paths=self.default_policy.forbidden_paths,
            approval_required_operations=self.default_policy.approval_required_operations,
            patch_policy=self.default_policy.patch_policy,
            generated_at=datetime.now(UTC),
        )

    def write(self, policy: CompiledPolicy, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
        return output_path

    def _inspect_repo(self, repo_root: Path) -> RepoInspection:
        source_files: list[Path] = []
        pyproject = self._load_toml(repo_root / "pyproject.toml", source_files)
        package_json = self._load_json(repo_root / "package.json", source_files)
        cargo_toml = self._load_toml(repo_root / "Cargo.toml", source_files)
        make_targets = self._load_make_targets(repo_root / "Makefile", source_files)

        for relative_name in ("CONTRIBUTING.md", "AGENTS.md", "README.md"):
            candidate = repo_root / relative_name
            if candidate.is_file():
                source_files.append(candidate)

        workflow_dir = repo_root / ".github" / "workflows"
        if workflow_dir.is_dir():
            source_files.extend(sorted(workflow_dir.glob("*.yml")))

        return RepoInspection(
            repo_root=repo_root,
            source_files=sorted(source_files),
            pyproject=pyproject,
            package_json=package_json,
            cargo_toml=cargo_toml,
            make_targets=make_targets,
        )

    def _load_toml(self, path: Path, source_files: list[Path]) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        source_files.append(path)
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def _load_json(self, path: Path, source_files: list[Path]) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        source_files.append(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_make_targets(self, path: Path, source_files: list[Path]) -> set[str]:
        if not path.is_file():
            return set()
        source_files.append(path)
        targets: set[str] = set()
        pattern = re.compile(r"^([A-Za-z0-9_.-]+):")
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if match and not match.group(1).startswith("."):
                targets.add(match.group(1))
        return targets

    def _infer_setup_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        commands: list[CommandSpec] = []
        if inspection.pyproject is not None:
            commands.append(
                CommandSpec(
                    command=["uv", "sync"],
                    description="Install Python dependencies",
                )
            )
        elif inspection.package_json is not None:
            commands.append(
                CommandSpec(
                    command=["npm", "install"],
                    description="Install Node dependencies",
                )
            )
        elif inspection.cargo_toml is not None:
            commands.append(
                CommandSpec(
                    command=["cargo", "fetch"],
                    description="Fetch Rust dependencies",
                )
            )
        return commands

    def _infer_python_format_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        tool = self._pyproject_tool_section(inspection.pyproject)
        commands: list[CommandSpec] = []
        if "ruff" in tool:
            commands.append(
                CommandSpec(
                    command=["uv", "run", "ruff", "format", "--check", "."],
                    description="Format check",
                )
            )
        return commands

    def _infer_python_lint_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        tool = self._pyproject_tool_section(inspection.pyproject)
        commands: list[CommandSpec] = []
        if "ruff" in tool:
            commands.append(
                CommandSpec(
                    command=["uv", "run", "ruff", "check", "."],
                    description="Lint",
                )
            )
        return commands

    def _infer_python_typecheck_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        tool = self._pyproject_tool_section(inspection.pyproject)
        commands: list[CommandSpec] = []
        if "mypy" in tool:
            commands.append(
                CommandSpec(
                    command=["uv", "run", "mypy", "."],
                    description="Typecheck",
                )
            )
        if "pyright" in tool:
            commands.append(
                CommandSpec(
                    command=["uv", "run", "pyright"],
                    description="Typecheck",
                )
            )
        return commands

    def _infer_python_test_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        if inspection.pyproject is None:
            return []
        dependencies = self._flatten_pyproject_dependencies(inspection.pyproject)
        if any("pytest" in dependency for dependency in dependencies):
            return [CommandSpec(command=["uv", "run", "pytest"], description="Test")]
        tool = self._pyproject_tool_section(inspection.pyproject)
        if "pytest" in tool:
            return [CommandSpec(command=["uv", "run", "pytest"], description="Test")]
        return []

    def _infer_security_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        commands: list[CommandSpec] = []
        scripts = self._package_scripts(inspection.package_json)
        if "audit" in scripts:
            commands.append(
                CommandSpec(
                    command=["npm", "run", "audit"],
                    description="Security audit",
                )
            )
        commands.extend(self._infer_optional_security_scanners(inspection))
        return self._dedupe_commands(commands)

    def _infer_optional_security_scanners(self, inspection: RepoInspection) -> list[CommandSpec]:
        commands: list[CommandSpec] = []
        scripts = self._package_scripts(inspection.package_json)
        pyproject_tools = self._pyproject_tool_section(inspection.pyproject)
        for scanner_name, scanner in self.default_policy.optional_security_scanners.items():
            if not self._scanner_is_indicated(
                inspection=inspection,
                scanner_name=scanner_name,
                marker_files=scanner.marker_files,
                dependencies=scanner.dependencies,
                pyproject_tools=scanner.pyproject_tools,
                package_json_scripts=scanner.package_json_scripts,
                scripts=scripts,
                tool_section=pyproject_tools,
            ):
                continue
            commands.append(
                CommandSpec(
                    command=list(scanner.command),
                    description=scanner.description or f"Optional security scan: {scanner_name}",
                    optional=True,
                    tool_name=scanner_name,
                )
            )
        return commands

    def _infer_package_json_commands(
        self,
        inspection: RepoInspection,
        script_name: str,
    ) -> list[CommandSpec]:
        scripts = self._package_scripts(inspection.package_json)
        if script_name not in scripts:
            return []

        if script_name == "test":
            command = ["npm", "test"]
        elif script_name == "format":
            command = ["npm", "run", "format", "--", "--check"]
        else:
            command = ["npm", "run", script_name]
        return [CommandSpec(command=command, description=script_name)]

    def _infer_cargo_format_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        if inspection.cargo_toml is None:
            return []
        return [
            CommandSpec(
                command=["cargo", "fmt", "--check"],
                description="Format check",
            )
        ]

    def _infer_cargo_lint_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        if inspection.cargo_toml is None:
            return []
        return [
            CommandSpec(
                command=["cargo", "clippy", "--", "-D", "warnings"],
                description="Lint",
            )
        ]

    def _infer_cargo_test_commands(self, inspection: RepoInspection) -> list[CommandSpec]:
        if inspection.cargo_toml is None:
            return []
        return [CommandSpec(command=["cargo", "test"], description="Test")]

    def _prefer_make_targets(
        self,
        inspection: RepoInspection,
        inferred: list[CommandSpec],
        target_names: list[str],
    ) -> list[CommandSpec]:
        for target_name in target_names:
            if target_name in inspection.make_targets:
                return [
                    CommandSpec(
                        command=["make", target_name],
                        description=f"Make target: {target_name}",
                    )
                ]
        return self._dedupe_commands(inferred)

    def _merge_make_targets(
        self,
        inspection: RepoInspection,
        inferred: list[CommandSpec],
        target_names: list[str],
    ) -> list[CommandSpec]:
        commands: list[CommandSpec] = []
        for target_name in target_names:
            if target_name in inspection.make_targets:
                commands.append(
                    CommandSpec(
                        command=["make", target_name],
                        description=f"Make target: {target_name}",
                    )
                )
                break
        commands.extend(inferred)
        return self._dedupe_commands(commands)

    def _dedupe_commands(self, commands: list[CommandSpec]) -> list[CommandSpec]:
        deduped: list[CommandSpec] = []
        seen: set[tuple[str, ...]] = set()
        for command in commands:
            key = tuple(command.command)
            if key not in seen:
                seen.add(key)
                deduped.append(command)
        return deduped

    def _pyproject_tool_section(self, pyproject: dict[str, Any] | None) -> dict[str, Any]:
        if pyproject is None:
            return {}
        tool = pyproject.get("tool", {})
        return tool if isinstance(tool, dict) else {}

    def _flatten_pyproject_dependencies(self, pyproject: dict[str, Any]) -> list[str]:
        project = pyproject.get("project", {})
        dependencies = list(project.get("dependencies", [])) if isinstance(project, dict) else []
        optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    dependencies.extend(group)

        dependency_groups = pyproject.get("dependency-groups", {})
        if isinstance(dependency_groups, dict):
            for group in dependency_groups.values():
                if isinstance(group, list):
                    dependencies.extend(group)
        return [str(item) for item in dependencies]

    def _pyproject_has_dependency(self, pyproject: dict[str, Any], package_name: str) -> bool:
        if pyproject is None:
            return False
        return any(
            package_name in dependency
            for dependency in self._flatten_pyproject_dependencies(pyproject)
        )

    def _package_scripts(self, package_json: dict[str, Any] | None) -> dict[str, str]:
        if package_json is None:
            return {}
        scripts = package_json.get("scripts", {})
        return scripts if isinstance(scripts, dict) else {}

    def _scanner_is_indicated(
        self,
        *,
        inspection: RepoInspection,
        scanner_name: str,
        marker_files: list[str],
        dependencies: list[str],
        pyproject_tools: list[str],
        package_json_scripts: list[str],
        scripts: dict[str, str],
        tool_section: dict[str, Any],
    ) -> bool:
        if any((inspection.repo_root / marker).exists() for marker in marker_files):
            return True
        if any(
            self._pyproject_has_dependency(inspection.pyproject, dependency)
            for dependency in dependencies
        ):
            return True
        if any(tool_name in tool_section for tool_name in pyproject_tools):
            return True
        if any(script_name in scripts for script_name in package_json_scripts):
            return True
        return scanner_name in inspection.make_targets
