# ACCEPTANCE_CRITERIA.md

# Local SWE Controller Acceptance Criteria

This document defines the acceptance criteria for `local-swe-controller`.

A task is complete only when the implementation satisfies the functional requirements, safety requirements, test requirements, and documentation requirements described here.

The project must be built as a reliable software tool first and an AI agent second.

The core acceptance principle is:

> No feature is complete unless it is validated, tested, and safe by default.

---

## 1. Global Completion Rules

For every change, the following commands must pass:

```bash id="8m4kje"
uv run pytest
uv run ruff check .
```

When CLI behavior is changed, this must also pass:

```bash id="2rktr6"
uv run local-swe --help
```

When policy compilation behavior is changed, this must also pass:

```bash id="94vqxy"
uv run local-swe compile-policy --repo examples/fixture_python_repo
```

When validation behavior is changed, this must also pass:

```bash id="job1gf"
uv run local-swe validate --repo examples/fixture_python_repo
```

When repair behavior is changed, this must also pass using a fake model profile:

```bash id="ae7or8"
uv run local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

Do not mark a task complete if any required command fails.

If a command cannot be run in the current environment, the implementation must clearly report:

1. which command could not be run,
2. why it could not be run,
3. what evidence was used instead,
4. what remains unverified.

---

## 2. Universal Safety Acceptance Criteria

The following invariants must always hold.

## 2.1 Target Repository Safety

The controller must never modify the target repository directly.

Accepted behavior:

* read target repository files,
* create ephemeral git worktrees,
* run validation inside worktrees,
* apply patches only inside worktrees,
* save patch files as artifacts for manual application.

Rejected behavior:

* applying model patches directly to the target repo,
* running validation directly in the target repo when a worktree is required,
* writing generated files into the target repo unless explicitly requested,
* changing git state of the target repo,
* pushing changes.

Acceptance checks:

* tests verify the target repo remains unchanged after validation,
* tests verify the target repo remains unchanged after repair,
* worktree paths are outside the target repo unless explicitly configured and safe.

---

## 2.2 Command Safety

The controller must not execute arbitrary shell commands from model output.

Commands may only come from:

* compiled policy,
* explicit user input,
* trusted internal implementation,
* explicit approval flow.

The following command patterns must be blocked or require explicit approval:

```text id="i5fy6i"
rm -rf
git push --force
sudo
curl | bash
wget | bash
chmod -R 777
chown -R
npm install -g
pip install --user
docker system prune
```

Acceptance checks:

* tests cover forbidden command detection,
* tests cover shell-like dangerous command strings,
* tests cover command argument lists,
* command runner uses `shell=False` unless explicitly justified,
* model output is never directly executed.

---

## 2.3 Patch Safety

Patch candidates must be rejected or require approval if they:

* are malformed,
* exceed `--max-diff-lines`,
* touch forbidden paths,
* modify `.local-swe/` policy to bypass gates,
* remove tests without justification,
* weaken CI checks,
* weaken security checks,
* add dependencies,
* modify lockfiles,
* modify license files,
* modify security-sensitive code.

Acceptance checks:

* tests cover malformed patch rejection,
* tests cover forbidden path rejection,
* tests cover large diff rejection,
* tests cover dependency addition detection,
* tests cover lockfile change detection,
* tests cover license change detection,
* tests cover test removal detection.

---

## 2.4 Bounded Autonomy

The controller must not implement unbounded autonomous loops.

Autonomous execution must be bounded by explicit controls:

```text id="g7s8ff"
--max-iters
--max-candidates
--max-diff-lines
--timeout-per-command
--max-total-runtime-seconds
```

The controller must stop on:

* validation success,
* budget exhaustion,
* repeated identical failure,
* security failure,
* policy violation,
* dangerous command request,
* dependency change requiring approval,
* large diff requiring approval,
* environment failure that cannot be resolved.

Acceptance checks:

* default repair behavior uses `--max-iters 1`,
* tests cover max iteration stop,
* tests cover repeated failure stop after bounded retry is implemented,
* tests cover security stop,
* tests cover policy stop.

---

## 3. MVP Acceptance Criteria

The MVP is accepted only when the following commands pass:

```bash id="858p5h"
uv sync
uv run pytest
uv run ruff check .
uv run local-swe --help
uv run local-swe compile-policy --repo examples/fixture_python_repo
uv run local-swe validate --repo examples/fixture_python_repo
```

The MVP must satisfy all of the following:

1. The project can be installed with `uv`.
2. The CLI entrypoint `local-swe` works.
3. A fixture Python repository exists.
4. Policy compilation works on the fixture repo.
5. Validation works on the fixture repo.
6. Validation runs in an ephemeral worktree.
7. Command output is captured.
8. Failures are classified.
9. Artifacts are saved locally.
10. Tests do not require a real LLM.
11. Tests do not require GPU.
12. Tests do not require internet.
13. Tests do not require Docker.
14. Tests do not require sudo.
15. The target repo is never modified directly.

For the first repair MVP, this command must also pass with a fake model:

```bash id="h5gvkt"
uv run local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

