# AGENTS.md

## Project Purpose

This repository implements a **local-first software engineering controller for coding agents**.

It is not a chatbot and not a free-form autonomous agent.
It is a verifier-first repair pipeline that prepares, validates, and safely applies candidate code patches in arbitrary target repositories.

The controller should support the following long-term workflow:

1. Read a target repository.
2. Compile repository conventions from `CONTRIBUTING.md`, `AGENTS.md`, CI files, and project metadata into a machine-readable policy.
3. Create an ephemeral git worktree.
4. Run setup, lint, typecheck, test, security, and other validation commands.
5. Call a local OpenAI-compatible model endpoint when needed.
6. Generate patch candidates.
7. Validate patches in fresh isolated worktrees.
8. Save traces, logs, patches, validation results, and artifacts.
9. Stop safely under bounded autonomy.

The core principle is:

> Verifier first, model second.

The LLM proposes candidates.
The validator decides whether they are acceptable.

---

## High-Level Design Principles

Follow these principles throughout the repository:

* Prefer deterministic workflow control over open-ended agent behavior.
* Prefer small, composable modules over large agentic abstractions.
* Do not implement unbounded autonomous loops.
* Every patch must be validated in a fresh ephemeral git worktree.
* Never modify a target repository directly.
* `CONTRIBUTING.md` and repo-level instructions must be compiled into structured policy instead of blindly copied into prompts.
* All command execution must be explicit, traceable, and policy-aware.
* Dangerous operations require explicit approval.
* Local execution must be the default.
* Cloud integrations must be optional.
* Do not assume sudo.
* Do not require Docker for the MVP.
* Keep the MVP small but real and tested.
* All behavior must be testable without a real LLM.
* Use fake model clients in tests.
* Prefer clear failure classification over blind retries.
* Always preserve evidence: logs, command outputs, patches, metadata, and final status.

---

## Technology Stack

Use the following default stack unless there is a strong reason to change it:

* Python 3.11+
* `uv` for environment and dependency management
* Typer for CLI
* Pydantic v2 for schemas and config validation
* pytest for tests
* ruff for linting and formatting
* SQLite for local run metadata
* git worktrees for ephemeral sandboxing
* OpenAI-compatible API client for local model servers

Do not add heavy infrastructure unless explicitly requested.

Avoid adding these in the initial implementation:

* Kubernetes
* mandatory Docker
* cloud services
* distributed queues
* web UI
* model fine-tuning
* self-improving agent loops
* multi-agent mesh

---

## Expected Repository Structure

Prefer this structure:

```text
local-swe-controller/
  AGENTS.md
  README.md
  pyproject.toml
  configs/
    default_policy.yaml
    model_profiles.example.yaml
  examples/
    fixture_python_repo/
      pyproject.toml
      CONTRIBUTING.md
      src/example_pkg/__init__.py
      tests/test_example.py
  src/
    local_swe_controller/
      __init__.py
      cli.py
      config.py
      models.py
      policy/
        __init__.py
        compiler.py
        schema.py
      sandbox/
        __init__.py
        worktree.py
        commands.py
      validation/
        __init__.py
        runner.py
        classifier.py
      storage/
        __init__.py
        sqlite.py
      llm/
        __init__.py
        client.py
        fake.py
  tests/
    test_cli.py
    test_policy_compiler.py
    test_worktree.py
    test_command_runner.py
    test_failure_classifier.py
```

This structure may evolve, but keep modules small and testable.

---

## Development Phases

### Phase 0: Project Skeleton

Implement:

* `pyproject.toml`
* CLI entry point
* basic config loading
* test setup
* example fixture repository
* minimal README

