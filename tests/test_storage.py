from __future__ import annotations

import threading
from pathlib import Path

from local_swe_controller.models import RunStatus
from local_swe_controller.storage import ArtifactStore, default_artifact_root


def test_artifact_store_indexes_runs(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / ".local-swe")
    context = store.create_run(run_type="repair", repo_root=tmp_path / "repo", goal="Fix tests")
    store.append_trace(
        context,
        "run_started",
        {
            "repo_root": str(tmp_path / "repo"),
            "goal": "Fix tests",
            "command_type": "repair",
            "generate_tests": False,
        },
    )
    store.write_summary(context, "# summary\n")
    store.finalize_run(context, status=RunStatus.SUCCESS, summary="ok")

    records = store.list_runs()

    assert len(records) == 1
    assert records[0].run_id == context.run_id
    assert store.get_run(context.run_id) is not None
    assert records[0].summary_path == context.summary_path


def test_list_runs_hides_in_progress_placeholder_rows(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / ".local-swe")
    context = store.create_run(run_type="validate", repo_root=tmp_path / "repo")

    assert store.list_runs() == []
    record = store.get_run(context.run_id)
    assert record is not None
    assert record.status == RunStatus.ERROR
    assert record.finished_at is None


def test_concurrent_finalize_persists_only_real_final_statuses(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / ".local-swe")
    repo_root = tmp_path / "repo"
    barrier = threading.Barrier(3)
    contexts = [
        store.create_run(run_type="validate", repo_root=repo_root),
        store.create_run(run_type="repair", repo_root=repo_root, goal="Fix tests"),
    ]

    def finalize(context, status: RunStatus, summary: str) -> None:
        barrier.wait()
        store.write_summary(context, f"# {summary}\n")
        store.finalize_run(context, status=status, summary=summary)
        barrier.wait()

    first = threading.Thread(
        target=finalize,
        args=(contexts[0], RunStatus.NO_ACTION_NEEDED, "validate-ok"),
    )
    second = threading.Thread(
        target=finalize,
        args=(contexts[1], RunStatus.SUCCESS, "repair-ok"),
    )
    first.start()
    second.start()
    barrier.wait()

    assert store.list_runs() == []

    barrier.wait()
    first.join()
    second.join()

    records = store.list_runs()
    assert len(records) == 2
    statuses = {record.run_id: record.status for record in records}
    assert statuses[contexts[0].run_id] == RunStatus.NO_ACTION_NEEDED
    assert statuses[contexts[1].run_id] == RunStatus.SUCCESS
    assert all(record.finished_at is not None for record in records)


def test_default_artifact_root_uses_home_when_env_unset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.delenv("LOCAL_SWE_ARTIFACT_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(fake_home))

    assert default_artifact_root() == fake_home / ".local-swe"


def test_default_artifact_root_falls_back_to_tmp_when_home_is_unwritable(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LOCAL_SWE_ARTIFACT_ROOT", raising=False)
    monkeypatch.setenv("HOME", "/proc/local-swe-home")

    assert default_artifact_root().name == "local-swe"
    assert str(default_artifact_root()).startswith("/tmp/")