---

## 4. Phase-Specific Acceptance Criteria

# Phase 0: Project Skeleton

## Required Deliverables

The repository must contain:

```text id="lr91k0"
pyproject.toml
README.md
AGENTS.md
PRODUCT_SPEC.md
ARCHITECTURE.md
TASKS.md
ACCEPTANCE_CRITERIA.md
src/local_swe_controller/
tests/
examples/fixture_python_repo/
```

The package name should be:

```text id="0h3vj5"
local-swe-controller
```

The import package should be:

```text id="by2jok"
local_swe_controller
```

The CLI entrypoint should be:

```text id="60mncs"
local-swe
```

## Required Commands

```bash id="734cne"
uv sync
uv run pytest
uv run ruff check .
uv run local-swe --help
uv run local-swe version
```

## Acceptance Conditions

* `local-swe --help` prints useful command information.
* `local-swe version` prints a version.
* The fixture repo is present.
* Tests run without internet, GPU, Docker, sudo, or a real LLM.

---

# Phase 1: Config and Schemas

## Required Deliverables

The project must contain validated schemas for:

```text id="f47xjv"
RunStatus
FailureClass
CommandSpec
CommandResult
ValidationReport
```

Required failure classes:

```text id="x65gtx"
ENVIRONMENT
BUILD
FORMAT
LINT
TYPECHECK
TEST
SECURITY
RESOURCE_EXHAUSTION
POLICY_VIOLATION
UNKNOWN
```

Required run statuses:

```text id="huqvjb"
NO_ACTION_NEEDED
BASELINE_FAILED
PATCH_PROPOSED
PATCH_VALIDATED
PATCH_REJECTED
SUCCESS
STOPPED_BY_BUDGET
STOPPED_BY_POLICY
STOPPED_BY_SECURITY
STOPPED_BY_ENVIRONMENT
ERROR
```

## Required Commands

