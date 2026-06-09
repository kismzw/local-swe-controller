# PRODUCT_SPEC.md

# Local SWE Controller Product Specification

## 1. Product Summary

`local-swe-controller` is a local-first software engineering controller for coding-agent repair workflows.

It helps a developer use local LLMs to inspect, validate, and repair arbitrary software repositories while preserving safety, reproducibility, and human control.

The system is not a chatbot.
The system is not an unrestricted autonomous coding agent.
The system is a **verifier-first controller**.

The core idea is:

> The model proposes.
> The validator verifies.
> The policy gate constrains.
> The user approves high-impact actions.

The product should eventually support a workflow where a user can point the controller at any local git repository and ask it to:

```bash
local-swe repair --repo /path/to/repo --goal "Fix failing tests"
```

The controller should then:

1. inspect the repository,
2. compile project rules into structured policy,
3. run validation in an isolated worktree,
4. classify failures,
5. ask a local model for patch candidates,
6. apply candidates only in fresh worktrees,
7. run validation again,
8. save evidence and patches,
9. stop safely when validation passes or limits are reached.

---

## 2. Problem Statement

Modern coding agents can generate patches, but they often fail in practical repository maintenance because they:

* ignore repository-specific contribution rules,
* run arbitrary or unsafe commands,
* modify the target repository directly,
* lack reliable test and validation gates,
* retry blindly after failures,
* lose evidence about what happened,
* overfit to passing tests,
* disable or weaken checks,
* consume unbounded compute,
* cannot distinguish environment failures from semantic failures.

For serious software engineering work, the main bottleneck is not simply patch generation.
The main bottleneck is **safe, repeatable, policy-aware validation**.

This product solves that by building a deterministic controller around local coding models.

---

## 3. Target Users

## 3.1 Primary User

A developer or researcher who wants to use a local LLM on a workstation to maintain and repair repositories.

Typical environment:

* Linux workstation,
* local git repositories,
* local model runtime,
* no cloud dependency required,
* no sudo assumption,
* optional GPU acceleration.

The initial user may run a high-end local GPU such as an RTX 5090, but the controller itself must not require GPU for tests or basic validation.

## 3.2 Secondary Users

Potential future users include:

* maintainers of internal research repositories,
* ML engineers managing experiment code,
* teams that need offline or private coding assistance,
* developers who want coding-agent output but with strict validation gates,
* researchers building SWE-agent experiments.

---

## 4. Product Goals

## 4.1 MVP Goals

The MVP should provide a CLI-first tool that can:

1. initialize and run as a Python package,
2. inspect a target git repository,
3. compile a machine-readable policy from common repo files,
4. create an ephemeral git worktree,
5. run validation commands from policy,
6. classify validation failures,
7. save logs and artifacts locally,
8. expose fake LLM clients for tests,
9. expose an OpenAI-compatible local model client,
10. perform a one-shot repair loop with a model-proposed patch,
11. validate the patch in a fresh worktree,
12. stop safely after one iteration by default.

The MVP must be useful without implementing a full multi-agent system.

## 4.2 Long-Term Goals

The long-term product should support:

* bounded retry loops,
* multi-candidate patch generation,
* model routing,
* test generation,
* security and supply-chain gates,
* local observability,
* OpenTelemetry integration,
* optional GitHub PR generation,
* custom repository benchmarks,
* failure corpus generation,
* LangGraph or equivalent durable orchestration.

---

## 5. Non-Goals

The MVP must not attempt to implement:

* unlimited autonomous repair,
* self-modifying controller code,
* model fine-tuning,
* automatic merge or push,
* mandatory Docker,
* Kubernetes deployment,
* web dashboard,
* cloud-hosted inference,
* multi-agent debate,
* automatic dependency upgrades,
* automatic license changes,
* automatic security policy weakening,
* automatic test disabling,
* production deployment system.

The product should be built as a reliable local software tool before becoming a more agentic system.

---

## 6. Core Product Principles

## 6.1 Verifier First

The validator is the source of truth.

The model may propose:

* summaries,
* candidate patches,
* likely fault locations,
* test ideas,
* failure explanations.

The model must not decide:

* whether to skip tests,
* whether to ignore policy,
* whether to run arbitrary commands,
* whether to accept a failed patch,
* whether to bypass security checks.

