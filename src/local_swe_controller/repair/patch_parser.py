"""Unified diff parsing and deterministic patch safety checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from local_swe_controller.policy.schema import CompiledPolicy

_PROTECTED_ARTIFACT_DIRS = {"data", "outputs", "artifacts", "checkpoints", "logs"}
_PROTECTED_ARTIFACT_SUFFIXES = {
    ".csv",
    ".tsv",
    ".jsonl",
    ".parquet",
    ".zarr",
    ".feather",
    ".arrow",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".npy",
    ".npz",
    ".h5",
    ".hdf5",
    ".pt",
    ".pth",
    ".ckpt",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".tif",
    ".svs",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
}
_ARGPARSE_HELP_PATTERN = re.compile(r"""add_argument\(\s*['"]--help['"]""")
_PARSE_ARGS_PATTERN = re.compile(r"\bparse_args\s*\(")
_ARGPARSE_SIGNAL_PATTERN = re.compile(r"\b(argparse|ArgumentParser|add_argument|parse_args)\b")


class PatchParseError(ValueError):
    """Raised when a candidate patch is malformed or unsafe."""

_FENCED_PATCH_PATTERN = re.compile(
    r"```(?:diff|patch)?\s*\n(?P<body>.*?)\n```",
    flags=re.DOTALL | re.IGNORECASE,
)
_DIFF_GIT_PATTERN = re.compile(r"^diff --git a/.+ b/.+$")
_OLD_FILE_PATTERN = re.compile(r"^--- (?:a/.+|/dev/null)$")
_NEW_FILE_PATTERN = re.compile(r"^\+\+\+ (?:b/.+|/dev/null)$")
_PATCH_META_PREFIXES = (
    "index ",
    "new file mode ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
)
_HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<suffix>.*)$"
)


def extract_patch_block(response_text: str) -> str:
    """Extract a valid patch block from a possibly wrapped LLM response."""

    text = response_text.strip()
    if not text:
        raise PatchParseError("Patch is empty.")

    apply_patch_block = _extract_apply_patch_block(text)
    if apply_patch_block is not None:
        return apply_patch_block

    # Prefer fenced patch/diff blocks if they contain a real patch header.
    for match in _FENCED_PATCH_PATTERN.finditer(text):
        body = match.group("body").strip()
        if _looks_like_patch(body):
            return body

    diff_git_block = _extract_unified_diff_block(text.splitlines(), prefer_diff_git=True)
    if diff_git_block is not None:
        return diff_git_block
    unified_block = _extract_unified_diff_block(text.splitlines(), prefer_diff_git=False)
    if unified_block is not None:
        return unified_block

    raise PatchParseError("No valid patch block found in model response.")


def normalize_patch_block(patch_text: str) -> str:
    """Normalize extracted patches into a stricter format.

    Currently this repairs unified-diff hunk line counts using the actual hunk body so
    common local-model count mistakes still produce an applicable patch artifact.
    """

    text = patch_text.strip()
    if not text or text.startswith("*** Begin Patch"):
        return text

    lines = text.splitlines()
    normalized: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _HUNK_HEADER_PATTERN.match(line)
        if match is None:
            normalized.append(line)
            index += 1
            continue

        hunk_lines: list[str] = []
        index += 1
        while index < len(lines):
            next_line = lines[index]
            if _HUNK_HEADER_PATTERN.match(next_line):
                break
            if next_line.startswith(("--- ", "diff --git ")):
                break
            hunk_lines.append(next_line)
            index += 1

        old_count = sum(1 for item in hunk_lines if item.startswith((" ", "-")))
        new_count = sum(1 for item in hunk_lines if item.startswith((" ", "+")))
        suffix = match.group("suffix") or ""
        normalized.append(
            "@@ "
            f"-{match.group('old_start')},{old_count} "
            f"+{match.group('new_start')},{new_count} "
            f"@@{suffix}"
        )
        normalized.extend(hunk_lines)

    return "\n".join(normalized)


def _looks_like_patch(text: str) -> bool:
    lines = text.lstrip().splitlines()
    if not lines:
        return False
    if lines[0].startswith("*** Begin Patch"):
        return "*** End Patch" in text
    if lines[0].startswith("diff --git "):
        return True
    return len(lines) >= 2 and lines[0].startswith("--- ") and lines[1].startswith("+++ ")


def _extract_apply_patch_block(text: str) -> str | None:
    begin = text.find("*** Begin Patch")
    end = text.find("*** End Patch")
    if begin == -1 or end == -1 or end <= begin:
        return None
    return text[begin : end + len("*** End Patch")].strip()


def _extract_unified_diff_block(lines: list[str], *, prefer_diff_git: bool) -> str | None:
    start_indexes: list[int] = []
    if prefer_diff_git:
        start_indexes = [index for index, line in enumerate(lines) if line.startswith("diff --git ")]
    else:
        for index in range(len(lines) - 1):
            if _OLD_FILE_PATTERN.match(lines[index]) and _NEW_FILE_PATTERN.match(lines[index + 1]):
                start_indexes.append(index)
    for start in start_indexes:
        extracted = _slice_patch_lines(lines, start)
        if extracted is not None and _looks_like_patch(extracted):
            return extracted
    return None


