# TASKS.md

# Local SWE Controller Task Plan

This document defines the implementation plan for `local-swe-controller`.

The goal is to build the project in disciplined phases:

```text
deterministic core
  -> policy compiler
  -> sandbox validator
  -> local model client
  -> one-shot patch loop
  -> bounded autonomy
  -> observability
  -> advanced orchestration
```

Do not skip phases.

The project should be built as a reliable software tool first and an AI agent second.

---

## Global Rules for All Tasks

Before marking any task complete:

```bash
uv run pytest
uv run ruff check .
```

When CLI behavior changes:

```bash
uv run local-swe --help
```

When policy behavior changes:

```bash
uv run local-swe compile-policy --repo examples/fixture_python_repo
```

When validation behavior changes:

```bash
uv run local-swe validate --repo examples/fixture_python_repo
```

Do not claim completion if these commands fail.

---

## Global Engineering Rules

Follow these rules for every phase:

* Do not modify a target repository directly.
* Use ephemeral git worktrees for validation.
* Do not run arbitrary shell from model output.
* Do not add cloud dependencies by default.
* Do not require Docker for MVP.
* Do not require sudo.
* Do not require GPU.
* Do not require a real LLM in tests.
* Use fake clients in tests.
* Add tests for new behavior.
* Preserve logs and artifacts.
* Prefer deterministic behavior before model-driven behavior.
* Prefer explicit failure classes over generic errors.

---

# Phase 0: Project Skeleton

## Goal

Create a minimal Python project that can be installed, tested, linted, and invoked from the CLI.

## Tasks

### 0.1 Create project files

Create:

```text
pyproject.toml
README.md
AGENTS.md
PRODUCT_SPEC.md
ARCHITECTURE.md
TASKS.md
```

The package name should be:

```text
local-swe-controller
```

The import package should be:

```text
local_swe_controller
```

### 0.2 Configure Python tooling

Use:

* Python 3.11+
* `uv`
* `pytest`
* `ruff`
* `typer`
* `pydantic>=2`

`pyproject.toml` should define:

* package metadata,
* dependencies,
* dev dependencies,
* CLI entrypoint,
* ruff config,
* pytest config.

CLI entrypoint:

```text
local-swe
```

### 0.3 Create source layout

Create:

```text
src/local_swe_controller/
  __init__.py
  cli.py
  config.py
  exceptions.py
```

### 0.4 Create test layout

Create:

```text
tests/
  test_cli.py
```

### 0.5 Implement basic CLI

Initial commands:

```bash
local-swe --help
local-swe version
```

### 0.6 Create fixture repo

Create:

```text
examples/fixture_python_repo/
  pyproject.toml
  CONTRIBUTING.md
  src/example_pkg/__init__.py
  tests/test_example.py
```

The fixture repo should be a valid git repository in tests when copied to a temporary directory.

Do not assume `examples/fixture_python_repo` itself is already initialized as git.

## Acceptance Criteria

These commands pass:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run local-swe --help
```

## Do Not Implement Yet

* policy compiler,
* worktree sandbox,
* validator,
* LLM client,
* patch generation.

---

# Phase 1: Config and Schemas

## Goal

Create validated configuration schemas used by later phases.

## Tasks

### 1.1 Implement base config loading

Create:

```text
src/local_swe_controller/config.py
```

Support loading YAML config files.

Use Pydantic v2.

### 1.2 Define common models

Create:

```text
src/local_swe_controller/models.py
```

Suggested models:

```python
RunStatus
FailureClass
CommandSpec
CommandResult
ValidationReport
```

Required failure classes:

```text
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

Suggested run statuses:

```text
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

### 1.3 Add tests

Test:

* valid config,
* missing config,
* malformed config,
* enum validation,
* command schema validation.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 2: Policy Compiler MVP

## Goal

Implement deterministic policy compilation for common repositories.

The policy compiler should not use an LLM in MVP.

## Tasks

### 2.1 Create policy package

Create:

```text
src/local_swe_controller/policy/
  __init__.py
  schema.py
  compiler.py
```

### 2.2 Define policy schema

Implement `CompiledPolicy`.

It should include:

```text
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

### 2.3 Implement repo scanning

The compiler should inspect:

```text
CONTRIBUTING.md
AGENTS.md
README.md
pyproject.toml
package.json
Cargo.toml
Makefile
.github/workflows/*.yml
```