## 6.2 Bounded Autonomy

The system may act autonomously only within explicit limits.

Examples:

```text
--max-iters
--max-candidates
--max-diff-lines
--timeout-per-command
--max-total-runtime-seconds
```

The system must stop on:

* security failure,
* policy violation,
* repeated identical failure,
* environment failure that cannot be reproduced,
* budget exhaustion,
* dangerous command request,
* approval-required change.

## 6.3 Local First

The default system must run locally.

It should not require:

* internet,
* cloud model APIs,
* Docker,
* sudo,
* GPU,
* external paid services.

Local model endpoints may be used through OpenAI-compatible APIs.

## 6.4 Target Repository Safety

The target repository must never be modified directly.

All validation and patch application must occur in ephemeral git worktrees.

## 6.5 Evidence Preservation

Every run should preserve enough evidence for a human to inspect what happened.

Evidence includes:

* policy snapshot,
* command outputs,
* validation results,
* failure class,
* candidate patches,
* model metadata,
* final status.

---

## 7. Primary User Stories

## 7.1 Compile Repository Policy

As a developer, I want to compile a target repository’s contribution and CI rules into a structured policy so that the controller can run validation consistently.

Command:

```bash
local-swe compile-policy --repo /path/to/repo
```

Expected behavior:

* verify the repo is a git repository,
* inspect common metadata files,
* infer commands,
* infer hard and soft gates,
* infer forbidden operations,
* write `policy.compiled.json`,
* print a summary.

---

## 7.2 Validate a Repository

As a developer, I want to validate a target repository in an isolated worktree so that I can know whether it currently passes its own rules.

Command:

```bash
local-swe validate --repo /path/to/repo
```

Expected behavior:

* compile or load policy,
* create ephemeral worktree,
* run validation commands,
* capture logs,
* classify failures,
* clean up worktree by default,
* report status.

---

## 7.3 One-Shot Repair

As a developer, I want the controller to propose and validate one patch for a failing repository using a local model.

Command:

```bash
local-swe repair --repo /path/to/repo --goal "Fix failing tests"
```

Expected behavior:

* run baseline validation,
* if validation passes, exit with `NO_ACTION_NEEDED`,
* if validation fails, collect evidence,
* call local model endpoint,
* request a unified diff patch,
* reject dangerous patch changes,
* apply patch in fresh worktree,
* validate patch,
* save patch artifact,
* report success or failure.

Default behavior:

```text
--max-iters 1
```

---

## 7.4 Inspect Past Runs

As a developer, I want to inspect previous controller runs.

Commands:

```bash
local-swe runs list
local-swe runs show <run-id>
```

Expected behavior:

* show run metadata,
* show final status,
* show policy hash,
* show failure class,
* show patch path if available,
* show log locations.

---

## 8. MVP Functional Requirements

## 8.1 CLI

The product must expose:

```bash
local-swe --help
local-swe compile-policy --repo /path/to/repo
local-swe validate --repo /path/to/repo
local-swe repair --repo /path/to/repo --goal "..."
local-swe runs list
local-swe runs show <run-id>
```

The CLI should support human-readable output.

Where appropriate, add:

```bash
--json
```

for machine-readable output.

---

## 8.2 Policy Compilation

The policy compiler must inspect, when present:

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

It must produce a structured policy containing:

```text
setup commands
format commands
lint commands
typecheck commands
test commands
security commands
hard gates
soft gates
forbidden commands
forbidden paths
approval-required operations
```

The MVP policy compiler should be deterministic and heuristic-based.

LLM-assisted policy extraction is allowed only in a later version.

---

## 8.3 Worktree Sandbox

The system must:

* create ephemeral git worktrees,
* run commands only inside worktrees,
* apply candidate patches only inside worktrees,
* clean up worktrees by default,
* support `--keep-worktree` for debugging,
* report cleanup failures.

The system must not modify the target repository directly.

---

## 8.4 Command Runner

The command runner must:

* run explicit command specs,
* enforce timeout,
* capture stdout,
* capture stderr,
* capture exit code,
* capture duration,
* avoid shell execution where possible,
* block forbidden commands.

The model must never directly supply executable commands.

---

## 8.5 Validation

