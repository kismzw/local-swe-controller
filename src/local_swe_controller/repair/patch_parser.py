"""Unified diff parsing and deterministic patch safety checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from local_swe_controller.policy.schema import CompiledPolicy


class PatchParseError(ValueError):
    """Raised when a candidate patch is malformed or unsafe."""


@dataclass(slots=True)
class PatchFileChange:
    """Parsed change summary for one file in a unified diff."""

    old_path: str
    new_path: str
    added_lines: list[str]
    removed_lines: list[str]
    hunk_count: int
    is_new_file: bool = False
    is_deleted_file: bool = False

    @property
    def changed_path(self) -> str:
        if self.new_path != "/dev/null":
            return self.new_path.removeprefix("b/")
        return self.old_path.removeprefix("a/")


@dataclass(slots=True)
class ParsedPatch:
    """Parsed patch metadata."""

    text: str
    files: list[PatchFileChange]
    diff_line_count: int

    @property
    def changed_files(self) -> list[str]:
        return [item.changed_path for item in self.files]


@dataclass(slots=True)
class PatchCheckResult:
    """Result of static patch validation."""

    accepted: bool
    reasons: list[str]


class PatchParser:
    """Parse and safety-check unified diffs."""

    def parse(self, patch_text: str) -> ParsedPatch:
        lines = patch_text.splitlines()
        if not lines:
            raise PatchParseError("Patch is empty.")

        files: list[PatchFileChange] = []
        current: PatchFileChange | None = None
        state = "expect_old"
        diff_line_count = 0

        for line in lines:
            if line.startswith("--- "):
                if current is not None:
                    files.append(current)
                current = PatchFileChange(
                    old_path=line[4:].strip(),
                    new_path="",
                    added_lines=[],
                    removed_lines=[],
                    hunk_count=0,
                    is_deleted_file=line[4:].strip() != "/dev/null",
                )
                state = "expect_new"
                continue
            if line.startswith("+++ "):
                if current is None or state != "expect_new":
                    raise PatchParseError("Malformed patch: unexpected new-file header.")
                current.new_path = line[4:].strip()
                current.is_new_file = current.old_path == "/dev/null"
                current.is_deleted_file = current.new_path == "/dev/null"
                self._validate_path(current.changed_path)
                state = "body"
                continue
            if line.startswith("@@ "):
                if current is None or state != "body":
                    raise PatchParseError("Malformed patch: hunk without file header.")
                current.hunk_count += 1
                continue
            if line.startswith("+") and not line.startswith("+++"):
                if current is None or current.hunk_count == 0:
                    raise PatchParseError("Malformed patch: added line outside hunk.")
                current.added_lines.append(line[1:])
                diff_line_count += 1
                continue
            if line.startswith("-") and not line.startswith("---"):
                if current is None or current.hunk_count == 0:
                    raise PatchParseError("Malformed patch: removed line outside hunk.")
                current.removed_lines.append(line[1:])
                diff_line_count += 1
                continue
            if line.startswith((" ", "\\ No newline at end of file", "diff --git", "index ")):
                continue
            if current is None:
                raise PatchParseError("Malformed patch: unexpected content before file header.")
            if current.hunk_count == 0:
                raise PatchParseError("Malformed patch: file header missing hunk body.")

        if current is not None:
            files.append(current)

        if not files:
            raise PatchParseError("Patch does not contain any file changes.")
        if any(file_change.hunk_count == 0 for file_change in files):
            raise PatchParseError("Patch contains a file without any hunks.")

        return ParsedPatch(text=patch_text, files=files, diff_line_count=diff_line_count)

    def check(
        self,
        parsed_patch: ParsedPatch,
        *,
        policy: CompiledPolicy,
        max_diff_lines: int,
    ) -> PatchCheckResult:
        reasons: list[str] = []
        if parsed_patch.diff_line_count > max_diff_lines:
            reasons.append(
                f"Patch exceeds max diff lines ({parsed_patch.diff_line_count} > {max_diff_lines})."
            )

        for file_change in parsed_patch.files:
            changed_path = file_change.changed_path
            if self._matches_forbidden_path(changed_path, policy.forbidden_paths):
                reasons.append(f"Patch touches forbidden path: {changed_path}")
            if changed_path.startswith(".local-swe/policies/") or changed_path.startswith(
                ".local-swe/policy"
            ):
                reasons.append(f"Patch modifies controller policy artifact path: {changed_path}")
            if self._is_dependency_file(changed_path, policy):
                reasons.extend(self._dependency_change_reasons(file_change))
            if self._is_lockfile(changed_path, policy):
                reasons.append(f"Patch changes lockfile and requires approval: {changed_path}")
            if self._is_license_file(changed_path, policy):
                reasons.append(
                    f"Patch changes license-related files and requires approval: {changed_path}"
                )
            if self._is_ci_file(changed_path, policy):
                reasons.append(f"Patch modifies CI configuration: {changed_path}")
            if self._is_security_sensitive_path(changed_path, policy):
                reasons.append(
                    f"Patch changes a security-sensitive path and requires approval: {changed_path}"
                )
            if self._is_test_path(changed_path) and any(
                line.strip() for line in file_change.removed_lines
            ):
                reasons.append(f"Patch removes test content: {changed_path}")
            if self._contains_secret_like_content(file_change, policy):
                reasons.append(
                    f"Patch adds secret-looking content and is rejected: {changed_path}"
                )

        return PatchCheckResult(accepted=not reasons, reasons=reasons)

    def _validate_path(self, path_text: str) -> None:
        path = Path(path_text)
        if path.is_absolute():
            raise PatchParseError(f"Patch path must be relative: {path_text}")
        if ".." in path.parts:
            raise PatchParseError(f"Patch path must not escape the repository: {path_text}")

    def _matches_forbidden_path(self, changed_path: str, forbidden_paths: list[str]) -> bool:
        return any(
            changed_path == item.rstrip("/") or changed_path.startswith(item.rstrip("/") + "/")
            for item in forbidden_paths
        )

    def _is_dependency_file(self, changed_path: str, policy: CompiledPolicy) -> bool:
        name = Path(changed_path).name
        configured = {Path(item).name for item in policy.patch_policy.dependency_files}
        return name in configured or name.startswith("requirements")

    def _is_lockfile(self, changed_path: str, policy: CompiledPolicy) -> bool:
        configured = {Path(item).name for item in policy.patch_policy.lockfiles}
        return Path(changed_path).name in configured

    def _is_license_file(self, changed_path: str, policy: CompiledPolicy) -> bool:
        configured = {Path(item).name.upper() for item in policy.patch_policy.license_files}
        upper_name = Path(changed_path).name.upper()
        return upper_name in configured or upper_name.startswith(("LICENSE", "COPYING", "NOTICE"))

    def _is_ci_file(self, changed_path: str, policy: CompiledPolicy) -> bool:
        return self._matches_configured_path(changed_path, policy.patch_policy.ci_files)

    def _is_test_path(self, changed_path: str) -> bool:
        path = Path(changed_path)
        return "tests" in path.parts or path.name.startswith("test_")

    def _is_security_sensitive_path(self, changed_path: str, policy: CompiledPolicy) -> bool:
        return self._matches_configured_path(
            changed_path,
            policy.patch_policy.security_sensitive_paths,
        )

    def _matches_configured_path(self, changed_path: str, configured_paths: list[str]) -> bool:
        return any(
            changed_path == item.rstrip("/") or changed_path.startswith(item.rstrip("/") + "/")
            for item in configured_paths
        )

    def _dependency_change_reasons(self, file_change: PatchFileChange) -> list[str]:
        changed_path = file_change.changed_path
        reasons: list[str] = []
        if any(self._is_meaningful_dependency_line(line) for line in file_change.added_lines):
            reasons.append(f"Patch adds dependencies and requires approval: {changed_path}")
        if any(self._is_meaningful_dependency_line(line) for line in file_change.removed_lines):
            reasons.append(f"Patch removes dependencies and requires approval: {changed_path}")
        if not reasons:
            reasons.append(
                f"Patch modifies dependency manifest and requires approval: {changed_path}"
            )
        return reasons

    def _is_meaningful_dependency_line(self, line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and not stripped.startswith(("#", "[", "]", "{", "}"))

    def _contains_secret_like_content(
        self,
        file_change: PatchFileChange,
        policy: CompiledPolicy,
    ) -> bool:
        added_text = "\n".join(file_change.added_lines).casefold()
        return any(
            pattern.casefold() in added_text for pattern in policy.patch_policy.secret_patterns
        )