### 2.4 Implement Python command inference

If `pyproject.toml` includes or suggests:

* `pytest`, infer test command,
* `ruff`, infer lint command,
* `mypy`, infer typecheck command,
* `pyright`, infer typecheck command.

Prefer project commands if available.

Examples:

```text
uv run pytest
uv run ruff check .
uv run mypy .
uv run pyright
```

### 2.5 Implement JavaScript / TypeScript inference

If `package.json` exists, inspect scripts:

```text
lint
test
typecheck
format
```

Infer:

```text
npm run lint
npm test
npm run typecheck
npm run format -- --check
```

where appropriate.

### 2.6 Implement Rust inference

If `Cargo.toml` exists, infer:

```text
cargo check
cargo test
cargo clippy -- -D warnings
```

### 2.7 Implement Makefile inference

If `Makefile` contains targets such as:

```text
lint
test
typecheck
format-check
```

prefer:

```text
make lint
make test
make typecheck
make format-check
```

### 2.8 Add CLI command

Add:

```bash
local-swe compile-policy --repo /path/to/repo
```

Options:

```text
--output
--json
```

Default output:

```text
.local-swe/policies/policy.compiled.json
```

### 2.9 Add tests

Test:

* Python fixture repo,
* missing optional files,
* package.json script inference,
* Cargo.toml inference,
* Makefile inference,
* invalid repo path,
* output file creation.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
uv run local-swe compile-policy --repo examples/fixture_python_repo
```

## Do Not Implement Yet

* LLM policy extraction,
* model routing,
* patch generation.

---

# Phase 3: Command Safety and Runner

## Goal

Implement a safe command runner that executes explicit commands and records outputs.

## Tasks

### 3.1 Create sandbox package

Create:

```text
src/local_swe_controller/sandbox/
  __init__.py
  commands.py
```

### 3.2 Implement command safety checks

Block forbidden commands such as:

```text
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

Command safety should inspect command argument lists, not only raw strings.

### 3.3 Implement command runner

The runner must capture:

```text
stdout
stderr
exit_code
duration_seconds
timed_out
command
cwd
```

It must support:

```text
timeout_seconds
env overrides
working directory
```

Prefer `subprocess.run` with `shell=False`.

### 3.4 Save logs

Save stdout/stderr to artifact paths.

Do not keep huge logs only in memory.

### 3.5 Add tests

Test:

* successful command,
* non-zero exit code,
* timeout,
* forbidden command,
* stdout/stderr capture,
* working directory behavior.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 4: Ephemeral Worktree Sandbox

## Goal

Ensure all validation happens outside the target repository.

## Tasks

### 4.1 Implement worktree manager

Create:

```text
src/local_swe_controller/sandbox/worktree.py
```

Responsibilities:

* verify target repo is git repo,
* create temporary git worktree,
* expose worktree path,
* clean up worktree,
* support `keep_worktree`.

### 4.2 Handle dirty repositories

For MVP:

* allow validation of current commit,
* warn if target repo is dirty,
* do not attempt to copy uncommitted changes by default.

Future option:

```text
--include-dirty
```

should be considered later, not required in MVP.

### 4.3 Ensure cleanup

Worktree cleanup should run even if command execution fails.

If cleanup fails, report it.

### 4.4 Add tests

Test:

* worktree creation,
* worktree cleanup,
* keep worktree mode,
* target repo unchanged,
* invalid non-git directory,
* cleanup after exception.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 5: Validator MVP

## Goal

Run compiled policy commands inside ephemeral worktrees and classify failures.

## Tasks

### 5.1 Create validation package

Create:

```text
src/local_swe_controller/validation/
  __init__.py
  runner.py
  classifier.py
```

### 5.2 Implement failure classifier

Use deterministic rules.

Examples:

```text
missing executable:
  ENVIRONMENT

ModuleNotFoundError:
  ENVIRONMENT

SyntaxError:
  BUILD

ruff / eslint / clippy:
  LINT

mypy / pyright / tsc:
  TYPECHECK

pytest / cargo test / npm test:
  TEST

osv-scanner / pip-audit / CodeQL:
  SECURITY

timeout / OOM / killed:
  RESOURCE_EXHAUSTION

forbidden command:
  POLICY_VIOLATION
```

### 5.3 Implement validation runner

Validation runner should:

1. create worktree,
2. run policy commands,
3. save logs,
4. classify failures,
5. return `ValidationReport`,
6. clean up worktree.

