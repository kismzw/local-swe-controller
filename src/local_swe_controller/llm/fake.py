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
        raise LLMError("Fake model could not derive a deterministic patch from the prompt.")
