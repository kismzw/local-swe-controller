"""Ephemeral git worktree sandbox management."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from local_swe_controller.exceptions import SandboxError


@dataclass(slots=True)
class ManagedWorktree:
    """Metadata for an ephemeral git worktree."""

    repo_root: Path
    path: Path
    keep_worktree: bool
    target_repo_dirty: bool
    target_repo_status_before: str
    warning: str | None = None
    sandbox_kind: str = "git_worktree"
    cleaned_up: bool = False


class WorktreeManager:
    """Create and clean up temporary detached git worktrees."""

    def __init__(
        self,
        *,
        temp_root: Path | None = None,
        keep_worktree: bool = False,
        prefix: str = "local-swe-",
    ) -> None:
        self.temp_root = temp_root
        self.keep_worktree = keep_worktree
        self.prefix = prefix

    def create(self, repo_path: Path) -> ManagedWorktree:
        resolved_repo = repo_path.expanduser().resolve()
        if not resolved_repo.exists():
            raise SandboxError(f"Repository path does not exist: {repo_path}")
        if not resolved_repo.is_dir():
            raise SandboxError(f"Repository path is not a directory: {repo_path}")

        repo_root = self._resolve_standalone_git_root(resolved_repo)
        if repo_root is None:
            return self._create_snapshot_sandbox(resolved_repo)

        dirty = self._is_dirty(repo_root)
        status_before = self._status(repo_root)
        worktree_path = Path(
            tempfile.mkdtemp(
                prefix=self.prefix,
                dir=str(self.temp_root) if self.temp_root else None,
            )
        ).resolve()

        try:
            self._run_git(
                repo_root,
                ["worktree", "add", "--detach", str(worktree_path), "HEAD"],
            )
        except Exception:
            shutil.rmtree(worktree_path, ignore_errors=True)
            raise

        warning = None
        if dirty:
            warning = (
                "Target repository has uncommitted changes; validation uses HEAD only and "
                "does not copy dirty changes by default."
            )

        return ManagedWorktree(
            repo_root=repo_root,
            path=worktree_path,
            keep_worktree=self.keep_worktree,
            target_repo_dirty=dirty,
            target_repo_status_before=status_before,
            warning=warning,
            sandbox_kind="git_worktree",
        )

    def cleanup(self, managed: ManagedWorktree) -> None:
        if managed.cleaned_up or managed.keep_worktree:
            return

        errors: list[str] = []
        if managed.sandbox_kind == "git_worktree":
            try:
                self._run_git(
                    managed.repo_root,
                    ["worktree", "remove", "--force", str(managed.path)],
                )
            except SandboxError as exc:
                errors.append(str(exc))

        shutil.rmtree(managed.path, ignore_errors=True)
        managed.cleaned_up = True

        if errors:
            raise SandboxError("; ".join(errors))

    def session(self, repo_path: Path) -> _WorktreeSession:
        return _WorktreeSession(self, repo_path)

    def _resolve_standalone_git_root(self, resolved: Path) -> Path | None:
        result = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
        if result.returncode != 0:
            return None
        repo_root = Path(result.stdout.strip()).resolve()
        if repo_root != resolved:
            return None
        return repo_root

    def _is_dirty(self, repo_root: Path) -> bool:
        return bool(self._status(repo_root))

    def status(self, repo_root: Path) -> str:
        return self._status(repo_root)

    def _status(self, repo_root: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
        if result.returncode != 0:
            raise SandboxError(f"Unable to inspect git status for repository: {repo_root}")
        return result.stdout.strip()

    def _run_git(self, repo_root: Path, args: list[str]) -> None:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise SandboxError(message)

    def _create_snapshot_sandbox(self, repo_path: Path) -> ManagedWorktree:
        worktree_path = Path(
            tempfile.mkdtemp(
                prefix=self.prefix,
                dir=str(self.temp_root) if self.temp_root else None,
            )
        ).resolve()
        shutil.rmtree(worktree_path, ignore_errors=True)
        shutil.copytree(repo_path, worktree_path)
        self._initialize_snapshot_git_repo(worktree_path)
        return ManagedWorktree(
            repo_root=repo_path,
            path=worktree_path,
            keep_worktree=self.keep_worktree,
            target_repo_dirty=False,
            target_repo_status_before="",
            warning=(
                "Target path is not a standalone git repository; using a temporary snapshot "
                "sandbox rooted at the requested directory."
            ),
            sandbox_kind="snapshot",
        )

    def _initialize_snapshot_git_repo(self, path: Path) -> None:
        for args in (
            ["git", "init", "-b", "main", str(path)],
            ["git", "-C", str(path), "config", "user.name", "Local SWE Snapshot"],
            ["git", "-C", str(path), "config", "user.email", "local-swe-snapshot@example.com"],
            ["git", "-C", str(path), "add", "."],
            ["git", "-C", str(path), "commit", "--allow-empty", "-m", "Snapshot"],
        ):
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                shell=False,
                check=False,
            )
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or "git command failed"
                raise SandboxError(message)


class _WorktreeSession:
    def __init__(self, manager: WorktreeManager, repo_path: Path) -> None:
        self.manager = manager
        self.repo_path = repo_path
        self.managed: ManagedWorktree | None = None

    def __enter__(self) -> ManagedWorktree:
        self.managed = self.manager.create(self.repo_path)
        return self.managed

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self.managed is not None:
            self.manager.cleanup(self.managed)
        return False
