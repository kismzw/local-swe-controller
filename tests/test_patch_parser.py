from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_swe_controller.config import PatchPolicyConfig, PatchRejectByDefaultConfig
from local_swe_controller.models import CommandSpec
from local_swe_controller.policy.schema import CompiledPolicy
from local_swe_controller.repair.patch_parser import PatchParseError, PatchParser


def test_parse_patch_extracts_change_metadata() -> None:
    patch = (
        "--- a/src/example_pkg/__init__.py\n"
        "+++ b/src/example_pkg/__init__.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-return left - right\n"
        "+return left + right\n"
    )

    parsed = PatchParser().parse(patch)

    assert parsed.changed_files == ["src/example_pkg/__init__.py"]
    assert parsed.diff_line_count == 2
    assert parsed.files[0].removed_lines == ["return left - right"]


def test_parse_patch_rejects_malformed_input() -> None:
    with pytest.raises(PatchParseError, match="unexpected content before file header"):
        PatchParser().parse("not a diff\n")


def test_patch_check_rejects_forbidden_changes(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = CompiledPolicy(
        repo_root=tmp_path,
        policy_version="0.1",
        source_files=[],
        setup_commands=[],
        format_commands=[],
        lint_commands=[],
        typecheck_commands=[],
        test_commands=[CommandSpec(command=["pytest"])],
        security_commands=[],
        hard_gates=[],
        soft_gates=[],
        forbidden_commands=[],
        forbidden_paths=[".git/", "secrets/"],
        approval_required_operations=[],
        patch_policy=_patch_policy(),
        generated_at=datetime.now(UTC),
    )
    patch = (
        "--- a/tests/test_example.py\n"
        "+++ b/tests/test_example.py\n"
        "@@ -1,2 +1,1 @@\n"
        "-assert True\n"
        "+assert False\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=1)

    assert result.accepted is False
    assert any("max diff lines" in reason for reason in result.reasons)
    assert any("removes test content" in reason for reason in result.reasons)


def test_patch_check_flags_dependency_lockfile_license_and_secrets(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path)
    patch = (
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        "@@ -1 +1,2 @@\n"
        " [project]\n"
        "+dependencies = ['requests>=2']\n"
        "--- a/uv.lock\n"
        "+++ b/uv.lock\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "--- a/LICENSE\n"
        "+++ b/LICENSE\n"
        "@@ -1 +1 @@\n"
        "-old license\n"
        "+new license\n"
        "--- a/src/auth/config.py\n"
        "+++ b/src/auth/config.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+api_key = 'secret'\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=50)

    assert result.accepted is False
    assert any("adds dependencies" in reason for reason in result.reasons)
    assert any("lockfile" in reason for reason in result.reasons)
    assert any("license-related" in reason for reason in result.reasons)
    assert any("security-sensitive path" in reason for reason in result.reasons)
    assert any("secret-looking content" in reason for reason in result.reasons)


def test_patch_check_flags_dependency_removal(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path)
    patch = (
        "--- a/requirements.txt\n"
        "+++ b/requirements.txt\n"
        "@@ -1 +0,0 @@\n"
        "-requests==2.32.0\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any("removes dependencies" in reason for reason in result.reasons)


def _policy(repo_root: Path) -> CompiledPolicy:
    return CompiledPolicy(
        repo_root=repo_root,
        policy_version="0.1",
        source_files=[],
        setup_commands=[],
        format_commands=[],
        lint_commands=[],
        typecheck_commands=[],
        test_commands=[CommandSpec(command=["pytest"])],
        security_commands=[],
        hard_gates=[],
        soft_gates=[],
        forbidden_commands=[],
        forbidden_paths=[".git/", "secrets/"],
        approval_required_operations=[],
        patch_policy=_patch_policy(),
        generated_at=datetime.now(UTC),
    )


def _patch_policy() -> PatchPolicyConfig:
    return PatchPolicyConfig(
        max_diff_lines=500,
        dependency_files=[
            "pyproject.toml",
            "requirements.txt",
            "package.json",
        ],
        lockfiles=["uv.lock"],
        license_files=["LICENSE", "REUSE.toml"],
        ci_files=[".github/workflows/"],
        test_paths=["tests/"],
        security_sensitive_paths=["src/auth/", ".secrets.baseline"],
        secret_patterns=["api_key", "BEGIN PRIVATE KEY", "ghp_"],
        reject_by_default=PatchRejectByDefaultConfig(),
    )
