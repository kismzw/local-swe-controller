# ARCHITECTURE.md

# Local SWE Controller Architecture

## 1. Overview

`local-swe-controller` is a local-first software engineering controller for coding-agent repair workflows.

It is designed to operate on arbitrary target repositories by:

1. scanning repository conventions,
2. compiling them into machine-readable policy,
3. creating isolated git worktrees,
4. running deterministic validation,
5. calling a local OpenAI-compatible model endpoint only when needed,
6. generating patch candidates,
7. validating each candidate independently,
8. storing traces, logs, patches, and final results,
9. stopping safely under bounded autonomy.

This project is not a chatbot and not a free-form autonomous coding agent.

The central architectural principle is:

> Verifier first, model second.

The LLM proposes candidates.
The controller validates them.
The policy gate decides what is allowed.
The user approves dangerous or high-impact operations.

---

## 2. Design Goals

## 2.1 Primary Goals

The system should:

* support arbitrary local git repositories,
* never modify the target repository directly,
* compile repo instructions into structured policy,
* run validation commands in ephemeral worktrees,
* classify validation failures,
* connect to local OpenAI-compatible model endpoints,
* generate and validate patch candidates,
* preserve full evidence for every run,
* stop safely when budget, policy, or convergence limits are reached.

## 2.2 Non-Goals for MVP

The MVP should not include:

* infinite autonomous repair loops,
* self-modifying controller code,
* fine-tuning,
* training data flywheel,
* multi-agent negotiation,
* mandatory Docker,
* Kubernetes,
* GitHub App integration,
* web dashboard,
* cloud inference dependency,
* automatic dependency upgrades,
* automatic license changes,
* direct merge or push behavior.

These may be considered later after the deterministic core is stable.

---

## 3. Core Architecture

The system is organized as a staged pipeline.

```text
Issue / Goal / Failing CI
        ↓
Repo Scanner
        ↓
Policy Compiler
        ↓
Repo Understanding
        ↓
Baseline Validator
        ↓
Failure Classifier
        ↓
Model Router
        ↓
Patch Proposer
        ↓
Patch Policy Check
        ↓
Ephemeral Worktree Validator
        ↓
Patch Selector
        ↓
Artifact Store
        ↓
Human Review / PR / Manual Apply
```

The initial implementation should emphasize deterministic execution.
Agentic behavior is introduced only after the validator, sandbox, and policy gates are reliable.

---

## 4. Major Components

## 4.1 CLI Layer

Location:

```text
src/local_swe_controller/cli.py
```

Responsibilities:

* expose user-facing commands,
* parse CLI arguments,
* load configuration,
* call controller services,
* return human-readable and optional JSON output.

Target commands:

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

The CLI should be thin.
Business logic should live in service modules.

---

## 4.2 Repo Scanner

Location:

```text
src/local_swe_controller/repo/
```

or initially:

```text
src/local_swe_controller/policy/compiler.py
```

Responsibilities:

* verify that the target path is a git repository,
* detect repository root,
* read project metadata,
* detect languages and frameworks,
* discover CI files,
* discover instruction files,
* discover test and build commands.

Inputs:

* target repo path.

Outputs:

* `RepoMetadata`.

Example fields:

```python
class RepoMetadata(BaseModel):
    root: Path
    git_commit: str | None
    branch: str | None
    has_contributing: bool
    has_agents_md: bool
    languages: list[str]
    package_managers: list[str]
    ci_files: list[Path]
    project_files: list[Path]
```

The scanner must not modify the target repository.

---

## 4.3 Policy Compiler

Location:

```text
src/local_swe_controller/policy/
  compiler.py
  schema.py
```

Responsibilities:

* read repository conventions,
* infer setup, lint, typecheck, test, security, and format commands,
* detect forbidden commands and approval-required operations,
* produce a structured compiled policy.

Input files may include:

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

Output:

```text
.local-swe/policies/policy.compiled.json
```