Acceptance commands:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run local-swe --help
```

Do not implement model inference in this phase.

---

### Phase 1: Policy Compiler

Implement a deterministic policy compiler.

It should inspect the target repository and read, when present:

* `CONTRIBUTING.md`
* `AGENTS.md`
* `README.md`
* `pyproject.toml`
* `package.json`
* `Cargo.toml`
* `Makefile`
* `.github/workflows/*.yml`

It should produce a compiled policy such as:

```json
{
  "setup_commands": [],
  "format_commands": [],
  "lint_commands": [],
  "typecheck_commands": [],
  "test_commands": [],
  "security_commands": [],
  "hard_gates": [],
  "soft_gates": [],
  "forbidden_commands": [],
  "approval_required_operations": []
}
```

For the first implementation, use deterministic heuristics.
Do not rely on an LLM to compile policy in the MVP.

The compiler must be tested with temporary fixture repositories.

---

### Phase 2: Ephemeral Worktree Sandbox and Validator

Implement:

* creation of ephemeral git worktrees
* cleanup of worktrees
* optional `--keep-worktree`
* command execution with timeout
* capture of stdout, stderr, exit code, and duration
* validation command runner
* failure classification

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

Never run validation directly in the target repository.

---

### Phase 3: Local Model Client

Implement an OpenAI-compatible local model client.

Requirements:

* configurable `base_url`
* configurable `api_key`
* configurable `model`
* chat completion support
* structured JSON output validation where possible
* retry only on transient connection errors
* fake client for tests
* no cloud dependency by default

Default local endpoint:

```text
http://localhost:8000/v1
```

Do not hard-code OpenAI cloud endpoints.

---

### Phase 4: Patch Loop MVP

Implement the first end-to-end repair loop.

Command shape:

```bash
local-swe repair --repo /path/to/repo --goal "Fix failing tests"
```

MVP flow:

1. Compile policy.
2. Create baseline worktree.
3. Run validation.
4. If validation passes, exit with `NO_ACTION_NEEDED`.
5. If validation fails, collect relevant logs.
6. Ask the model for a unified diff patch.
7. Apply the patch to a fresh worktree.
8. Run validation again.
9. If hard gates pass, save patch and report success.
10. Otherwise classify failure and stop.

Default `--max-iters` should be `1`.

Do not implement unbounded retry.

---

### Phase 5: Bounded Autonomy

Add bounded retry only after the MVP is stable.

Required controls:

* `--max-iters`
* `--max-candidates`
* `--max-diff-lines`
* `--timeout-per-command`
* `--max-total-runtime-seconds`
* stop on repeated identical failure
* stop on dangerous command request
* stop when patch touches forbidden paths
* require approval for dependency additions
* require approval for large diffs

Failure-aware retry strategy:

```text
LINT failure:
  request minimal lint-focused patch

TYPECHECK failure:
  request type-focused patch

TEST failure:
  request semantic fix

ENVIRONMENT failure:
  stop and report setup issue

SECURITY failure:
  stop immediately

POLICY_VIOLATION:
  stop or request explicit approval
```

Never interpret “run until fixed” as permission for unlimited execution.

---

### Phase 6: Observability

Implement local observability before advanced autonomy.

Store each run in SQLite and JSONL traces.

Minimum metadata:

* repo path
* git commit
* goal
* policy hash
* iteration
* model used
* commands run
* command duration
* exit code
* stdout/stderr path
* failure classification
* patch path
* final status
* timestamp

Suggested local artifact directory:

```text
.local-swe/
  runs/
  patches/
  logs/
  policies/
```

OpenTelemetry hooks may be added later, but must be optional and disabled by default.

---

## Required Commands Before Completion

Before considering a change complete, run:

```bash
uv run pytest
uv run ruff check .
```

When CLI behavior is changed, also run:

```bash
uv run local-swe --help
```

When policy compilation is changed, also run:

```bash
uv run local-swe compile-policy --repo examples/fixture_python_repo
```

When validation behavior is changed, also run:

```bash
uv run local-swe validate --repo examples/fixture_python_repo
```

If any command fails, do not claim completion.
Fix the failure or clearly report why it cannot be fixed.

---

## Coding Style

Use:

* Python 3.11+
* Pydantic v2 for schemas
* Typer for CLI
* pathlib over raw string paths
* explicit exceptions
* small modules
* typed function signatures
* structured return objects
* clear error messages

Avoid:

* hidden global state
* broad `except Exception` without re-raising or classification
* shell=True unless unavoidable
* mutable default arguments
* large functions
* prompt-only logic without tests
* silent cleanup failures
* implicit network access

---

## Testing Expectations

New code must include tests.

Tests should cover:

* missing files
* invalid config
* temporary git repositories
* command timeout
* non-zero exit codes
* policy compilation fallbacks
* cleanup of ephemeral worktrees
* malformed model responses
* fake model client behavior
* patch application failure
* forbidden command detection
* approval-required operation detection

Tests must not require:

* a real LLM
* internet access
* Docker
* sudo
* GPU
* external paid services

Use fixture repositories under `examples/` or temporary repositories created in pytest.

---

## Safety Rules

Forbidden by default:

* deleting files outside ephemeral worktrees
* force pushing
* installing global packages
* reading secrets
* uploading repository contents
* running arbitrary shell from model output
* modifying the target repository directly
* editing generated lockfiles without explicit dependency-change approval
* changing license files without explicit approval
* adding dependencies without explicit approval
* disabling tests to make validation pass
* weakening security checks to make validation pass
* suppressing type errors without justification

Dangerous commands must be blocked or require approval.

Examples of dangerous commands:

```text
rm -rf
git push --force
sudo
curl | bash
wget | bash
chmod -R 777
chown -R
pip install --user
npm install -g
docker system prune
```

Model output must never be executed directly as shell commands.
Model output may propose patches, but commands must come from policy or explicit user approval.

---

## Patch Rules

Patch generation must follow these rules:

* Generate unified diffs.
* Apply patches only to ephemeral worktrees.
* Reject patches that touch forbidden paths.
* Reject patches that exceed `--max-diff-lines`.
* Reject patches that remove tests without justification.
* Reject patches that weaken validation commands.
* Reject patches that alter policy to make failures disappear.
* Reject patches that add dependencies without approval.
* Save every candidate patch as an artifact.
* Validate every candidate independently.

Preferred patch flow:

```text
model proposes patch
  -> parse patch
  -> static policy check
  -> apply to fresh worktree
  -> run validation
  -> classify result
  -> save evidence
```

---

## Policy Compiler Requirements

The policy compiler should infer commands conservatively.

Examples:

For Python:

```text
pyproject.toml with ruff -> ruff check .
pyproject.toml with pytest -> pytest
pyproject.toml with mypy -> mypy .
pyproject.toml with pyright -> pyright
```

For JavaScript / TypeScript:

```text
package.json scripts.lint -> npm run lint
package.json scripts.test -> npm test
package.json scripts.typecheck -> npm run typecheck
```

For Rust:

```text
Cargo.toml -> cargo check
Cargo.toml -> cargo test
Cargo.toml -> cargo clippy -- -D warnings
```

For Makefile:

```text
make lint
make test
make typecheck
make format-check
```

Prefer existing project commands over invented commands.
If uncertain, compile a soft suggestion rather than a hard gate.

---

## Failure Classification Guidance

Classify failures by symptoms.

Examples:

```text
ModuleNotFoundError, missing executable, dependency install failure:
  ENVIRONMENT

SyntaxError, compile error, build backend failure:
  BUILD

ruff, eslint, clippy style failure:
  LINT

mypy, pyright, tsc type failure:
  TYPECHECK

pytest, cargo test, npm test failure:
  TEST

osv-scanner, pip-audit, CodeQL, secret scanner failure:
  SECURITY

timeout, OOM, killed process:
  RESOURCE_EXHAUSTION

forbidden path touched, dangerous command requested:
  POLICY_VIOLATION
```

Do not retry blindly.
Retry strategy must depend on failure class.

---

## Model Usage Rules

The model should be used only when deterministic code cannot handle the task.

Good model tasks:

* summarizing validation failure
* fault localization
* proposing a patch
* explaining a failed patch
* generating a small regression test
* classifying ambiguous failures

Bad model tasks:

* deciding whether to skip tests
* directly executing commands
* modifying policy to bypass gates
* choosing to ignore security failures
* reading arbitrary secrets
* making irreversible repository changes

All model responses used for control flow must be schema-validated.

---

## Default Model Profile

Provide a sample model profile for local inference:

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

The actual model runtime is outside the MVP.
The controller should only require an OpenAI-compatible endpoint.

---

## CLI Expectations

Target CLI commands:

```bash
local-swe --help

local-swe compile-policy \
  --repo /path/to/repo

local-swe validate \
  --repo /path/to/repo \
  --policy /path/to/policy.compiled.json

local-swe repair \
  --repo /path/to/repo \
  --goal "Fix failing tests" \
  --max-iters 1

local-swe runs list

local-swe runs show <run-id>
```

All commands should produce human-readable output.
Where useful, add `--json` for machine-readable output.

---

## Completion Criteria

A task is complete only when:

1. Code is implemented.
2. Tests are added or updated.
3. Required commands pass.
4. Safety rules are preserved.
5. The target repository is not modified directly.
6. Failure cases are handled explicitly.
7. The README or relevant docs are updated if behavior changes.

Do not mark work complete if validation was skipped.

---

## Non-Goals for the MVP

Do not implement these in the initial version:

* autonomous infinite repair loop
* fine-tuning
* model training data flywheel
* web dashboard
* GitHub App
* merge queue
* Kubernetes deployment
* cloud-hosted inference
* multi-agent negotiation
* automatic dependency upgrades
* automatic license changes
* production deployment
* self-modifying controller code

These can be considered later after the deterministic core is stable.

---

## Final Instruction

Build the system as a reliable software tool first and an AI agent second.

The correct architecture is:

```text
policy compiler
  -> repo scanner
  -> ephemeral worktree
  -> validator
  -> failure classifier
  -> local model client
  -> patch proposer
  -> patch validator
  -> bounded retry
  -> observability
```

Do not skip the deterministic core.
Do not trust the model without validation.
Do not allow unbounded autonomy.