def _slice_patch_lines(lines: list[str], start: int) -> str | None:
    collected: list[str] = []
    seen_file = False
    seen_hunk = False
    expect_new = False
    in_hunk = False

    for line in lines[start:]:
        if not collected and not _is_patch_start_line(line):
            return None
        if line.startswith("diff --git "):
            if collected and seen_file and not expect_new:
                next_line = line
                collected.append(next_line)
                seen_file = False
                seen_hunk = False
                expect_new = False
                in_hunk = False
                continue
            collected.append(line)
            continue
        if line.startswith(_PATCH_META_PREFIXES):
            if not collected:
                return None
            collected.append(line)
            continue
        if _OLD_FILE_PATTERN.match(line):
            if seen_hunk and not expect_new:
                seen_file = True
                seen_hunk = False
                expect_new = True
                in_hunk = False
                collected.append(line)
                continue
            seen_file = True
            expect_new = True
            in_hunk = False
            collected.append(line)
            continue
        if _NEW_FILE_PATTERN.match(line):
            if not expect_new:
                break
            expect_new = False
            collected.append(line)
            continue
        if line.startswith("@@ "):
            if expect_new or not seen_file:
                break
            seen_hunk = True
            in_hunk = True
            collected.append(line)
            continue
        if _is_hunk_line(line):
            if not in_hunk:
                break
            collected.append(line)
            continue
        if line == "" and in_hunk:
            break
        if seen_hunk:
            break
        if collected:
            break
    if not seen_file or expect_new or not seen_hunk:
        return None
    return "\n".join(collected).strip()


def _is_patch_start_line(line: str) -> bool:
    return line.startswith("diff --git ") or _OLD_FILE_PATTERN.match(line) is not None


def _is_hunk_line(line: str) -> bool:
    return (
        line.startswith(("+", "-", " "))
        or line == r"\ No newline at end of file"
    )

@dataclass(slots=True)
class PatchHunk:
    """Parsed hunk body for one file change."""

    header: str
    lines: list[str]


