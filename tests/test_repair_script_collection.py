from __future__ import annotations

import subprocess
from pathlib import Path

from local_swe_controller.models import RunStatus
from local_swe_controller.repair.controller import RepairController
from local_swe_controller.storage import ArtifactStore


def test_validate_messy_script_collection_reports_warnings_and_keeps_repo_clean(
    project_root: Path,
    temp_messy_script_repo: Path,
) -> None:
    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(),
    )
    before_status = _git_status(temp_messy_script_repo)

    report = controller.validation_runner.validate(temp_messy_script_repo)

    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert any(
        warning.startswith("Script readability:") for warning in report.warnings
    )
    assert any(
        command.spec.command == ["bash", "-n", "old_script.sh"] for command in report.commands
    )
    assert any(
        "parse(file='test.R')" in " ".join(command.spec.command)
        for command in report.commands
    )
    assert all(command.spec.command[:2] != ["bash", "old_script.sh"] for command in report.commands)
    assert before_status == _git_status(temp_messy_script_repo)


def test_repair_messy_script_collection_uses_fake_patch_and_keeps_repo_clean(
    project_root: Path,
    temp_messy_script_repo: Path,
    tmp_path: Path,
) -> None:
    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )
    before_status = _git_status(temp_messy_script_repo)

    result = controller.repair(
        repo_path=temp_messy_script_repo,
        goal="Improve readability and organization without changing behavior",
        model_profile_name="fake",
        max_iters=1,
        max_candidates=1,
    )

    assert result.status == RunStatus.SUCCESS
    assert result.patch_path is not None
    patch_text = result.patch_path.read_text(encoding="utf-8")
    assert "+++ b/README.md" in patch_text
    assert "python tmp_process.py" in patch_text
    assert "-python ghost_script.py" in patch_text
    assert result.validation_report is not None
    assert result.validation_report.status == RunStatus.NO_ACTION_NEEDED
    assert result.target_repo_changed is False
    assert before_status == _git_status(temp_messy_script_repo)
    assert not (temp_messy_script_repo / "scripts").exists()
    assert (temp_messy_script_repo / "helper copy.py").exists()


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
