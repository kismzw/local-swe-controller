"""Deterministic fake model client for tests."""

from __future__ import annotations

from local_swe_controller.llm.client import LLMClient, LLMError, ModelProfile


class FakeLLMClient(LLMClient):
    """Return a deterministic unified diff based on prompt content."""

    def __init__(self, profile_name: str, profile: ModelProfile) -> None:
        super().__init__(profile_name, profile)

    def generate_patch(self, *, system_prompt: str, user_prompt: str) -> str:
        del system_prompt
        if (
            "containing only regression tests" in user_prompt
            and "src/example_pkg/__init__.py" in user_prompt
        ):
            return (
                "--- /dev/null\n"
                "+++ b/tests/test_regression.py\n"
                "@@ -0,0 +1,5 @@\n"
                "+from example_pkg import add\n"
                "+\n"
                "+\n"
                "+def test_add_handles_negative_right_operand() -> None:\n"
                "+    assert add(4, -1) == 3\n"
            )
        if "return left - right" in user_prompt and "src/example_pkg/__init__.py" in user_prompt:
            return (
                "--- a/src/example_pkg/__init__.py\n"
                "+++ b/src/example_pkg/__init__.py\n"
                "@@ -2,4 +2,4 @@\n"
                " \n"
                " \n"
                " def add(left: int, right: int) -> int:\n"
                "-    return left - right\n"
                "+    return left + right\n"
            )
        if "helper copy.py" in user_prompt and "run1.py" in user_prompt:
            return (
                "diff --git a/README.md b/README.md\n"
                "index 1111111..2222222 100644\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1,15 +1,25 @@\n"
                " # Messy Script Collection\n"
                " \n"
                "+This repo keeps small local scripts without turning them into a package.\n"
                "+\n"
                "+Scripts in use:\n"
                "+- `run1.py`: normalize one value from the command line.\n"
                "+- `tmp_process.py`: preview the shared normalization logic.\n"
                "+- `final2.py`: emit a tiny normalized report label.\n"
                "+- `helper copy.py`: awkward legacy helper filename kept for reference.\n"
                "+- `old_script.sh`: legacy shell helper checked with `bash -n`.\n"
                "+- `test.R`: tiny R parse example.\n"
                "+\n"
                " Run the main example with:\n"
                " \n"
                " ```bash\n"
                " python run1.py\n"
                " ```\n"
                " \n"
                "-Legacy note:\n"
                "+Preview the secondary processing script:\n"
                " \n"
                " ```bash\n"
                "-python ghost_script.py\n"
                "+python tmp_process.py\n"
                " ```\n"
                " \n"
                " The shell helper is `old_script.sh`.\n"
            )
        if "Build_tiles_dataset.py" in user_prompt and "MultiRegression_Train.py" in user_prompt:
            return (
                "diff --git a/README.md b/README.md\n"
                "index 1111111..2222222 100644\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -4,6 +4,14 @@\n"
                " \n"
                " 1. Build dataset artifacts in `DataPipe/`\n"
                " 2. Train or test downstream models in `DownStream/`\n"
                "+3. Use `configs/example.yaml` for documented example inputs.\n"
                "+\n"
                "+Recommended smoke commands:\n"
                "+- `python DataPipe/Build_tiles_dataset.py --help`\n"
                "+- `python DownStream/ROI_MultiRegression/MultiRegression_Train.py --help`\n"
                "+- `python DownStream/ROI_MultiRegression/MultiRegression_Test.py --help`\n"
                "+\n"
                "+The `Build_embedded_dataset.py` and `MTL_Train.py` scripts still need explicit "
                "+help paths.\n"
                " \n"
                " ```bash\n"
                " python DataPipe/Build_tiles_dataset.py --help\n"
                " python DownStream/ROI_MultiRegression/MultiRegression_Train.py --help\n"
            )
        raise LLMError("Fake model could not derive a deterministic patch from the prompt.")