### 5.4 Add CLI command

Add:

```bash
local-swe validate --repo /path/to/repo
```

Options:

```text
--policy
--keep-worktree
--json
```

### 5.5 Add tests

Test:

* passing fixture repo,
* failing fixture repo,
* lint failure,
* test failure,
* timeout,
* policy violation,
* target repo unchanged.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
uv run local-swe validate --repo examples/fixture_python_repo
```

---

# Phase 6: Local Artifact Storage

## Goal

Persist validation and repair evidence.

## Tasks

### 6.1 Create storage package

Create:

```text
src/local_swe_controller/storage/
  __init__.py
  artifacts.py
  sqlite.py
```

### 6.2 Implement artifact directory layout

Use:

```text
.local-swe/
  runs/
  patches/
  logs/
  policies/
```

### 6.3 Implement run IDs

Run IDs should be unique and stable enough for local inspection.

Suggested format:

```text
YYYYMMDD-HHMMSS-<short-random>
```

### 6.4 Implement JSONL traces

Store events such as:

```text
run_started
policy_compiled
worktree_created
command_started
command_finished
validation_finished
patch_generated
patch_validated
run_finished
```

### 6.5 Implement SQLite run index

Minimum fields:

```text
run_id
repo_path
git_commit
goal
policy_hash
final_status
failure_class
patch_path
created_at
updated_at
```

### 6.6 Add CLI commands

Add:

```bash
local-swe runs list
local-swe runs show <run-id>
```

### 6.7 Add tests

Test:

* artifact directory creation,
* JSONL trace writing,
* SQLite insert/read,
* run list,
* run show,
* missing run ID.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
uv run local-swe runs list
```

---

# Phase 7: LLM Client

## Goal

Add local OpenAI-compatible model client and fake test client.

## Tasks

### 7.1 Create LLM package

Create:

```text
src/local_swe_controller/llm/
  __init__.py
  client.py
  fake.py
  schema.py
```

### 7.2 Implement model profile schema

Support:

```text
provider
base_url
api_key
model
context_window
temperature
max_output_tokens
```

### 7.3 Implement OpenAI-compatible client

Support chat completions.

Requirements:

* local endpoint by default,
* configurable base URL,
* timeout,
* transient retry,
* structured response validation where possible,
* no hard-coded cloud endpoint.

Default:

```text
http://localhost:8000/v1
```

### 7.4 Implement fake client

Fake client should support deterministic responses for tests.

### 7.5 Add config file

Create:

```text
configs/model_profiles.example.yaml
```

Include:

```yaml
models:
  main_patch_model:
    provider: openai_compatible
    base_url: "http://localhost:8000/v1"
    model: "Qwen3-Coder-30B-A3B-Instruct-AWQ"
    context_window: 262144
    temperature: 0.2
    max_output_tokens: 8192

  fast_triage_model:
    provider: openai_compatible
    base_url: "http://localhost:8001/v1"
    model: "Qwen3-8B-Instruct"
    context_window: 32768
    temperature: 0.1
    max_output_tokens: 4096

routing:
  policy_extraction: fast_triage_model
  failure_classification: fast_triage_model
  fault_localization: main_patch_model
  patch_generation: main_patch_model
  patch_review: main_patch_model
```

### 7.6 Add tests

Test:

* fake client response,
* model profile validation,
* malformed response,
* transient error retry using fake HTTP server,
* no real model required.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 8: Patch Parser and Policy Check

## Goal

Parse and statically check candidate patches before applying them.

## Tasks

### 8.1 Create repair package

Create:

```text
src/local_swe_controller/repair/
  __init__.py
  patch.py
```

### 8.2 Implement unified diff parser

The parser should extract:

```text
changed files
added lines
removed lines
diff line count
file status if possible
```

### 8.3 Implement patch policy checks

Reject patches that:

* are malformed,
* exceed max diff lines,
* touch forbidden paths,
* modify `.local-swe/` policy to bypass gates,
* remove tests without justification,
* weaken CI files without approval,
* add dependencies without approval,
* change lockfiles without approval,
* change license files without approval.

### 8.4 Add tests

Test:

* valid patch,
* malformed patch,
* forbidden path,
* large diff,
* dependency file change,
* lockfile change,
* license change,
* test deletion.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 9: One-Shot Repair MVP

## Goal