The validator must:

* run commands from compiled policy,
* stop or continue according to gate configuration,
* save logs,
* classify failure,
* produce a validation report.

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

---

## 8.6 Local Model Client

The system must support an OpenAI-compatible local model endpoint.

Configurable fields:

```text
base_url
api_key
model
temperature
max_output_tokens
context_window
```

Default endpoint:

```text
http://localhost:8000/v1
```

Tests must use a fake model client.

The MVP must not require a real model.

---

## 8.7 Patch Handling

The system must support model-proposed unified diff patches.

Patch handling must:

* parse unified diffs,
* reject malformed patches,
* reject patches touching forbidden paths,
* reject patches exceeding max diff lines,
* reject patches that modify policy to bypass validation,
* require approval for dependency changes,
* require approval for lockfile changes,
* save every candidate patch.

---

## 8.8 Artifact Storage

The system must save run artifacts locally.

Suggested layout:

```text
.local-swe/
  runs/
  patches/
  logs/
  policies/
```

Minimum run metadata:

```text
run_id
repo_path
git_commit
goal
policy_hash
iteration
model_used
commands_run
exit_codes
durations
stdout_path
stderr_path
failure_class
patch_path
final_status
timestamp
```

---

## 9. MVP Acceptance Criteria

The MVP is acceptable when the following commands work:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run local-swe --help
uv run local-swe compile-policy --repo examples/fixture_python_repo
uv run local-swe validate --repo examples/fixture_python_repo
```

Additionally:

1. Tests do not require a real LLM.
2. Tests do not require GPU.
3. Tests do not require internet.
4. Tests do not require Docker.
5. Tests do not require sudo.
6. The target repo is never modified directly.
7. Validation runs in ephemeral worktrees.
8. Logs are captured.
9. Failures are classified.
10. Policy compilation produces a valid JSON file.

For the first repair MVP, this must also work with a fake model:

```bash
uv run local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

---

## 10. Example Product Workflow

## 10.1 User Runs Policy Compilation

```bash
local-swe compile-policy --repo ~/projects/example
```

Expected output:

```text
Repository: ~/projects/example
Languages: Python
Policy: .local-swe/policies/policy.compiled.json

Detected gates:
- lint: uv run ruff check .
- test: uv run pytest
```

---

## 10.2 User Runs Validation

```bash
local-swe validate --repo ~/projects/example
```

Expected output:

```text
Validation status: FAIL
Failure class: TEST
Failed command: uv run pytest
Logs:
  .local-swe/runs/2026-06-04T120000/logs/pytest.stderr
```

---

## 10.3 User Runs Repair

```bash
local-swe repair --repo ~/projects/example --goal "Fix failing tests"
```

Expected output:

```text
Baseline validation: FAIL
Failure class: TEST
Patch candidate: .local-swe/patches/run_abc123_candidate_1.patch
Patch validation: PASS
Final status: SUCCESS

Apply manually:
  git apply .local-swe/patches/run_abc123_candidate_1.patch
```

The MVP should not automatically modify the original target repository.

---

## 11. Safety Requirements

The system must block or require approval for:

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
dependency additions
lockfile changes
license changes
large diffs
security-sensitive code changes
```

The system must never:

* directly execute shell commands from model output,
* upload repository contents by default,
* read secrets intentionally,
* push changes,
* merge PRs,
* delete files outside controlled worktrees,
* weaken tests to make validation pass,
* weaken policy to make validation pass,
* hide validation failures.

---

## 12. Model Strategy

The product should support local model routing, but the MVP only needs one model profile and one fake client.

Recommended future local model setup:

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
  failure_classification: fast_triage_model
  fault_localization: main_patch_model
  patch_generation: main_patch_model
  patch_review: main_patch_model
```

The product should not depend on a specific model runtime.

Compatible runtimes may include:

* llama.cpp,
* Ollama,
* LM Studio,
* vLLM,
* SGLang,
* any OpenAI-compatible local server.

---

## 13. Performance and Resource Requirements

The MVP should be lightweight.

Expected constraints:

* no GPU required for tests,
* no model required for tests,
* validation command runtime controlled by timeout,
* logs stored on disk,
* no large in-memory repo loading,
* no whole-repo prompt stuffing.