Suggested schema:

```python
class CompiledPolicy(BaseModel):
    repo_root: Path
    policy_version: str
    source_files: list[Path]
    setup_commands: list[CommandSpec]
    format_commands: list[CommandSpec]
    lint_commands: list[CommandSpec]
    typecheck_commands: list[CommandSpec]
    test_commands: list[CommandSpec]
    security_commands: list[CommandSpec]
    hard_gates: list[str]
    soft_gates: list[str]
    forbidden_commands: list[str]
    forbidden_paths: list[str]
    approval_required_operations: list[str]
    generated_at: datetime
```

The MVP policy compiler should be deterministic and heuristic-based.

The LLM may later assist with ambiguous instruction extraction, but LLM-derived policy must still be schema-validated and reviewed before use.

---

## 4.4 Command Model

Location:

```text
src/local_swe_controller/sandbox/commands.py
```

Command execution must be explicit and policy-aware.

Suggested schema:

```python
class CommandSpec(BaseModel):
    name: str
    command: list[str]
    cwd: Path | None = None
    timeout_seconds: int = 300
    gate: Literal[
        "setup",
        "format",
        "lint",
        "typecheck",
        "test",
        "security",
        "custom"
    ]
    required: bool = True
```

Avoid raw shell strings where possible.
Prefer argument lists.

Allowed:

```python
["uv", "run", "pytest"]
["ruff", "check", "."]
["npm", "test"]
["cargo", "test"]
```

Avoid unless unavoidable:

```python
"bash -lc '...'"
```

Forbidden by default:

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

Model output must never become an executable command directly.

---

## 4.5 Ephemeral Worktree Sandbox

Location:

```text
src/local_swe_controller/sandbox/
  worktree.py
  commands.py
```

Responsibilities:

* create a temporary git worktree,
* run commands inside it,
* capture outputs,
* clean up by default,
* preserve worktree only with `--keep-worktree`.

Why git worktrees:

* target repository remains untouched,
* patch candidates can be validated independently,
* cleanup is simple,
* no Docker requirement for MVP.

Worktree lifecycle:

```text
target repo
   ↓
create temporary git worktree
   ↓
run setup / validation / patch
   ↓
collect logs and results
   ↓
cleanup unless --keep-worktree
```

Rules:

* never run validation in the target repository,
* never apply model patches to the target repository,
* every candidate patch gets a fresh worktree,
* worktree path must be inside a controlled temporary directory,
* cleanup errors should be reported but not hidden.

---

## 4.6 Validator

Location:

```text
src/local_swe_controller/validation/
  runner.py
  classifier.py
```

Responsibilities:

* run commands from compiled policy,
* enforce timeouts,
* collect stdout, stderr, exit code, duration,
* classify failures,
* produce a validation report.

Suggested output:

```python
class CommandResult(BaseModel):
    name: str
    command: list[str]
    cwd: Path
    exit_code: int
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path
    timed_out: bool = False

class ValidationReport(BaseModel):
    status: Literal["PASS", "FAIL", "ERROR"]
    failure_class: FailureClass | None
    command_results: list[CommandResult]
    started_at: datetime
    finished_at: datetime
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

Failure classification should be deterministic first.

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

---

## 4.7 Local Model Client

Location:

```text
src/local_swe_controller/llm/
  client.py
  fake.py
```

Responsibilities:

* connect to OpenAI-compatible local model endpoints,
* support chat completions,
* support structured JSON validation,
* retry transient connection failures,
* expose fake clients for tests.

Default endpoint:

```text
http://localhost:8000/v1
```

The controller should not require cloud inference.

Suggested config:

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

MVP should support fake model output so tests do not require GPU or model runtime.

---

## 4.8 Model Router

Location:

```text
src/local_swe_controller/llm/router.py
```

Can be added after the first model client is stable.

Responsibilities:

* choose model profile by task,
* enforce max context size,
* enforce max output tokens,
* track model usage,
* fall back to smaller or safer models when needed.

Example routing:

```text
policy_extraction:
  fast_triage_model

