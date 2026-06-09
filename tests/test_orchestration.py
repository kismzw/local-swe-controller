from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

import local_swe_controller
from local_swe_controller.exceptions import OrchestrationError
from local_swe_controller.models import RunStatus, ValidationReport
from local_swe_controller.orchestration.langgraph_adapter import LangGraphRepairAdapter
from local_swe_controller.orchestration.models import (
    GraphExecutionStatus,
    RepairGraphNode,
    RepairGraphResult,
    RepairGraphState,
)
from local_swe_controller.repair.controller import RepairController, RepairResult
from local_swe_controller.storage import ArtifactStore


class _RecordingClient:
    def __init__(self, patches: list[str]) -> None:
        self.patches = patches
        self.calls = 0

    def generate_patch(self, *, system_prompt: str, user_prompt: str) -> str:
        del system_prompt, user_prompt
        patch = self.patches[min(self.calls, len(self.patches) - 1)]
        self.calls += 1
        return patch


def _langgraph_installed() -> bool:
    try:
        importlib.import_module("langgraph.graph")
    except ImportError:
        return False
    return True


def test_package_imports_without_langgraph_installed() -> None:
    assert local_swe_controller.__version__ == "0.1.0"


def test_adapter_error_is_clear_when_langgraph_missing(
    monkeypatch,
    project_root: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(LangGraphRepairAdapter, "is_available", staticmethod(lambda: False))
    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )

    with pytest.raises(OrchestrationError, match="LangGraph orchestrator requested"):
        LangGraphRepairAdapter(controller).require_available()


def test_state_models_validate() -> None:
    state = RepairGraphState(repo_path=Path("/tmp/repo"), goal="Fix tests")

    assert state.execution_status == GraphExecutionStatus.PENDING
    assert state.current_node == RepairGraphNode.INTAKE
    assert RepairGraphState.deserialize(state.serialize()) == state


def test_deterministic_cli_option_keeps_existing_behavior(temp_fixture_repo: Path) -> None:
    before_status = _git_status(temp_fixture_repo)
    result = subprocess.run(
        [
            str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "local-swe"),
            "repair",
            "--repo",
            str(temp_fixture_repo),
            "--goal",
            "Fix failing tests",
            "--model-profile",
            "fake",
            "--orchestrator",
            "deterministic",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    after_status = _git_status(temp_fixture_repo)

    assert "Repair status: NO_ACTION_NEEDED" in result.stdout
    assert before_status == after_status


@pytest.mark.skipif(not _langgraph_installed(), reason="LangGraph is not installed.")
def test_langgraph_orchestrator_runs_with_fake_model(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )
    adapter = LangGraphRepairAdapter(controller)

    result = adapter.execute(
        RepairGraphState(
            repo_path=temp_fixture_repo,
            goal="Fix failing tests",
            model_profile_name="fake",
            max_iters=1,
        )
    )

    assert result.status.value in {"NO_ACTION_NEEDED", "SUCCESS"}
    assert _git_status(temp_fixture_repo) == ""


@pytest.mark.skipif(not _langgraph_installed(), reason="LangGraph is not installed.")
def test_langgraph_adapter_invokes_graph_with_thread_id(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )
    adapter = LangGraphRepairAdapter(controller)
    captured: dict[str, object] = {}

    class StubGraph:
        def invoke(self, payload, config=None):
            captured["payload"] = payload
            captured["config"] = config
            state = RepairGraphState.model_validate(payload)
            run = controller.artifact_store.create_run(
                run_type="repair",
                repo_root=temp_fixture_repo,
                goal=state.goal,
            )
            baseline_report = ValidationReport(
                repo_root=temp_fixture_repo,
                status=RunStatus.NO_ACTION_NEEDED,
            )
            repair_result = RepairResult(
                run_id=run.run_id,
                repo_root=temp_fixture_repo,
                goal=state.goal,
                status=RunStatus.NO_ACTION_NEEDED,
                summary="stubbed",
                model_profile="fake",
                artifact_dir=run.artifact_dir,
                policy_path=run.policy_path,
                trace_path=run.trace_path,
                baseline_report=baseline_report,
            )
            state.result = RepairGraphResult(
                status=GraphExecutionStatus.COMPLETED,
                run_status=repair_result.status,
                stop_reason=repair_result.stop_reason,
                repair_result=repair_result,
            )
            return state.checkpoint_payload()

    monkeypatch.setattr(adapter, "build_repair_graph", lambda: StubGraph())

    result = adapter.execute(
        RepairGraphState(
            repo_path=temp_fixture_repo,
            goal="Fix failing tests",
            model_profile_name="fake",
        )
    )

    assert result.summary == "stubbed"
    assert captured["config"] is not None
    assert captured["config"]["configurable"]["thread_id"]


@pytest.mark.skipif(not _langgraph_installed(), reason="LangGraph is not installed.")
def test_langgraph_bounded_retry_respected(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("langgraph-bounded")
    (repo / "README.md").write_text("example\n", encoding="utf-8")
    (repo / "Makefile").write_text(
        "test:\n\tpython -c \"import sys; print('FAILED still broken'); sys.exit(1)\"\n",
        encoding="utf-8",
    )
    init_git_repo(repo)
    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )
    monkeypatch.setattr(
        controller,
        "_build_client",
        lambda *_args, **_kwargs: _RecordingClient(
            [_readme_patch("same"), _readme_patch("same")]
        ),
    )
    adapter = LangGraphRepairAdapter(controller)

    result = adapter.execute(
        RepairGraphState(
            repo_path=repo,
            goal="Fix tests",
            model_profile_name="fake",
            max_iters=1,
            max_candidates=1,
        )
    )

    assert result.status in {result.status.STOPPED_BY_BUDGET, result.status.PATCH_REJECTED}
    assert _git_status(repo) == ""


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _readme_patch(text: str) -> str:
    return (
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-example\n"
        f"+{text}\n"
    )