@dataclass(slots=True)
class PatchFileChange:
    """Parsed change summary for one file in a unified diff."""

    old_path: str
    new_path: str
    added_lines: list[str]
    removed_lines: list[str]
    hunk_count: int
    hunks: list[PatchHunk]
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
        patch_text = extract_patch_block(patch_text)
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
                    hunks=[],
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
                current.hunks.append(PatchHunk(header=line, lines=[]))
                continue
            if line.startswith("+") and not line.startswith("+++"):
                if current is None or current.hunk_count == 0:
                    raise PatchParseError("Malformed patch: added line outside hunk.")
                current.added_lines.append(line[1:])
                current.hunks[-1].lines.append(line)
                diff_line_count += 1
                continue
            if line.startswith("-") and not line.startswith("---"):
                if current is None or current.hunk_count == 0:
                    raise PatchParseError("Malformed patch: removed line outside hunk.")
                current.removed_lines.append(line[1:])
                current.hunks[-1].lines.append(line)
                diff_line_count += 1
                continue
            if line.startswith((" ", "\\ No newline at end of file", "diff --git", "index ")):
                if current is not None and current.hunk_count > 0 and line.startswith((" ", "\\")):
                    current.hunks[-1].lines.append(line)
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
        rename_count = sum(1 for file_change in parsed_patch.files if self._is_rename(file_change))
        if rename_count:
            reasons.extend(self._rename_reasons(parsed_patch, policy, rename_count))

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
        reasons.extend(self._implicit_script_chain_reasons(parsed_patch, policy))
        reasons.extend(self._readme_consistency_reasons(parsed_patch, policy))

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

    def _rename_reasons(
        self,
        parsed_patch: ParsedPatch,
        policy: CompiledPolicy,
        rename_count: int,
    ) -> list[str]:
        if policy.repo_kind != "script_collection":
            return ["Patch renames files or folders and requires approval."]
        if not policy.patch_policy.allow_script_collection_renames:
            return ["Patch renames files or folders and requires approval."]
        if rename_count > policy.patch_policy.max_script_collection_renames:
            return [
                "Patch renames too many files for a script_collection repo "
                f"({rename_count} > {policy.patch_policy.max_script_collection_renames})."
            ]
        reasons: list[str] = []
        for file_change in parsed_patch.files:
            if not self._is_rename(file_change):
                continue
            old_path = file_change.old_path.removeprefix("a/")
            new_path = file_change.new_path.removeprefix("b/")
            if self._is_license_file(old_path, policy) or self._is_license_file(new_path, policy):
                reasons.append(
                    f"Patch renames license-related files and requires approval: {new_path}"
                )
            if self._is_ci_file(old_path, policy) or self._is_ci_file(new_path, policy):
                reasons.append(f"Patch renames CI configuration and requires approval: {new_path}")
            if self._is_lockfile(old_path, policy) or self._is_lockfile(new_path, policy):
                reasons.append(f"Patch renames lockfiles and requires approval: {new_path}")
            if self._is_security_sensitive_path(
                old_path, policy
            ) or self._is_security_sensitive_path(new_path, policy):
                reasons.append(
                    f"Patch renames a security-sensitive path and requires approval: {new_path}"
                )
            if self._is_protected_artifact_path(
                old_path
            ) or self._is_protected_artifact_path(new_path):
                reasons.append(
                    f"Patch renames protected data artifacts and requires approval: {new_path}"
                )
        return reasons

    def _readme_consistency_reasons(
        self,
        parsed_patch: ParsedPatch,
        policy: CompiledPolicy,
    ) -> list[str]:
        if policy.repo_kind != "script_collection":
            return []
        readme_path = self._first_readme(policy.repo_root)
        if readme_path is None:
            return []
        try:
            readme_text = readme_path.read_text(encoding="utf-8")
        except OSError:
            return []
        readme_changed = any(
            Path(item.changed_path).name.upper().startswith("README")
            for item in parsed_patch.files
        )
        reasons: list[str] = []
        for file_change in parsed_patch.files:
            if not self._is_rename(file_change):
                continue
            old_rel = file_change.old_path.removeprefix("a/")
            old_name = Path(old_rel).name
            if old_rel in readme_text or old_name in readme_text:
                if not readme_changed:
                    reasons.append(
                        f"Patch renames {old_rel} but does not update README references."
                    )
        return reasons

    def _implicit_script_chain_reasons(
        self,
        parsed_patch: ParsedPatch,
        policy: CompiledPolicy,
    ) -> list[str]:
        if policy.repo_kind != "workflow_repo":
            return []
        if "implicit_script_chain" not in policy.workflow_sources:
            return []

        reasons: list[str] = []
        argparse_files: set[str] = set()
        for file_change in parsed_patch.files:
            changed_path = file_change.changed_path
            if not changed_path.endswith(".py"):
                continue
            if any(_ARGPARSE_SIGNAL_PATTERN.search(line) for line in file_change.added_lines):
                argparse_files.add(changed_path)
            if any(_ARGPARSE_HELP_PATTERN.search(line) for line in file_change.added_lines):
                reasons.append(
                    "Patch adds explicit argparse --help boilerplate and is rejected: "
                    f"{changed_path}"
                )
            if self._is_cli_protected_module(changed_path) and any(
                _ARGPARSE_SIGNAL_PATTERN.search(line) for line in file_change.added_lines
            ):
                reasons.append(
                    "Patch injects CLI parsing into importable workflow module and is "
                    f"rejected: {changed_path}"
                )
            reasons.extend(self._top_level_parse_args_reasons(file_change))
        if len(argparse_files) > 3:
            reasons.append(
                "Patch sprays argparse boilerplate across too many Python files for an "
                f"implicit script workflow ({len(argparse_files)} > 3)."
            )
        return reasons

    def _top_level_parse_args_reasons(self, file_change: PatchFileChange) -> list[str]:
        reasons: list[str] = []
        for hunk in file_change.hunks:
            if not any(
                line.startswith("+") and _PARSE_ARGS_PATTERN.search(line[1:]) for line in hunk.lines
            ):
                continue
            hunk_text = "\n".join(hunk.lines)
            if 'if __name__ == "__main__"' in hunk_text or "if __name__ == '__main__'" in hunk_text:
                continue
            if re.search(r"^\+\s*def\s+main\s*\(", hunk_text, flags=re.MULTILINE):
                continue
            reasons.append(
                "Patch adds parse_args() outside an obvious main guard and is rejected: "
                f"{file_change.changed_path}"
            )
        return reasons

    def _is_rename(self, file_change: PatchFileChange) -> bool:
        if file_change.is_new_file or file_change.is_deleted_file:
            return False
        return file_change.old_path.removeprefix("a/") != file_change.new_path.removeprefix("b/")

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

    def _is_cli_protected_module(self, changed_path: str) -> bool:
        path = Path(changed_path)
        if changed_path.startswith("ModelBase/"):
            return True
        if "models" in path.parts:
            return True
        return path.name in {"utils.py", "dataset_framework.py", "h5tools.py"}

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

    def _is_protected_artifact_path(self, changed_path: str) -> bool:
        path = Path(changed_path)
        return any(part in _PROTECTED_ARTIFACT_DIRS for part in path.parts) or (
            path.suffix.casefold() in _PROTECTED_ARTIFACT_SUFFIXES
        )

    def _first_readme(self, repo_root: Path) -> Path | None:
        for name in ("README.md", "README.rst", "README.txt"):
            path = repo_root / name
            if path.is_file():
                return path
        return None

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