failure_classification:
  fast_triage_model

fault_localization:
  main_patch_model

patch_generation:
  main_patch_model

patch_review:
  main_patch_model
```

The router should be simple in the MVP.
Do not over-engineer dynamic model routing early.

---

## 4.9 Patch Proposer

Location:

```text
src/local_swe_controller/repair/
  proposer.py
  patch.py
```

Responsibilities:

* prepare minimal context,
* ask the model for a unified diff,
* parse the patch,
* validate patch structure,
* reject dangerous changes before applying,
* save candidate patch as artifact.

Patch rules:

* patch must be unified diff,
* patch must apply cleanly,
* patch must not exceed max diff size,
* patch must not touch forbidden paths,
* patch must not remove tests without justification,
* patch must not weaken validation commands,
* patch must not modify policy to bypass failures,
* patch must not add dependencies without approval.

MVP should support a single patch candidate.
Multiple candidates can be added later.

---

## 4.10 Patch Validator

Location:

```text
src/local_swe_controller/repair/
  validator.py
```

Responsibilities:

* create fresh worktree,
* apply candidate patch,
* run policy validation,
* classify results,
* save validation report,
* return pass/fail decision.

Every patch candidate must be validated independently.

Validation flow:

```text
candidate patch
   ↓
static patch policy check
   ↓
fresh worktree
   ↓
apply patch
   ↓
run validation
   ↓
save result
   ↓
accept / reject
```

---

## 4.11 Repair Controller

Location:

```text
src/local_swe_controller/repair/
  controller.py
```

Responsibilities:

* orchestrate compile-policy,
* run baseline validation,
* collect failure evidence,
* request patch proposal,
* validate patch,
* stop or retry according to budgets.

MVP flow:

```text
compile policy
   ↓
baseline validation
   ↓
if pass: NO_ACTION_NEEDED
   ↓
if fail: collect logs
   ↓
generate one patch
   ↓
validate patch in fresh worktree
   ↓
if pass: save patch and report success
   ↓
if fail: classify and stop
```

Default `--max-iters` should be `1`.

Bounded autonomy can later extend this with:

* max iterations,
* max candidates,
* max diff lines,
* max total runtime,
* repeated failure detection,
* approval gates,
* failure-aware retry.

---

## 4.12 Storage and Artifact Store

Location:

```text
src/local_swe_controller/storage/
  sqlite.py
  artifacts.py
```

Local artifact directory:

```text
.local-swe/
  runs/
  patches/
  logs/
  policies/
```

Store every run.

Minimum metadata:

```text
run_id
repo_path
git_commit
goal
policy_hash
iteration
model_used
commands_run
command_durations
exit_codes
stdout/stderr paths
failure_classification
patch_path
final_status
timestamp
```

MVP can use:

* SQLite for run index,
* JSONL for traces,
* files for logs and patches.

OpenTelemetry can be added later but should be optional and disabled by default.

---

## 5. Data Flow

## 5.1 Policy Compilation Flow

```text
User runs:
  local-swe compile-policy --repo /target/repo

System:
  validate repo path
  scan repo files
  detect languages and tools
  infer commands
  infer gates
  infer forbidden operations
  write compiled policy
  print summary
```

Output:

```text
.local-swe/policies/policy.compiled.json
```

---

## 5.2 Validation Flow

```text
User runs:
  local-swe validate --repo /target/repo

System:
  compile or load policy
  create ephemeral worktree
  run commands
  classify failures
  save logs
  cleanup worktree
  return validation report
```

---

## 5.3 Repair Flow

```text
User runs:
  local-swe repair --repo /target/repo --goal "Fix failing tests"

System:
  compile policy
  run baseline validation
  if pass:
      return NO_ACTION_NEEDED
  else:
      collect failure evidence
      build minimal model context
      request unified diff patch
      validate patch policy
      create fresh worktree
      apply patch
      run validation
      save patch and report
