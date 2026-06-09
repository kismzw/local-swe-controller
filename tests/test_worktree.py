from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_swe_controller.sandbox.worktree import WorktreeManager


def test_worktree_creation(
    git_repo_factory,
    init_git_repo,
) -> None:
    repo = git_repo_factory("repo")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    init_git_repo(repo)

    manager = WorktreeManager()
    managed = manager.create(repo)

    try:
        assert managed.path.exists()
        assert (managed.path / "README.md").read_text(encoding="utf-8") == "hello\n"
        assert managed.repo_root == repo.resolve()
    finally:
        manager.cleanup(managed)


def test_cleanup(git_repo_factory, init_git_repo) -> None:
    repo = git_repo_factory("repo")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    init_git_repo(repo)

    manager = WorktreeManager()
    managed = manager.create(repo)
    worktree_path = managed.path
    manager.cleanup(managed)

    assert not worktree_path.exists()


def test_keep_worktree(git_repo_factory, init_git_repo) -> None:
    repo = git_repo_factory("repo")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    init_git_repo(repo)

    manager = WorktreeManager(keep_worktree=True)
    managed = manager.create(repo)
    manager.cleanup(managed)

    assert managed.path.exists()

    subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(managed.path)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_non_git_directory_uses_snapshot_sandbox(tmp_path: Path) -> None:
    repo = tmp_path / "not_git"
    repo.mkdir()
    (repo / "README.md").write_text("snapshot\n", encoding="utf-8")

    managed = WorktreeManager().create(repo)

    try:
        assert managed.sandbox_kind == "snapshot"
        assert (managed.path / "README.md").read_text(encoding="utf-8") == "snapshot\n"
    finally:
        WorktreeManager().cleanup(managed)


def test_cleanup_after_exception(git_repo_factory, init_git_repo) -> None:
    repo = git_repo_factory("repo")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    init_git_repo(repo)

    manager = WorktreeManager()
    with pytest.raises(RuntimeError, match="boom"):
        with manager.session(repo) as managed:
            path = managed.path
            assert path.exists()
            raise RuntimeError("boom")

    assert not path.exists()


def test_target_repo_unchanged(git_repo_factory, init_git_repo) -> None:
    repo = git_repo_factory("repo")
    readme = repo / "README.md"
    readme.write_text("hello\n", encoding="utf-8")
    init_git_repo(repo)
    before_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    manager = WorktreeManager()
    with manager.session(repo) as managed:
        (managed.path / "README.md").write_text("changed in worktree\n", encoding="utf-8")

    after_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert readme.read_text(encoding="utf-8") == "hello\n"
    assert before_status == after_status