Implement the first end-to-end repair workflow.

## Tasks

### 9.1 Implement repair controller

Create:

```text
src/local_swe_controller/repair/controller.py
src/local_swe_controller/repair/proposer.py
src/local_swe_controller/repair/validator.py
```

### 9.2 Implement repair flow

Flow:

```text
compile policy
  -> baseline validation
  -> if pass: NO_ACTION_NEEDED
  -> collect failure evidence
  -> ask model for one unified diff patch
  -> parse patch
  -> static patch policy check
  -> apply patch to fresh worktree
  -> run validation
  -> save patch and result
  -> report status
```

### 9.3 Add CLI command

Add:

```bash
local-swe repair --repo /path/to/repo --goal "Fix failing tests"
```

Options:

```text
--model-profile
--max-iters
--max-diff-lines
--keep-worktree
--json
```

Default:

```text
--max-iters 1
```

### 9.4 Add fake repair test

Use a fixture repo with a failing test.

Fake model returns a patch.

The controller applies patch in worktree and validates it.

### 9.5 Save patch artifact

Save patches under:

```text
.local-swe/patches/
```

### 9.6 Add tests

Test:

* no action needed,
* baseline failure,
* fake model patch success,
* fake model malformed patch,
* patch validation failure,
* target repo unchanged,
* artifact paths exist.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
uv run local-swe repair --repo examples/fixture_python_repo --goal "Fix failing tests" --model-profile fake
```

---

# Phase 10: Bounded Retry

## Goal

Add controlled iteration without unbounded autonomy.

## Tasks

### 10.1 Add budget config

Support:

```text
--max-iters
--max-candidates
--max-diff-lines
--timeout-per-command
--max-total-runtime-seconds
```

### 10.2 Detect repeated failures

Stop when the same failure signature repeats.

Failure signature may include:

```text
failure_class
failed_command
stderr hash
changed files
```

### 10.3 Implement failure-aware retry

Strategies:

```text
LINT:
  ask for minimal lint-focused patch

TYPECHECK:
  ask for type-focused patch

TEST:
  ask for semantic fix

ENVIRONMENT:
  stop and report setup issue

SECURITY:
  stop immediately

POLICY_VIOLATION:
  stop or request explicit approval
```

### 10.4 Add tests

Test:

* max iteration stop,
* repeated failure stop,
* security stop,
* environment stop,
* lint retry prompt,
* typecheck retry prompt,
* test retry prompt.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 11: Model Router

## Goal

Support multiple local model profiles by task.

## Tasks

### 11.1 Implement router

Create:

```text
src/local_swe_controller/llm/router.py
```

### 11.2 Implement routing config

Route task kinds:

```text
policy_extraction
failure_classification
fault_localization
patch_generation
patch_review
```

### 11.3 Add context limits

Reject or truncate context if it exceeds model profile limits.

### 11.4 Add tests

Test:

* valid route,
* missing route,
* missing model,
* context window violation,
* fallback model.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 12: Test Generation

## Goal

Add optional model-assisted test generation after repair MVP is stable.

## Tasks

### 12.1 Add test proposal mode

New command option:

```text
--generate-tests
```

### 12.2 Generate regression tests

The model may propose tests based on:

* failing behavior,
* stack traces,
* issue goal,
* relevant source snippets.

### 12.3 Validate generated tests

Generated tests must be:

* applied in a worktree,
* run before patch,
* expected to fail when appropriate,
* run after patch,
* expected to pass.

### 12.4 Safety checks

Reject generated tests that:

* skip existing tests,
* weaken assertions,
* depend on external services,
* encode arbitrary implementation details too strongly,
* require secrets.

### 12.5 Add tests

Use fake model responses.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 13: Security and Supply Chain Gates

## Goal

Add optional security and supply-chain checks.

## Tasks

### 13.1 Add optional scanners

Support when installed:

```text
pip-audit
osv-scanner
detect-secrets
reuse
```

### 13.2 Add policy gates

Security checks may be hard or soft gates.

### 13.3 Add patch checks

Flag:

* dependency addition,
* lockfile change,
* license file change,
* security-sensitive code change,
* secret-looking content.

### 13.4 Add tests

Tests should not require scanners to be installed.
Mock scanner outputs.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 14: Observability Enhancements

## Goal

Improve run tracing and optional observability integrations.

## Tasks

### 14.1 Add structured event model

Events:

```text
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

