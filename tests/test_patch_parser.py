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
        repo_kind="package_repo",
        policy_version="0.1",
        source_files=[],
        setup_commands=[],
        format_commands=[],
        lint_commands=[],
        typecheck_commands=[],
        build_commands=[],
        test_commands=[CommandSpec(command=["pytest"])],
        smoke_commands=[],
        e2e_commands=[],
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


def test_patch_check_allows_bounded_script_collection_rename(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path, repo_kind="script_collection")
    (tmp_path / "README.md").write_text("Use old_name.py\n", encoding="utf-8")
    patch = (
        "--- a/scripts/old_name.py\n"
        "+++ b/scripts/new_name.py\n"
        "@@ -1 +1 @@\n"
        "-print('old')\n"
        "+print('old')\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-Use old_name.py\n"
        "+Use new_name.py\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is True


def test_patch_check_allows_rename_of_filename_with_spaces(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path, repo_kind="script_collection")
    (tmp_path / "README.md").write_text("Use helper copy.py\n", encoding="utf-8")
    patch = (
        "--- a/helper copy.py\n"
        "+++ b/scripts/helper_copy.py\n"
        "@@ -1 +1 @@\n"
        "-print('old')\n"
        "+print('old')\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-Use helper copy.py\n"
        "+Use scripts/helper_copy.py\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is True


def test_patch_check_rejects_rename_for_non_script_repo(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path, repo_kind="package_repo")
    patch = (
        "--- a/scripts/old_name.py\n"
        "+++ b/scripts/new_name.py\n"
        "@@ -1 +1 @@\n"
        "-print('old')\n"
        "+print('old')\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any("renames files or folders" in reason for reason in result.reasons)


def test_patch_check_rejects_protected_script_collection_rename(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path, repo_kind="script_collection")
    patch = (
        "--- a/.github/workflows/ci.yml\n"
        "+++ b/.github/workflows/ci-renamed.yml\n"
        "@@ -1 +1 @@\n"
        "-name: ci\n"
        "+name: ci\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any("CI configuration" in reason for reason in result.reasons)


def test_patch_check_requires_readme_update_when_renaming_scripts(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path, repo_kind="script_collection")
    (tmp_path / "README.md").write_text("Use old_name.py\n", encoding="utf-8")
    patch = (
        "--- a/old_name.py\n"
        "+++ b/scripts/new_name.py\n"
        "@@ -1 +1 @@\n"
        "-print('old')\n"
        "+print('old')\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any("does not update README references" in reason for reason in result.reasons)


def test_patch_check_rejects_protected_artifact_rename(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path, repo_kind="script_collection")
    patch = (
        "--- a/data.csv\n"
        "+++ b/archive/data.csv\n"
        "@@ -1 +1 @@\n"
        "-name,value\n"
        "+name,value\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any("protected data artifacts" in reason for reason in result.reasons)


def test_patch_check_rejects_license_and_lockfile_renames(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(tmp_path, repo_kind="script_collection")
    patch = (
        "--- a/LICENSE\n"
        "+++ b/LICENSE.old\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+old\n"
        "--- a/uv.lock\n"
        "+++ b/uv.lock.old\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+old\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any("license-related" in reason for reason in result.reasons)
    assert any("lockfiles" in reason for reason in result.reasons)


def test_patch_check_rejects_explicit_argparse_help_for_implicit_workflow(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(
        tmp_path,
        repo_kind="workflow_repo",
        workflow_sources=["implicit_script_chain"],
    )
    patch = (
        "--- a/DownStream/MTL_Train.py\n"
        "+++ b/DownStream/MTL_Train.py\n"
        "@@ -1,3 +1,4 @@\n"
        " import argparse\n"
        " parser = argparse.ArgumentParser()\n"
        "+parser.add_argument(\"--help\", action=\"store_true\")\n"
        " parser.parse_args()\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any("explicit argparse --help boilerplate" in reason for reason in result.reasons)


def test_patch_check_rejects_top_level_parse_args_in_h5tools(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(
        tmp_path,
        repo_kind="workflow_repo",
        workflow_sources=["implicit_script_chain"],
    )
    patch = (
        "--- a/DataPipe/h5tools.py\n"
        "+++ b/DataPipe/h5tools.py\n"
        "@@ -1 +1,4 @@\n"
        " import argparse\n"
        "+parser = argparse.ArgumentParser()\n"
        "+parser.add_argument(\"--input\")\n"
        "+parser.parse_args()\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any("parse_args() outside an obvious main guard" in reason for reason in result.reasons)
    assert any(
        "injects CLI parsing into importable workflow module"
        in reason
        for reason in result.reasons
    )


def test_patch_check_rejects_top_level_cli_in_modelbase_module(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(
        tmp_path,
        repo_kind="workflow_repo",
        workflow_sources=["implicit_script_chain"],
    )
    patch = (
        "--- a/ModelBase/Get_ROI_model.py\n"
        "+++ b/ModelBase/Get_ROI_model.py\n"
        "@@ -1 +1,4 @@\n"
        " import argparse\n"
        "+parser = argparse.ArgumentParser()\n"
        "+parser.add_argument(\"--config\")\n"
        "+args = parser.parse_args()\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is False
    assert any(
        "injects CLI parsing into importable workflow module"
        in reason
        for reason in result.reasons
    )


def test_patch_check_rejects_broad_argparse_boilerplate_spray(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(
        tmp_path,
        repo_kind="workflow_repo",
        workflow_sources=["implicit_script_chain"],
    )
    patch = (
        "--- a/DataPipe/build_a.py\n"
        "+++ b/DataPipe/build_a.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import argparse\n"
        "+parser = argparse.ArgumentParser()\n"
        "--- a/DataPipe/build_b.py\n"
        "+++ b/DataPipe/build_b.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import argparse\n"
        "+parser = argparse.ArgumentParser()\n"
        "--- a/DownStream/train.py\n"
        "+++ b/DownStream/train.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import argparse\n"
        "+parser = argparse.ArgumentParser()\n"
        "--- a/DownStream/test.py\n"
        "+++ b/DownStream/test.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import argparse\n"
        "+parser = argparse.ArgumentParser()\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=20)

    assert result.accepted is False
    assert any(
        "sprays argparse boilerplate across too many Python files"
        in reason
        for reason in result.reasons
    )


def test_patch_check_allows_readme_only_workflow_patch(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(
        tmp_path,
        repo_kind="workflow_repo",
        workflow_sources=["implicit_script_chain"],
    )
    patch = (
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,3 @@\n"
        "-workflow\n"
        "+workflow\n"
        "+\n"
        "+Document build, train, test, and eval entrypoints.\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=10)

    assert result.accepted is True


def test_patch_check_allows_safe_argparse_cleanup_with_main_guard(tmp_path: Path) -> None:
    parser = PatchParser()
    policy = _policy(
        tmp_path,
        repo_kind="workflow_repo",
        workflow_sources=["implicit_script_chain"],
    )
    patch = (
        "--- a/DataPipe/Build_tiles_dataset.py\n"
        "+++ b/DataPipe/Build_tiles_dataset.py\n"
        "@@ -1,4 +1,8 @@\n"
        " import argparse\n"
        "+def main() -> None:\n"
        "+    parser = argparse.ArgumentParser(description=\"Prepare tile metadata.\")\n"
        "+    parser.add_argument(\"--input\")\n"
        "+    parser.parse_args()\n"
        "+if __name__ == \"__main__\":\n"
        "+    main()\n"
        " def build_parser() -> argparse.ArgumentParser:\n"
    )

    result = parser.check(parser.parse(patch), policy=policy, max_diff_lines=20)

    assert result.accepted is True


def _policy(
    repo_root: Path,
    repo_kind: str = "package_repo",
    workflow_sources: list[str] | None = None,
) -> CompiledPolicy:
    return CompiledPolicy(
        repo_root=repo_root,
        repo_kind=repo_kind,
        workflow_sources=workflow_sources or [],
        policy_version="0.1",
        source_files=[],
        setup_commands=[],
        format_commands=[],
        lint_commands=[],
        typecheck_commands=[],
        build_commands=[],
        test_commands=[CommandSpec(command=["pytest"])],
        smoke_commands=[],
        e2e_commands=[],
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
