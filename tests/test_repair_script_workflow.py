from __future__ import annotations

import subprocess
from pathlib import Path

from local_swe_controller.models import RunStatus
from local_swe_controller.repair.controller import RepairController
from local_swe_controller.storage import ArtifactStore


def test_repair_script_workflow_fixture_is_actionable_and_keeps_repo_clean(
    project_root: Path,
    temp_script_workflow_repo: Path,
    tmp_path: Path,
) -> None:
    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )
    before_status = _git_status(temp_script_workflow_repo)

    result = controller.repair(
        repo_path=temp_script_workflow_repo,
        goal="Improve workflow readability and smoke-testability without changing behavior",
        model_profile_name="fake",
        max_iters=1,
        max_candidates=1,
    )

    assert result.status == RunStatus.SUCCESS
    assert result.patch_path is not None
    patch_text = result.patch_path.read_text(encoding="utf-8")
    assert "+++ b/README.md" in patch_text
    assert "configs/example.yaml" in patch_text
    assert before_status == _git_status(temp_script_workflow_repo)


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