### 14.2 Add OpenTelemetry hooks

Optional only.

Disabled by default.

### 14.3 Add summary report

Generate:

```text
.local-swe/runs/<run-id>/summary.md
```

### 14.4 Add tests

Test event writing and summary generation.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 15: LangGraph Orchestration

## Goal

Add durable orchestration only after deterministic controller works.

## Tasks

### 15.1 Add optional LangGraph dependency

Do not make it required for MVP.

### 15.2 Implement graph nodes

Suggested nodes:

```text
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

### 15.3 Add checkpointing

Persist state between steps.

### 15.4 Add human interrupt points

Interrupt on:

* dependency addition,
* large diff,
* security-sensitive path,
* license change,
* repeated failure,
* budget exhaustion.

### 15.5 Add tests

Use fake model and fake graph execution.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 16: GitHub Integration

## Goal

Optional integration after local workflow is stable.

## Tasks

### 16.1 Add PR summary generation

Generate markdown report for a patch.

### 16.2 Add optional PR creation

Only when explicitly configured.

Never push by default.

### 16.3 Add check-run annotations

Optional.

### 16.4 Add tests

Mock GitHub API.

No real network required.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 17: Custom Benchmark Runner

## Goal

Evaluate the controller on local benchmark tasks.

## Tasks

### 17.1 Define benchmark schema

A benchmark case should include:

```text
repo
setup
failure
expected_status
acceptance_commands
notes
```

### 17.2 Add benchmark command

```bash
local-swe bench run --cases benchmarks/*.yaml
```

### 17.3 Track metrics

Track:

```text
resolved rate
patch validation pass rate
rollback rate
human review required rate
average iterations
average runtime
failure class distribution
```

### 17.4 Add tests

Use small fixture cases.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Phase 18: Documentation Polish

## Goal

Make the project usable by another developer.

## Tasks

### 18.1 README

README should include:

* project purpose,
* installation,
* quickstart,
* CLI examples,
* local model setup,
* safety model,
* MVP limitations.

### 18.2 Developer docs

Add:

```text
docs/
  policy_compiler.md
  validation.md
  repair_loop.md
  model_profiles.md
  safety.md
```

### 18.3 Examples

Add examples for:

* Python repo,
* JS/TS repo,
* Rust repo,
* fake model repair.

## Acceptance Criteria

```bash
uv run pytest
uv run ruff check .
```

---

# Priority Summary

## Must Have for MVP

```text
Phase 0: Project Skeleton
Phase 1: Config and Schemas
Phase 2: Policy Compiler MVP
Phase 3: Command Safety and Runner
Phase 4: Ephemeral Worktree Sandbox
Phase 5: Validator MVP
Phase 6: Local Artifact Storage
Phase 7: LLM Client
Phase 8: Patch Parser and Policy Check
Phase 9: One-Shot Repair MVP
```

## Should Have Next

```text
Phase 10: Bounded Retry
Phase 11: Model Router
Phase 12: Test Generation
Phase 13: Security and Supply Chain Gates
Phase 14: Observability Enhancements
```

## Later

```text
Phase 15: LangGraph Orchestration
Phase 16: GitHub Integration
Phase 17: Custom Benchmark Runner
Phase 18: Documentation Polish
```

---

# Initial Codex Task

Use this task for the first Codex run:

```text
Implement Phase 0 to Phase 2 of local-swe-controller.

Follow AGENTS.md, PRODUCT_SPEC.md, ARCHITECTURE.md, and TASKS.md.

Create a Python 3.11+ package using uv, Typer, Pydantic v2, pytest, and ruff.

Implement:
- project skeleton,
- CLI entrypoint,
- config schemas,
- policy schema,
- deterministic policy compiler,
- fixture Python repo,
- tests.

Do not implement:
- worktree sandbox,
- validator,
- LLM client,
- patch generation,
- bounded retry.

Acceptance commands:
uv sync
uv run pytest
uv run ruff check .
uv run local-swe --help
uv run local-swe compile-policy --repo examples/fixture_python_repo
```

---

# Completion Definition

The implementation is complete only when:

1. The requested phase is implemented.
2. Tests are added.
3. Required commands pass.
4. The target repo is never modified directly.
5. Safety rules are preserved.
6. Artifacts are inspectable where relevant.
7. The README or docs are updated if user-facing behavior changes.

Do not skip validation.
Do not mark incomplete work as done.