```bash id="cw4bcv"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Valid configs load successfully.
* Invalid configs fail with clear errors.
* Enums validate correctly.
* Command schemas reject invalid command specs.
* Tests cover missing and malformed config.

---

# Phase 2: Policy Compiler MVP

## Required Deliverables

The policy compiler must inspect:

```text id="y6ro0r"
CONTRIBUTING.md
AGENTS.md
README.md
pyproject.toml
package.json
Cargo.toml
Makefile
.github/workflows/*.yml
```

It must produce a compiled policy containing:

```text id="f1mroe"
repo_root
policy_version
source_files
setup_commands
format_commands
lint_commands
typecheck_commands
test_commands
security_commands
hard_gates
soft_gates
forbidden_commands
forbidden_paths
approval_required_operations
generated_at
```

The CLI must support:

```bash id="1o959o"
local-swe compile-policy --repo /path/to/repo
```

## Required Commands

```bash id="75b03j"
uv run pytest
uv run ruff check .
uv run local-swe compile-policy --repo examples/fixture_python_repo
```

## Acceptance Conditions

* The compiler is deterministic.
* The compiler does not require an LLM.
* Python command inference works for the fixture repo.
* Missing optional files do not crash compilation.
* Invalid repo paths are handled clearly.
* The compiled policy is valid JSON.
* The output path is printed to the user.
* Tests cover Python, package.json, Cargo.toml, and Makefile inference where feasible.

---

# Phase 3: Command Safety and Runner

## Required Deliverables

The command runner must support:

```text id="wuvbqy"
explicit command argument lists
timeout_seconds
working directory
environment overrides
stdout capture
stderr capture
exit code capture
duration capture
forbidden command blocking
```

## Required Commands

```bash id="kc2d8y"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Successful commands return exit code `0`.
* Failed commands return non-zero exit code and captured stderr.
* Timeout is enforced.
* Forbidden commands are blocked before execution.
* stdout and stderr are saved to artifact paths.
* `shell=False` is used unless explicitly justified.
* Tests cover success, failure, timeout, forbidden command, and output capture.

---

# Phase 4: Ephemeral Worktree Sandbox

## Required Deliverables

The worktree manager must support:

```text id="j2817z"
creating temporary git worktrees
cleaning up temporary git worktrees
keep_worktree mode
invalid repo handling
dirty repo warning
cleanup after exceptions
```

## Required Commands

```bash id="lcrbmu"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Worktrees are created outside the target repo by default.
* Worktrees are cleaned up by default.
* `--keep-worktree` or equivalent preserves worktrees for debugging.
* Cleanup runs even after command failure.
* Invalid non-git directories are rejected.
* Tests verify the target repo is not modified.

---

# Phase 5: Validator MVP

## Required Deliverables

The validator must:

1. create an ephemeral worktree,
2. run policy commands,
3. save logs,
4. classify failures,
5. return a validation report,
6. clean up worktree.

The CLI must support:

```bash id="7vrqyr"
local-swe validate --repo /path/to/repo
```

Options:

```text id="du3uz8"
--policy
--keep-worktree
--json
```

## Required Commands

```bash id="8h09zo"
uv run pytest
uv run ruff check .
uv run local-swe validate --repo examples/fixture_python_repo
```

## Acceptance Conditions

* Passing fixture repo validates successfully.
* Failing fixture repo produces `FAIL`.
* Failure class is not `UNKNOWN` when deterministic classification is possible.
* Logs are saved.
* Worktree is cleaned up by default.
* Target repo remains unchanged.
* Tests cover passing validation, failing validation, timeout, policy violation, and cleanup.

---

# Phase 6: Local Artifact Storage

## Required Deliverables

The system must create and use:

```text id="8tl9ar"
.local-swe/
  runs/
  patches/
  logs/
  policies/
```

The system must store:

```text id="d1465r"
JSONL traces
SQLite run index
command logs
compiled policies
patch artifacts when available
```

The CLI must support:

```bash id="qa9lry"
local-swe runs list
local-swe runs show <run-id>
```

## Required Commands

```bash id="mkncs8"
uv run pytest
uv run ruff check .
uv run local-swe runs list
```

## Acceptance Conditions

* Artifact directories are created automatically.
* JSONL traces are valid JSON lines.
* SQLite run records can be inserted and read.
* `runs list` handles empty state.
* `runs show <run-id>` handles missing IDs clearly.
* Tests cover artifact creation and run retrieval.

---

# Phase 7: LLM Client

## Required Deliverables

The LLM layer must provide:

```text id="xrt7tr"
OpenAI-compatible local client
fake client
model profile schema
structured response validation where possible
timeout handling
transient retry
```

Default endpoint:

```text id="i7m36i"
http://localhost:8000/v1
```

The project must include:

```text id="q8ws9x"
configs/model_profiles.example.yaml
```

## Required Commands

```bash id="w5ze8j"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Tests use fake client by default.
* No real model is required for tests.
* No GPU is required for tests.
* The OpenAI-compatible endpoint is configurable.
* The client does not hard-code a cloud endpoint.
* Malformed responses are handled clearly.
* Transient retry behavior is tested with mocks or fake HTTP server.

---

# Phase 8: Patch Parser and Policy Check

## Required Deliverables

The patch layer must:

```text id="x9b06p"
parse unified diffs
extract changed files
count diff lines
detect forbidden paths
detect dependency file changes
detect lockfile changes
detect license changes
detect test removal
reject malformed patches
```

## Required Commands

```bash id="sdv1dg"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Valid patches parse successfully.
* Malformed patches are rejected.
* Patches touching forbidden paths are rejected.
* Large patches are rejected.
* Dependency additions require approval.
* Lockfile changes require approval.
* License changes require approval.
* Test removal is detected.
* Tests cover all patch policy checks.

---

# Phase 9: One-Shot Repair MVP

## Required Deliverables

The repair controller must implement:

```text id="73wlx4"
compile policy
baseline validation
NO_ACTION_NEEDED if baseline passes
failure evidence collection
one model patch proposal
patch parsing
static patch policy check
fresh worktree patch validation
patch artifact saving
final status reporting
```

The CLI must support:

```bash id="bjbzx2"
local-swe repair --repo /path/to/repo --goal "Fix failing tests"
```

## Required Commands

```bash id="nw5v6f"
uv run pytest
uv run ruff check .
uv run local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

## Acceptance Conditions

* Repair defaults to `--max-iters 1`.
* Fake model can return a patch.
* Patch is applied only in a fresh worktree.
* Patch validation is run after application.
* Successful patch is saved as artifact.
* Failed patch is rejected with reason.
* Target repo remains unchanged.
* Tests cover success, malformed patch, validation failure, and no-action-needed case.

---

# Phase 10: Bounded Retry

## Required Deliverables

Bounded retry must support:

```text id="z0stb6"
--max-iters
--max-candidates
--max-diff-lines
--timeout-per-command
--max-total-runtime-seconds
repeated failure detection
failure-aware retry
```

## Required Commands

```bash id="osazyx"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Retry never runs without explicit budget.
* Repeated identical failure causes stop.
* Security failure causes immediate stop.
* Environment failure causes stop and report.
* Lint/type/test failures produce different retry prompts.
* Tests cover all stop conditions.

---

# Phase 11: Model Router

## Required Deliverables

The router must support routing by task:

```text id="dotphk"
policy_extraction
failure_classification
fault_localization
patch_generation
patch_review
```

## Required Commands

```bash id="77mqtr"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Valid routes resolve to model profiles.
* Missing route is handled clearly.
* Missing model is handled clearly.
* Context window limit is enforced.
* Fallback behavior is tested.

---

# Phase 12: Test Generation

## Required Deliverables

Optional model-assisted test generation must support:

```text id="rn7bqc"
--generate-tests
```

Generated tests must be:

* applied in a worktree,
* validated before patch where appropriate,
* validated after patch,
* saved as artifacts.

## Required Commands

```bash id="k70xz6"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Generated tests do not disable existing tests.
* Generated tests do not require secrets.
* Generated tests do not require external services unless approved.
* Test-only changes are validated.
* Fake model tests cover test generation flow.

---

# Phase 13: Security and Supply Chain Gates

## Required Deliverables

Optional gates may include:

```text id="bpm7vq"
pip-audit
osv-scanner
detect-secrets
reuse
CodeQL
OpenSSF Scorecard
```

## Required Commands

```bash id="qnkxdd"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Tests do not require scanners to be installed.
* Scanner outputs can be mocked.
* Security findings map to `SECURITY`.
* Secret-looking content is flagged.
* Dependency and license changes require approval.

---

# Phase 14: Observability Enhancements

## Required Deliverables

The system should emit structured events:

```text id="6u2k7y"
run_started
policy_compiled
baseline_validation_started
baseline_validation_finished
model_called
patch_generated
patch_policy_checked
patch_validation_started
patch_validation_finished
run_finished
```

It should generate:

```text id="uwktwz"
.local-swe/runs/<run-id>/summary.md
```

## Required Commands

```bash id="3ue0n7"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Events are valid JSON.
* Summary report is generated.
* Optional OpenTelemetry is disabled by default.
* Tests cover event writing and summary generation.

---

# Phase 15: LangGraph Orchestration

## Required Deliverables

LangGraph orchestration is optional and must not destabilize the deterministic controller.

If implemented, it should include nodes for:

```text id="3vp94x"
intake
compile_policy
baseline_validate
classify_failure
localize_fault
generate_patch
patch_policy_check
validate_patch
rank_patch
store_artifacts
escalate
```

## Required Commands

```bash id="55n5dw"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* LangGraph dependency is optional unless explicitly promoted.
* Existing CLI behavior remains unchanged.
* Graph execution supports fake model tests.
* Human interrupt points exist for risky operations.
* Checkpointing works in tests.

---

# Phase 16: GitHub Integration

## Required Deliverables

GitHub integration must be optional.

Possible features:

```text id="btoqav"
PR summary generation
optional PR creation
check-run annotations
```

## Required Commands

```bash id="ky25yk"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* No real GitHub API call is required in tests.
* Network calls are mocked.
* The system never pushes by default.
* PR creation requires explicit configuration.
* Generated PR summaries include validation evidence.

---

# Phase 17: Custom Benchmark Runner

## Required Deliverables

Benchmark cases should include:

```text id="rlg8x9"
repo
setup
failure
expected_status
acceptance_commands
notes
```

The CLI may support:

```bash id="uuwm9c"
local-swe bench run --cases benchmarks/*.yaml
```

## Required Commands

```bash id="2r6tfm"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* Benchmark cases are schema-validated.
* Fixture benchmark cases run locally.
* Metrics are saved.
* Tests do not require internet, GPU, Docker, sudo, or real LLM.

---

# Phase 18: Documentation Polish

## Required Deliverables

The README must include:

* project purpose,
* installation,
* quickstart,
* CLI examples,
* local model setup,
* safety model,
* MVP limitations.

Optional docs:

```text id="twn9my"
docs/policy_compiler.md
docs/validation.md
docs/repair_loop.md
docs/model_profiles.md
docs/safety.md
```

## Required Commands

```bash id="b5ocma"
uv run pytest
uv run ruff check .
```

## Acceptance Conditions

* User-facing commands in docs are accurate.
* Safety constraints are documented.
* MVP limitations are documented.
* Local model setup is documented without requiring cloud services.

---

## 5. Required Test Matrix

The test suite must cover the following categories over time.

## 5.1 Unit Tests

Required coverage:

```text id="7kq3p4"
schema validation
config loading
policy inference
command safety
failure classification
patch parsing
patch policy checks
model profile validation
artifact writing
```

## 5.2 Integration Tests

Required coverage:

```text id="yoav9q"
temporary git repo creation
worktree creation and cleanup
validation command execution
policy compilation on fixture repo
repair loop with fake model
target repo unchanged
```

## 5.3 Safety Tests

Required coverage:

```text id="g8is95"
forbidden command blocked
dangerous patch rejected
dependency change requires approval
lockfile change requires approval
license change requires approval
security failure stops retry
model output is not executed as shell
```

## 5.4 Regression Tests

Required coverage:

```text id="8kbbzq"
known ruff failure -> LINT
known mypy failure -> TYPECHECK
known pytest failure -> TEST
known missing executable -> ENVIRONMENT
known timeout -> RESOURCE_EXHAUSTION
known forbidden command -> POLICY_VIOLATION
```

---

## 6. Required Artifact Evidence

For validation and repair runs, the system must save enough evidence for inspection.

Required artifacts:

```text id="x62sjt"
compiled policy
stdout logs
stderr logs
command results
validation report
candidate patch
patch validation report
run summary
JSONL trace
SQLite run record
```

Artifacts should be saved under:

```text id="7oxsfs"
.local-swe/
  runs/
  patches/
  logs/
  policies/
```

Acceptance condition:

* A human should be able to inspect a run without re-running it.

---

## 7. CLI Output Acceptance Criteria

CLI output should clearly report:

```text id="k68guq"
repository path
git commit if available
policy output path
commands run
failed command
exit code
failure class
log paths
patch path if generated
final status
next manual action
```

Bad output:

```text id="861jxb"
Something failed.
```

Good output:

```text id="iz2is7"
Validation status: FAIL
Failure class: TEST
Failed command: uv run pytest
Exit code: 1
stderr: .local-swe/runs/<run-id>/logs/test.stderr
```

---

## 8. Documentation Acceptance Criteria

Documentation is acceptable when:

* a new developer can install the project,
* a new developer can run the fixture repo validation,
* a new developer can understand the safety model,
* a new developer can see what the MVP does not do,
* command examples are accurate,
* local model setup is described as optional for tests,
* fake model flow is documented.

---

## 9. Definition of Done

A task is done only when all applicable items are true:

1. The requested functionality is implemented.
2. Tests are added or updated.
3. Required commands pass.
4. Safety invariants are preserved.
5. Target repositories are not modified directly.
6. Worktree behavior is tested where applicable.
7. Artifacts are saved where applicable.
8. Failure cases are classified where applicable.
9. CLI output is clear.
10. Documentation is updated if user-facing behavior changes.
11. No real LLM is required in tests.
12. No GPU is required in tests.
13. No internet is required in tests.
14. No Docker is required in tests.
15. No sudo is required in tests.

---

## 10. Definition of Not Done

A task is not done if any of the following are true:

* tests were skipped without explanation,
* required commands fail,
* target repo is modified directly,
* model output is executed as shell,
* validation is bypassed,
* safety checks are weakened,
* policy is modified to hide failures,
* tests are removed to make validation pass,
* cloud dependency is added by default,
* real LLM is required in tests,
* GPU is required in tests,
* implementation relies on unbounded loops,
* artifacts are not inspectable,
* failure is reported only as a generic error,
* documentation contradicts CLI behavior.

---

## 11. MVP Release Gate

The MVP can be tagged only when all of the following pass:

```bash id="cd3k61"
uv sync
uv run pytest
uv run ruff check .
uv run local-swe --help
uv run local-swe compile-policy --repo examples/fixture_python_repo
uv run local-swe validate --repo examples/fixture_python_repo
```

And the following are true:

```text id="bmayn3"
target repo is not modified directly
validation runs in ephemeral worktree
policy compiler is deterministic
tests require no real LLM
tests require no GPU
tests require no internet
tests require no Docker
tests require no sudo
artifacts are saved locally
failures are classified
```

The first repair MVP can be tagged only when this also passes:

```bash id="bts4hp"
uv run local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

---

## 12. Final Acceptance Statement

The project is acceptable when it behaves like:

```text id="wrpnvg"
a strict local CI-aware repair controller with an LLM-powered patch proposal stage
```

and not like:

```text id="bbgztb"
an unrestricted AI agent that might do anything
```

The validator has final authority.
The policy gate constrains actions.
The user approves risky changes.
The model proposes candidates only.