```

---

## 6. Context Strategy

Do not stuff the entire repository into the model prompt.

Use minimal, evidence-based context:

* goal,
* compiled policy summary,
* failing command,
* relevant stdout/stderr excerpts,
* stack trace,
* likely files,
* selected source snippets,
* test snippets,
* current git diff if any.

Context should be generated deterministically where possible.

The model should not decide which commands to run.
Commands come from policy or explicit user approval.

---

## 7. Safety Model

The safety model has three layers.

## 7.1 Input Safety

Inputs include:

* user goal,
* target repo path,
* repo instruction files,
* model profile config,
* policy config.

Risks:

* prompt injection in repo files,
* malicious `AGENTS.md`,
* malicious `CONTRIBUTING.md`,
* poisoned CI commands,
* secret leakage.

Mitigation:

* repo files are data, not trusted instructions,
* policy compiler extracts limited structured fields,
* command allowlist and denylist,
* approval gates,
* no automatic secret reading,
* no arbitrary upload.

---

## 7.2 Action Safety

Actions include:

* creating worktrees,
* running commands,
* applying patches,
* reading files,
* writing artifacts.

Rules:

* never modify target repo directly,
* never execute shell from model output,
* never run forbidden commands,
* never push changes,
* never install global packages,
* never delete files outside controlled temp dirs,
* require approval for dangerous operations.

---

## 7.3 Output Safety

Outputs include:

* patches,
* logs,
* reports,
* traces.

Rules:

* do not print secrets if detected,
* do not include excessive file contents in reports,
* store logs locally,
* make cloud integrations opt-in,
* keep artifacts inspectable.

---

## 8. Bounded Autonomy

The system may retry only under explicit budgets.

Budget controls:

```text
--max-iters
--max-candidates
--max-diff-lines
--timeout-per-command
--max-total-runtime-seconds
```

Stop conditions:

```text
validation passes
budget exhausted
same failure repeats
security failure
policy violation
dangerous command request
large diff requires approval
dependency change requires approval
environment cannot be reproduced
```

Retry strategy should depend on failure class.

Examples:

```text
LINT:
  request minimal lint-only patch

TYPECHECK:
  request type-focused patch

TEST:
  request semantic fix

ENVIRONMENT:
  stop and report setup issue

SECURITY:
  stop immediately

POLICY_VIOLATION:
  stop or request explicit approval
```

Never interpret “run until fixed” as permission for unlimited execution.

---

## 9. Observability

Every run should be reproducible and inspectable.

Track:

* repo path,
* git commit,
* goal,
* policy hash,
* command results,
* stdout/stderr paths,
* model profile,
* token usage if available,
* patch candidates,
* validation results,
* failure classification,
* final status.

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

For MVP:

* SQLite run index,
* JSONL traces,
* local log files.

Future:

* OpenTelemetry,
* Prometheus,
* Langfuse or MLflow,
* dashboard.

---

## 10. Configuration

## 10.1 Default Policy Config

Example:

```yaml
defaults:
  timeout_seconds: 300
  max_diff_lines: 500
  keep_worktree: false

forbidden_commands:
  - "rm -rf"
  - "git push --force"
  - "sudo"
  - "curl | bash"
  - "wget | bash"
  - "chmod -R 777"
  - "chown -R"
  - "npm install -g"
  - "pip install --user"
  - "docker system prune"

approval_required_operations:
  - "dependency_addition"
  - "lockfile_change"
  - "license_change"
  - "large_diff"
  - "security_sensitive_code"
```

## 10.2 Model Profile Config

Example:

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

---

## 11. Suggested Package Layout

```text
src/local_swe_controller/
  __init__.py

  cli.py
  config.py
  exceptions.py

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

  llm/
    __init__.py
    client.py
    fake.py
    router.py

  repair/
    __init__.py
    controller.py
    proposer.py
    patch.py
    validator.py

  storage/
    __init__.py
    sqlite.py
    artifacts.py

  telemetry/
    __init__.py
    trace.py
