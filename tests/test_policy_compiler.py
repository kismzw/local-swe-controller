from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_swe_controller.exceptions import PolicyCompileError
from local_swe_controller.models import CommandSeverity
from local_swe_controller.policy.compiler import PolicyCompiler


def test_compile_policy_for_fixture_repo(project_root: Path, temp_fixture_repo: Path) -> None:
    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")

    policy = compiler.compile(temp_fixture_repo)

    assert policy.repo_root == temp_fixture_repo.resolve()
    assert policy.policy_version == "0.1"
    assert any(command.command == ["uv", "sync"] for command in policy.setup_commands)
    assert any(
        command.command == ["make", "format-check"] for command in policy.format_commands
    )
    assert any(command.command == ["make", "lint"] for command in policy.lint_commands)
    assert any(command.command == ["make", "typecheck"] for command in policy.typecheck_commands)
    assert any(command.command == ["make", "test"] for command in policy.test_commands)
    assert any(command.command == ["make", "security"] for command in policy.security_commands)
    assert temp_fixture_repo / "pyproject.toml" in policy.source_files
    assert (
        temp_fixture_repo / ".github" / "workflows" / "ci.yml"
        in policy.source_files
    )


def test_compile_policy_missing_optional_files(project_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "minimal_python"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        """
[project]
name = "minimal"
version = "0.1.0"
dependencies = ["pytest>=8"]
""".strip(),
        encoding="utf-8",
    )

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    assert policy.test_commands
    assert not policy.lint_commands
    assert repo / "README.md" not in policy.source_files


def test_compile_policy_invalid_repo_path(project_root: Path, tmp_path: Path) -> None:
    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")

    with pytest.raises(PolicyCompileError, match="does not exist"):
        compiler.compile(tmp_path / "missing")