The repair system should use minimal context:

```text
goal
compiled policy summary
failing command
stdout/stderr excerpts
stack traces
likely files
selected source snippets
selected test snippets
```

The model should not receive the entire repository unless explicitly requested.

---

## 14. UX Requirements

The CLI should be explicit and transparent.

Good CLI output should answer:

* What repository was inspected?
* What policy was compiled?
* What commands were run?
* Which command failed?
* What failure class was assigned?
* Where are the logs?
* Was a patch generated?
* Did the patch pass validation?
* How can the user manually apply the patch?

The tool should avoid vague output such as:

```text
Something went wrong.
```

Prefer:

```text
Validation failed at gate: TEST
Command: uv run pytest
Exit code: 1
Failure class: TEST
stderr: .local-swe/runs/<run-id>/logs/test.stderr
```

---

## 15. Product Phases

## Phase 0: Skeleton

Deliver:

* Python package,
* CLI entrypoint,
* config schemas,
* tests,
* README,
* fixture repo.

## Phase 1: Policy Compiler

Deliver:

* repo scanner,
* policy schema,
* deterministic command inference,
* compiled policy output,
* tests for Python/JS/Rust-style repos where feasible.

## Phase 2: Sandbox Validator

Deliver:

* git worktree creation,
* command runner,
* timeout handling,
* log capture,
* failure classification,
* cleanup.

## Phase 3: Model Client

Deliver:

* OpenAI-compatible local client,
* fake model client,
* structured response validation.

## Phase 4: One-Shot Repair

Deliver:

* baseline validation,
* model patch proposal,
* patch parsing,
* patch policy check,
* fresh worktree validation,
* patch artifact output.

## Phase 5: Bounded Retry

Deliver:

* iteration budget,
* candidate budget,
* diff budget,
* repeated failure detection,
* failure-aware retry,
* approval-required stops.

## Phase 6: Observability

Deliver:

* SQLite run index,
* JSONL traces,
* local artifact browser commands,
* optional OpenTelemetry hooks.

---

## 16. Risks and Mitigations

## 16.1 Risk: Model Generates Unsafe Commands

Mitigation:

* never execute commands from model output,
* commands must come from compiled policy or explicit approval,
* maintain forbidden command denylist.

## 16.2 Risk: Model Weakens Tests

Mitigation:

* reject patches that remove tests without justification,
* compare test files before/after,
* flag policy or CI weakening.

## 16.3 Risk: Target Repo Is Modified

Mitigation:

* all work happens in git worktrees,
* tests verify target repo remains unchanged,
* patch is saved for manual application.

## 16.4 Risk: Infinite Loop

Mitigation:

* default `--max-iters 1`,
* require explicit budget for retries,
* stop on repeated failures,
* stop on security and policy failures.

## 16.5 Risk: Environment Failure Misclassified as Code Failure

Mitigation:

* failure classifier distinguishes environment failures,
* missing executable and dependency errors stop early,
* report setup issues clearly.

## 16.6 Risk: Context Leakage

Mitigation:

* local inference by default,
* no cloud endpoint by default,
* explicit model config,
* logs stored locally,
* avoid uploading repo contents.

---

## 17. Future Extensions

Possible future work:

* LangGraph durable controller,
* multiple patch candidates,
* patch ranking,
* generated regression tests,
* secret scanning,
* OSV / pip-audit / CodeQL integration,
* OpenTelemetry traces,
* Prometheus metrics,
* local dashboard,
* GitHub PR creation,
* custom SWE benchmark runner,
* failure corpus generation,
* model performance evaluation,
* Aider or SWE-agent integration as patch proposer backend.

These should be added only after the MVP deterministic core is stable.

---

## 18. Final Product Definition

The product is successful when a developer can safely use local LLMs to repair code without trusting the LLM blindly.

A successful run should produce:

* a clear validation report,
* a failure classification,
* a candidate patch,
* a validation result for that patch,
* local logs and artifacts,
* no direct modification of the target repo,
* no unbounded autonomous behavior.

The final product should feel less like:

```text
an AI agent that might do anything
```

and more like:

```text
a strict local CI-aware repair controller with an LLM-powered patch proposal stage
```

Build the deterministic controller first.
Then add agentic behavior behind gates.