```

Tests:

```text
tests/
  test_cli.py
  test_policy_compiler.py
  test_worktree.py
  test_command_runner.py
  test_failure_classifier.py
  test_llm_fake.py
  test_repair_controller.py
```

---

## 12. MVP Implementation Order

Implement in this order:

1. project skeleton,
2. CLI shell,
3. config schemas,
4. policy schema,
5. deterministic policy compiler,
6. ephemeral worktree manager,
7. command runner,
8. validation runner,
9. failure classifier,
10. local artifact storage,
11. fake LLM client,
12. OpenAI-compatible LLM client,
13. patch parser,
14. one-shot repair loop,
15. bounded retry,
16. SQLite run tracking,
17. model router,
18. optional OpenTelemetry.

Do not implement patch generation before the validator works.

---

## 13. Testing Strategy

Tests must not require:

* a real LLM,
* GPU,
* internet,
* Docker,
* sudo,
* cloud credentials.

Use:

* temporary git repositories,
* fixture repos,
* fake model clients,
* fake command runners where appropriate.

Test categories:

```text
unit:
  schema validation
  command classification
  forbidden command detection
  policy inference

integration:
  temporary git repo
  worktree creation and cleanup
  validation command execution
  repair loop with fake model

safety:
  forbidden commands blocked
  target repo not modified
  dangerous patches rejected
  dependency changes require approval

regression:
  known failure logs map to expected failure classes
```

---

## 14. Security and Supply Chain Considerations

MVP:

* local-only execution,
* no cloud dependency,
* command denylist,
* command allowlist from compiled policy,
* worktree isolation,
* no direct target repo modification,
* artifact preservation.

Future:

* secret scanning,
* OSV scanner,
* pip-audit,
* CodeQL,
* OpenSSF Scorecard,
* SPDX / REUSE,
* SBOM generation,
* SLSA provenance,
* Sigstore signing.

Security checks should be hard gates when enabled.

---

## 15. Future Architecture

After MVP, possible extensions include:

## 15.1 LangGraph Controller

Replace or wrap the repair controller with a durable state machine.

Nodes:

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

This should be added only after the simple deterministic controller is stable.

## 15.2 Multi-Candidate Patch Factory

Generate multiple patch candidates and validate each independently.

Features:

* candidate ranking,
* duplicate patch detection,
* failure-aware retry,
* regression test generation,
* patch minimization.

## 15.3 Test Generation

Add model-assisted test generation.

Rules:

* generated tests must be validated,
* tests must not encode implementation details too strongly,
* generated tests must not weaken existing tests,
* test-only changes require review if they remove coverage.

## 15.4 GitHub Integration

Possible future features:

* PR creation,
* PR comment summary,
* check run annotations,
* merge queue integration,
* Danger-style review comments.

This is not part of MVP.

## 15.5 Observability Integrations

Possible future features:

* OpenTelemetry,
* Prometheus,
* Alertmanager,
* Langfuse,
* MLflow,
* dashboard.

---

## 16. Architectural Invariants

These invariants must remain true:

1. The target repository is never modified directly.
2. Every patch candidate is validated in a fresh worktree.
3. Commands are never executed directly from model output.
4. Policy is structured and validated.
5. Dangerous operations require approval.
6. Autonomy is bounded by explicit budgets.
7. Validation evidence is preserved.
8. Tests do not require a real LLM.
9. Local execution is the default.
10. The validator has final authority over patch acceptance.

If a proposed change violates these invariants, reject it or require explicit architectural review.

---

## 17. Summary

The architecture is intentionally conservative.

The intended system is:

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

The controller should become more agentic only after the deterministic core is reliable.

Build the system as a reliable software tool first and an AI agent second.