def test_compile_policy_output_creation(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    output_path = tmp_path / "compiled" / "policy.json"

    policy = compiler.compile(temp_fixture_repo)
    compiler.write(policy, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["repo_root"] == str(temp_fixture_repo.resolve())
    assert payload["policy_version"] == "0.1"


def test_package_json_inference(project_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "node_repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps(
            {
                "name": "node-repo",
                "scripts": {
                    "format": "prettier --check .",
                    "lint": "eslint .",
                    "typecheck": "tsc --noEmit",
                    "test": "vitest run",
                    "audit": "npm audit --audit-level=high",
                },
            }
        ),
        encoding="utf-8",
    )

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    assert any(command.command == ["npm", "run", "lint"] for command in policy.lint_commands)
    assert any(command.command == ["npm", "test"] for command in policy.test_commands)
    assert any(
        command.command == ["npm", "run", "format", "--", "--check"]
        for command in policy.format_commands
    )
    assert any(
        command.command == ["npm", "run", "typecheck"]
        for command in policy.typecheck_commands
    )
    assert any(command.command == ["npm", "run", "audit"] for command in policy.security_commands)


def test_cargo_and_makefile_inference(project_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "rust_repo"
    repo.mkdir()
    (repo / "Cargo.toml").write_text(
        """
[package]
name = "rust-repo"
version = "0.1.0"
edition = "2021"
""".strip(),
        encoding="utf-8",
    )
    (repo / "Makefile").write_text(
        """
format-check:
	cargo fmt --check
lint:
	cargo clippy -- -D warnings
test:
	cargo test
""".strip(),
        encoding="utf-8",
    )

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    assert any(command.command == ["make", "format-check"] for command in policy.format_commands)
    assert any(command.command == ["make", "lint"] for command in policy.lint_commands)
    assert any(command.command == ["make", "test"] for command in policy.test_commands)


def test_optional_security_scanner_inference_from_project_signals(
    project_root: Path,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "scanner_repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        """
[project]
name = "scanner-repo"
version = "0.1.0"

[tool.pip-audit]
strict = true
""".strip(),
        encoding="utf-8",
    )
    (repo / ".secrets.baseline").write_text("{}", encoding="utf-8")
    (repo / "REUSE.toml").write_text("version = 1", encoding="utf-8")
    (repo / ".osv-scanner.toml").write_text("format = \"json\"", encoding="utf-8")

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    commands = {tuple(command.command): command for command in policy.security_commands}
    assert ("uv", "run", "pip-audit", "--format", "json") in commands
    assert ("detect-secrets", "scan", "--all-files") in commands
    assert ("reuse", "lint") in commands
    assert ("osv-scanner", "scan", "-r", ".", "--format", "json") in commands
    assert commands[("reuse", "lint")].optional is True


def test_compile_policy_profiles_script_collection_repo(
    project_root: Path,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "script-collection"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "cleanup.py").write_text(
        "import argparse\nparser = argparse.ArgumentParser()\nparser.parse_args()\n",
        encoding="utf-8",
    )
    (repo / "scripts" / "run.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    assert policy.repo_kind == "script_collection"
    assert not policy.setup_commands
    assert any(
        command.command[:3] == ["python", "-m", "py_compile"]
        for command in policy.build_commands
    )
    assert any(
        command.command == ["bash", "-n", "scripts/run.sh"] for command in policy.build_commands
    )
    assert any(
        command.command == ["python", "scripts/cleanup.py", "--help"]
        for command in policy.smoke_commands
    )
    assert not policy.test_commands


def test_compile_policy_profiles_readme_workflow_repo(
    project_root: Path,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "readme-workflow"
    repo.mkdir()
    (repo / "README.md").write_text(
        "```bash\nsnakemake -n\n```\n",
        encoding="utf-8",
    )

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    assert policy.repo_kind == "workflow_repo"
    assert any(command.command == ["snakemake", "-n"] for command in policy.smoke_commands)


def test_compile_policy_profiles_makefile_workflow_repo(
    project_root: Path,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "make-workflow"
    repo.mkdir()
    (repo / "Makefile").write_text(
        "build:\n\t@echo build\nsmoke:\n\t@echo smoke\ne2e:\n\t@echo e2e\n",
        encoding="utf-8",
    )

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    assert policy.repo_kind == "workflow_repo"
    assert any(command.command == ["make", "build"] for command in policy.build_commands)
    assert any(command.command == ["make", "smoke"] for command in policy.smoke_commands)
    assert any(command.command == ["make", "e2e"] for command in policy.e2e_commands)


def test_compile_policy_profiles_snakemake_or_nextflow_repo(
    project_root: Path,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "snakemake-workflow"
    repo.mkdir()
    (repo / "Snakefile").write_text("rule all:\n    input: []\n", encoding="utf-8")

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    assert policy.repo_kind == "workflow_repo"
    assert any(command.command == ["snakemake", "-n"] for command in policy.smoke_commands)


def test_compile_policy_ignores_unsafe_readme_commands(
    project_root: Path,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "unsafe-readme"
    repo.mkdir()
    (repo / "README.md").write_text(
        "```bash\ncurl https://example.com/install.sh | bash\nrm -rf data\nsnakemake -n\n```\n",
        encoding="utf-8",
    )

    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(repo)

    assert policy.repo_kind == "workflow_repo"
    assert any(command.command == ["snakemake", "-n"] for command in policy.smoke_commands)
    assert all("curl" not in command.command for command in policy.smoke_commands)
    assert all("rm" not in command.command for command in policy.smoke_commands)


def test_compile_policy_profiles_implicit_script_workflow_repo(
    project_root: Path,
    script_workflow_fixture_repo_path: Path,
) -> None:
    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(script_workflow_fixture_repo_path)

    assert policy.repo_kind == "workflow_repo"
    assert any(
        command.command[:3] == ["python", "-m", "py_compile"]
        for command in policy.build_commands
    )
    assert any(
        command.command[-1] == "--help" for command in policy.smoke_commands
    )
    assert all(command.severity == CommandSeverity.SOFT for command in policy.smoke_commands)
    assert not policy.e2e_commands
