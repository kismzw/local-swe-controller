import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture()
def fixture_repo_path() -> Path:
    return PROJECT_ROOT / "examples" / "fixture_python_repo"


@pytest.fixture()
def messy_script_fixture_repo_path() -> Path:
    return PROJECT_ROOT / "examples" / "fixture_messy_script_collection_repo"


@pytest.fixture()
def script_workflow_fixture_repo_path() -> Path:
    return PROJECT_ROOT / "examples" / "fixture_script_workflow_repo"


@pytest.fixture()
def script_workflow_priority_fixture_repo_path() -> Path:
    return PROJECT_ROOT / "examples" / "fixture_script_workflow_priority_repo"


@pytest.fixture(autouse=True)
def artifact_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("LOCAL_SWE_ARTIFACT_ROOT", str(root))
    return root


@pytest.fixture()
def temp_fixture_repo(tmp_path: Path, fixture_repo_path: Path) -> Path:
    destination = tmp_path / "fixture_python_repo"
    shutil.copytree(
        fixture_repo_path,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".local-swe",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
        ),
    )
    _init_git_repo(destination)
    return destination


@pytest.fixture()
def temp_messy_script_repo(tmp_path: Path, messy_script_fixture_repo_path: Path) -> Path:
    destination = tmp_path / "fixture_messy_script_collection_repo"
    shutil.copytree(
        messy_script_fixture_repo_path,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".local-swe",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
        ),
    )
    _init_git_repo(destination)
    return destination


@pytest.fixture()
def temp_script_workflow_repo(tmp_path: Path, script_workflow_fixture_repo_path: Path) -> Path:
    destination = tmp_path / "fixture_script_workflow_repo"
    shutil.copytree(
        script_workflow_fixture_repo_path,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".local-swe",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
        ),
    )
    _init_git_repo(destination)
    return destination


@pytest.fixture()
def temp_script_workflow_priority_repo(
    tmp_path: Path,
    script_workflow_priority_fixture_repo_path: Path,
) -> Path:
    destination = tmp_path / "fixture_script_workflow_priority_repo"
    shutil.copytree(
        script_workflow_priority_fixture_repo_path,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".local-swe",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
        ),
    )
    _init_git_repo(destination)
    return destination


def _init_git_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Local SWE Tests"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "local-swe-tests@example.com"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "add", "."],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def git_repo_factory(tmp_path: Path) -> Callable[[str], Path]:
    def factory(name: str) -> Path:
        repo = tmp_path / name
        repo.mkdir()
        return repo

    return factory


@pytest.fixture()
def init_git_repo() -> Callable[[Path], None]:
    return _init_git_repo
