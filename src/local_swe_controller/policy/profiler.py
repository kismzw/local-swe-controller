"""Deterministic repository profiling helpers."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from local_swe_controller.models import CommandSeverity, CommandSpec, MissingToolBehavior

_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".local-swe",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "build",
}
_SCRIPT_SUFFIXES: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "shell": (".sh", ".bash", ".zsh"),
    "r": (".r",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx"),
    "notebook": (".ipynb",),
}
_WORKFLOW_FILENAMES = {
    "Makefile",
    "Snakefile",
    "snakefile",
    "main.nf",
    "nextflow.config",
    "Justfile",
}
_README_NAMES = ("README.md", "README.rst", "README.txt")
_SAFE_COMMAND_PREFIXES = {
    "python",
    "python3",
    "pytest",
    "make",
    "snakemake",
    "nextflow",
    "bash",
    "sh",
    "Rscript",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "uv",
}
_UNSAFE_COMMAND_PREFIXES = {
    "rm",
    "sudo",
    "curl",
    "wget",
    "git",
    "docker",
    "podman",
    "kubectl",
    "ssh",
    "scp",
}
_HELP_MARKERS = ("argparse", "click", "typer", "getopts", "optparse", "commandArgs", "yargs")
_SCRIPT_REFERENCE_PREFIXES = ("python ", "python3 ", "bash ", "sh ", "Rscript ", "node ")
_IMPLICIT_WORKFLOW_DIR_NAMES = {
    "datapipe",
    "dataset",
    "preprocess",
    "downstream",
    "train",
    "training",
    "test",
    "eval",
    "evaluation",
    "inference",
    "pipeline",
    "scripts",
}
_IMPLICIT_WORKFLOW_FILE_TOKENS = (
    "build",
    "prepare",
    "preprocess",
    "train",
    "test",
    "eval",
    "predict",
    "inference",
    "finetune",
    "regression",
)
_PRIMARY_STAGE_TOKEN_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("build", ("build", "prepare", "preprocess", "extract")),
    ("test", ("test",)),
    ("train", ("train", "finetune")),
    ("eval", ("evaluate", "evaluation", "eval", "predict", "inference")),
)
_ARTIFACT_SUFFIXES = {
    ".csv",
    ".h5",
    ".hdf5",
    ".pt",
    ".pth",
    ".ckpt",
    ".parquet",
    ".zarr",
    ".tif",
    ".svs",
}
_SCRIPT_PATH_PATTERN = re.compile(
    r"(?P<path>[\w./ -]+\.(?:py|sh|bash|zsh|R|r|js|jsx|ts|tsx|ipynb))"
)
_UNCLEAR_SCRIPT_NAME_PATTERN = re.compile(
    r"^(?:run\d+|final\d+|test|old(?:[_-]script)?|tmp[_-]?.+|.+ copy)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RepoProfile:
    repo_kind: str
    confidence: str | None = None
    scripts_by_language: dict[str, list[Path]] = field(default_factory=dict)
    workflow_files: list[Path] = field(default_factory=list)
    workflow_sources: list[str] = field(default_factory=list)
    readme_commands: list[CommandSpec] = field(default_factory=list)
    ignored_readme_commands: list[str] = field(default_factory=list)
    has_package_manifest: bool = False
    readability_warnings: list[str] = field(default_factory=list)
    protected_artifact_files: list[Path] = field(default_factory=list)
    implicit_workflow_stages: list[str] = field(default_factory=list)
    workflow_stage_scripts: dict[str, list[Path]] = field(default_factory=dict)
    config_files: list[Path] = field(default_factory=list)
    planning_warnings: list[str] = field(default_factory=list)


class RepoProfiler:
    """Classify repository shape from deterministic local signals."""

    def profile(self, repo_root: Path) -> RepoProfile:
        scripts_by_language = self._collect_scripts(repo_root)
        workflow_files = self._collect_workflow_files(repo_root)
        readme_commands, ignored_readme_commands = self._collect_readme_commands(repo_root)
        config_files = self._collect_config_files(repo_root)
        stage_scripts = self._implicit_workflow_stage_scripts(repo_root, scripts_by_language)
        implicit_workflow = self._is_implicit_script_workflow(
            repo_root=repo_root,
            stage_scripts=stage_scripts,
            scripts_by_language=scripts_by_language,
            config_files=config_files,
        )
        has_package_manifest = any(
            (repo_root / name).is_file()
            for name in (
                "pyproject.toml",
                "package.json",
                "Cargo.toml",
                "setup.py",
                "setup.cfg",
                "requirements.txt",
            )
        )
        repo_kind = self._classify(
            repo_root=repo_root,
            scripts_by_language=scripts_by_language,
            workflow_files=workflow_files,
            readme_commands=readme_commands,
            has_package_manifest=has_package_manifest,
            implicit_workflow=implicit_workflow,
        )
        return RepoProfile(
            repo_kind=repo_kind,
            confidence=self._confidence(
                repo_kind=repo_kind,
                has_package_manifest=has_package_manifest,
                workflow_files=workflow_files,
                scripts_by_language=scripts_by_language,
                implicit_workflow=implicit_workflow,
            ),
            scripts_by_language=scripts_by_language,
            workflow_files=workflow_files,
            workflow_sources=self._workflow_sources(
                repo_root=repo_root,
                workflow_files=workflow_files,
                readme_commands=readme_commands,
                implicit_workflow=implicit_workflow,
            ),
            readme_commands=readme_commands,
            ignored_readme_commands=ignored_readme_commands,
            has_package_manifest=has_package_manifest,
            readability_warnings=self._readability_warnings(
                repo_root=repo_root,
                repo_kind=repo_kind,
                scripts_by_language=scripts_by_language,
                implicit_workflow=implicit_workflow,
            ),
            protected_artifact_files=self._protected_artifact_files(repo_root),
            implicit_workflow_stages=[
                stage
                for stage in ("build", "train", "test", "eval")
                if stage_scripts.get(stage)
            ],
            workflow_stage_scripts=stage_scripts,
            config_files=config_files,
            planning_warnings=self._planning_warnings(
                repo_kind=repo_kind,
                implicit_workflow=implicit_workflow,
                config_files=config_files,
            ),
        )

    def _collect_scripts(self, repo_root: Path) -> dict[str, list[Path]]:
        scripts: dict[str, list[Path]] = {language: [] for language in _SCRIPT_SUFFIXES}
        for path in sorted(repo_root.rglob("*")):
            if not path.is_file() or self._exclude(path, repo_root):
                continue
            suffix = path.suffix.casefold()
            for language, suffixes in _SCRIPT_SUFFIXES.items():
                if suffix in suffixes:
                    scripts[language].append(path)
                    break
        return {language: paths for language, paths in scripts.items() if paths}

    def _collect_workflow_files(self, repo_root: Path) -> list[Path]:
        workflow_files: list[Path] = []
        for name in _WORKFLOW_FILENAMES:
            candidate = repo_root / name
            if candidate.is_file():
                workflow_files.append(candidate)
        workflow_dir = repo_root / ".github" / "workflows"
        if workflow_dir.is_dir():
            workflow_files.extend(sorted(workflow_dir.glob("*.y*ml")))
        return sorted(set(workflow_files))

    def _collect_config_files(self, repo_root: Path) -> list[Path]:
        config_files: list[Path] = []
        for path in sorted(repo_root.rglob("*")):
            if not path.is_file() or self._exclude(path, repo_root):
                continue
            rel = path.relative_to(repo_root)
            if not (
                "config" in path.name.casefold()
                or any(part.casefold() == "configs" for part in rel.parts[:-1])
            ):
                continue
            if path.suffix.casefold() in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"}:
                config_files.append(path)
        return config_files

    def _collect_readme_commands(self, repo_root: Path) -> tuple[list[CommandSpec], list[str]]:
        commands: list[CommandSpec] = []
        ignored_commands: list[str] = []
        for name in _README_NAMES:
            path = repo_root / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            safe_commands, ignored = self._extract_safe_commands_from_readme(text)
            commands.extend(safe_commands)
            ignored_commands.extend(ignored)
        deduped: list[CommandSpec] = []
        seen: set[tuple[str, ...]] = set()
        for command in commands:
            key = tuple(command.command)
            if key not in seen:
                seen.add(key)
                deduped.append(command)
        return deduped, sorted(set(ignored_commands))

    def _extract_safe_commands_from_readme(self, text: str) -> tuple[list[CommandSpec], list[str]]:
        commands: list[CommandSpec] = []
        ignored: list[str] = []
        in_block = False
        block_language = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                if not in_block:
                    block_language = stripped.removeprefix("```").strip().casefold()
                    in_block = block_language in {"bash", "sh", "shell", "console", "text", ""}
                else:
                    in_block = False
                    block_language = ""
                continue
            if not in_block:
                continue
            candidate = stripped.removeprefix("$ ").removeprefix("> ").strip()
            if not candidate or candidate.startswith("#"):
                continue
            if any(token in candidate for token in ("|", "&&", "||", ";", ">", "<", "$(", "`")):
                ignored.append(candidate)
                continue
            try:
                argv = shlex.split(candidate)
            except ValueError:
                ignored.append(candidate)
                continue
            if not argv:
                continue
            if "=" in argv[0]:
                ignored.append(candidate)
                continue
            head = argv[0]
            if head in _UNSAFE_COMMAND_PREFIXES or head not in _SAFE_COMMAND_PREFIXES:
                ignored.append(candidate)
                continue
            if head == "docker" and "--privileged" in argv:
                ignored.append(candidate)
                continue
            commands.append(CommandSpec(command=argv, description="README command", optional=True))
        return commands, ignored

    def cli_help_candidates(
        self,
        repo_root: Path,
        scripts_by_language: dict[str, list[Path]],
    ) -> list[Path]:
        candidates: list[Path] = []
        for paths in scripts_by_language.values():
            for path in paths:
                if len(candidates) >= 3:
                    return candidates
                if not self._looks_like_cli_script(repo_root, path):
                    continue
                candidates.append(path)
        return candidates

    def _looks_like_cli_script(self, repo_root: Path, path: Path) -> bool:
        rel = path.relative_to(repo_root)
        if rel.parts and rel.parts[0] in {"scripts", "bin", "cli"}:
            return True
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        lowered = content.casefold()
        return any(marker.casefold() in lowered for marker in _HELP_MARKERS)

    def _classify(
        self,
        *,
        repo_root: Path,
        scripts_by_language: dict[str, list[Path]],
        workflow_files: list[Path],
        readme_commands: list[CommandSpec],
        has_package_manifest: bool,
        implicit_workflow: bool,
    ) -> str:
        has_package_layout = any(
            path.exists()
            for path in (
                repo_root / "src",
                repo_root / "tests",
                repo_root / "package.json",
                repo_root / "pyproject.toml",
                repo_root / "Cargo.toml",
            )
        )
        workflow_signals = bool(workflow_files) or any(
            self._readme_command_looks_workflow_like(command.command) for command in readme_commands
        )
        script_count = sum(len(paths) for paths in scripts_by_language.values())
        if has_package_manifest and has_package_layout and not workflow_signals:
            return "package_repo"
        if workflow_signals:
            return "workflow_repo"
        if implicit_workflow:
            return "workflow_repo"
        if script_count > 0:
            return "script_collection"
        if has_package_manifest:
            return "package_repo"
        return "unknown"

    def _readme_command_looks_workflow_like(self, argv: list[str]) -> bool:
        text = " ".join(argv).casefold()
        return argv[0] in {"make", "snakemake", "nextflow"} or any(
            marker in text for marker in ("workflow", "pipeline", "smoke", "dry-run")
        )

    def _workflow_sources(
        self,
        *,
        repo_root: Path,
        workflow_files: list[Path],
        readme_commands: list[CommandSpec],
        implicit_workflow: bool,
    ) -> list[str]:
        sources: list[str] = []
        if (repo_root / "Makefile").is_file():
            sources.append("Makefile")
        for path in workflow_files:
            name = path.name
            if name not in sources:
                sources.append(name)
        if any(
            self._readme_command_looks_workflow_like(command.command)
            for command in readme_commands
        ):
            sources.append("README")
        if implicit_workflow:
            sources.append("implicit_script_chain")
        return sources

    def _confidence(
        self,
        *,
        repo_kind: str,
        has_package_manifest: bool,
        workflow_files: list[Path],
        scripts_by_language: dict[str, list[Path]],
        implicit_workflow: bool,
    ) -> str | None:
        if repo_kind == "unknown":
            return "low"
        if repo_kind == "workflow_repo" and workflow_files:
            return "high"
        if repo_kind == "workflow_repo" and implicit_workflow:
            return "medium"
        if repo_kind == "package_repo" and has_package_manifest:
            return "high"
        if repo_kind == "script_collection" and scripts_by_language:
            return "high"
        return "medium"

    def script_validation_commands(
        self,
        repo_root: Path,
        scripts_by_language: dict[str, list[Path]],
        *,
        smoke_severity: CommandSeverity = CommandSeverity.HARD,
    ) -> tuple[list[CommandSpec], list[CommandSpec]]:
        syntax_commands: list[CommandSpec] = []
        smoke_commands: list[CommandSpec] = []
        python_files = [
            str(path.relative_to(repo_root))
            for path in scripts_by_language.get("python", [])
        ]
        if python_files:
            syntax_commands.append(
                CommandSpec(
                    command=["python", "-m", "py_compile", *python_files],
                    description="Python syntax check",
                )
            )
        shell_files = [
            str(path.relative_to(repo_root))
            for path in scripts_by_language.get("shell", [])
        ]
        for rel in shell_files:
            syntax_commands.append(
                CommandSpec(
                    command=["bash", "-n", rel],
                    description=f"Shell syntax check: {rel}",
                )
            )
        for path in scripts_by_language.get("javascript", []):
            rel = str(path.relative_to(repo_root))
            syntax_commands.append(
                CommandSpec(
                    command=["node", "--check", rel],
                    description=f"JavaScript syntax check: {rel}",
                    optional=True,
                    tool_name="node",
                    missing_tool_behavior=MissingToolBehavior.SKIP,
                )
            )
        if scripts_by_language.get("typescript"):
            syntax_commands.append(
                CommandSpec(
                    command=["tsc", "--noEmit", "--pretty", "false"],
                    description="TypeScript syntax check",
                    optional=True,
                    tool_name="tsc",
                    missing_tool_behavior=MissingToolBehavior.SKIP,
                )
            )
        for path in scripts_by_language.get("r", []):
            rel = str(path.relative_to(repo_root))
            syntax_commands.append(
                CommandSpec(
                    command=["Rscript", "-e", f"parse(file='{rel}')"],
                    description=f"R parse check: {rel}",
                    optional=True,
                    tool_name="Rscript",
                    missing_tool_behavior=MissingToolBehavior.SKIP,
                )
            )
        for path in scripts_by_language.get("notebook", []):
            rel = str(path.relative_to(repo_root))
            syntax_commands.append(
                CommandSpec(
                    command=["python", "-m", "json.tool", rel],
                    description=f"Notebook JSON check: {rel}",
                )
            )

        for path in self.cli_help_candidates(repo_root, scripts_by_language):
            rel = str(path.relative_to(repo_root))
            suffix = path.suffix.casefold()
            if suffix == ".py":
                command = ["python", rel, "--help"]
            elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
                command = ["node", rel, "--help"]
            else:
                continue
            smoke_commands.append(
                CommandSpec(
                    command=command,
                    description=f"CLI help check: {rel}",
                    optional=suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"},
                    tool_name=command[0],
                    missing_tool_behavior=MissingToolBehavior.SKIP,
                    severity=smoke_severity,
                )
            )
        return syntax_commands, smoke_commands

    def _implicit_workflow_stage_scripts(
        self,
        repo_root: Path,
        scripts_by_language: dict[str, list[Path]],
    ) -> dict[str, list[Path]]:
        python_scripts = scripts_by_language.get("python", [])
        stages: dict[str, list[Path]] = {"build": [], "train": [], "test": [], "eval": []}
        for path in python_scripts:
            stage = self._infer_implicit_workflow_stage(repo_root, path)
            if stage is not None:
                stages[stage].append(path)
        return {stage: sorted(paths) for stage, paths in stages.items() if paths}

    def _infer_implicit_workflow_stage(self, repo_root: Path, path: Path) -> str | None:
        rel = path.relative_to(repo_root)
        stem = path.stem.casefold()
        basename_stage = self._match_stage_tokens(stem)
        if basename_stage is not None:
            return basename_stage

        dir_parts = tuple(part.casefold() for part in rel.parts[:-1])
        for part in reversed(dir_parts):
            stage = self._match_stage_tokens(part)
            if stage is not None:
                return stage
        return None

    def _match_stage_tokens(self, text: str) -> str | None:
        for stage, tokens in _PRIMARY_STAGE_TOKEN_ORDER:
            if any(token in text for token in tokens):
                return stage
        return None

    def _is_implicit_script_workflow(
        self,
        *,
        repo_root: Path,
        stage_scripts: dict[str, list[Path]],
        scripts_by_language: dict[str, list[Path]],
        config_files: list[Path],
    ) -> bool:
        if not stage_scripts:
            return False
        has_train_test_pair = "train" in stage_scripts and "test" in stage_scripts
        has_build_chain = "build" in stage_scripts and (
            "train" in stage_scripts or "test" in stage_scripts or "eval" in stage_scripts
        )
        workflow_dir_signal = any(
            part.casefold() in _IMPLICIT_WORKFLOW_DIR_NAMES
            for paths in stage_scripts.values()
            for path in paths
            for part in path.relative_to(repo_root).parts[:-1]
        )
        filename_signal_count = sum(
            1
            for path in scripts_by_language.get("python", [])
            if any(token in path.stem.casefold() for token in _IMPLICIT_WORKFLOW_FILE_TOKENS)
        )
        script_signal_count = sum(len(paths) for paths in stage_scripts.values())
        return (
            has_train_test_pair
            or has_build_chain
            or (workflow_dir_signal and script_signal_count >= 3)
            or filename_signal_count >= 3
            or (config_files and len(stage_scripts) >= 2)
        )

    def _planning_warnings(
        self,
        *,
        repo_kind: str,
        implicit_workflow: bool,
        config_files: list[Path],
    ) -> list[str]:
        warnings: list[str] = []
        if repo_kind == "workflow_repo" and implicit_workflow:
            warnings.append(
                "Workflow planning: full train/test/eval execution was not inferred because the "
                "detected script chain appears data-heavy or long-running."
            )
            if not config_files:
                warnings.append(
                    "Workflow planning: no example config files were detected for a safe dry-run."
                )
            warnings.append(
                "Workflow planning: required data or runtime inputs are unknown, so validation "
                "stays at syntax and bounded help checks."
            )
        return warnings

    def _readability_warnings(
        self,
        *,
        repo_root: Path,
        repo_kind: str,
        scripts_by_language: dict[str, list[Path]],
        implicit_workflow: bool,
    ) -> list[str]:
        if repo_kind not in {"script_collection", "workflow_repo"}:
            return []
        if repo_kind == "workflow_repo" and not implicit_workflow:
            return []

        warnings: list[str] = []
        readme = self._first_readme(repo_root)
        all_scripts = sorted(
            path.relative_to(repo_root)
            for paths in scripts_by_language.values()
            for path in paths
        )
        top_level_scripts = [path for path in all_scripts if len(path.parts) == 1]
        if readme is None:
            warnings.append("Script readability: README.md is missing.")
        script_dir = repo_root / "scripts"
        if (
            repo_kind == "script_collection"
            and len(top_level_scripts) >= 4
            and not script_dir.is_dir()
        ):
            warnings.append(
                "Script readability: many scripts live at the repository top level without a "
                "scripts/ folder."
            )
        for rel in all_scripts:
            stem = rel.stem
            if repo_kind == "script_collection" and " " in rel.name:
                warnings.append(
                    f"Script readability: script filename contains spaces: {rel.as_posix()}"
                )
            if repo_kind == "script_collection" and _UNCLEAR_SCRIPT_NAME_PATTERN.match(stem):
                warnings.append(
                    f"Script readability: script name is unclear: {rel.as_posix()}"
                )
            if rel.suffix.casefold() == ".py" and self._is_executable_python_without_help(
                repo_root / rel
            ):
                warnings.append(
                    "Script readability: executable Python script lacks a deterministic --help "
                    f"path: {rel.as_posix()}"
                )

        if readme is not None:
            readme_text = readme.read_text(encoding="utf-8")
            mentioned_scripts = {
                self._normalize_script_reference(match.group("path"))
                for match in _SCRIPT_PATH_PATTERN.finditer(readme_text)
                if self._normalize_script_reference(match.group("path")) is not None
            }
            existing_names = {path.name for path in all_scripts}
            missing_refs = sorted(name for name in mentioned_scripts if name not in existing_names)
            for name in missing_refs:
                warnings.append(
                    f"Script readability: README references a missing script: {name}"
                )
            mentioned_existing = sorted(existing_names & mentioned_scripts)
            if all_scripts and (len(mentioned_existing) * 2) < len(existing_names):
                warnings.append(
                    "Script readability: README does not mention most detected scripts."
                )

        for path in sorted(repo_root.rglob("*")):
            if not path.is_file() or self._exclude(path, repo_root):
                continue
            if path.suffix.casefold() in _ARTIFACT_SUFFIXES:
                warnings.append(
                    "Script readability: data or artifact file should stay protected from rename: "
                    f"{path.relative_to(repo_root).as_posix()}"
                )
        return warnings

    def _normalize_script_reference(self, raw: str) -> str | None:
        cleaned = raw.strip().strip("`'\".,:;()[]{}")
        for prefix in _SCRIPT_REFERENCE_PREFIXES:
            if cleaned.startswith(prefix):
                cleaned = cleaned.removeprefix(prefix).strip()
                break
        if not cleaned:
            return None
        return Path(cleaned).name

    def _protected_artifact_files(self, repo_root: Path) -> list[Path]:
        protected: list[Path] = []
        for path in sorted(repo_root.rglob("*")):
            if not path.is_file() or self._exclude(path, repo_root):
                continue
            if path.suffix.casefold() in _ARTIFACT_SUFFIXES:
                protected.append(path)
        return protected

    def _first_readme(self, repo_root: Path) -> Path | None:
        for name in _README_NAMES:
            path = repo_root / name
            if path.is_file():
                return path
        return None

    def _is_executable_python_without_help(self, path: Path) -> bool:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        lowered = content.casefold()
        has_main = "__name__" in content and "__main__" in content
        has_help = any(marker.casefold() in lowered for marker in _HELP_MARKERS)
        return has_main and not has_help

    def _exclude(self, path: Path, repo_root: Path) -> bool:
        rel = path.relative_to(repo_root)
        return any(part in _EXCLUDED_DIRS for part in rel.parts[:-1])


def json_command_spec(argv: list[str], description: str) -> CommandSpec:
    """Small helper retained for tests and future command generation."""

    return CommandSpec(command=argv, description=description, optional=True)
