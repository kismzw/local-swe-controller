# local-swe-controller

`local-swe-controller` is a local-first, verifier-first controller for validate and repair workflows on git repositories.

It is built around a simple rule:

> The model proposes. The validator verifies. The policy gate constrains.

The controller does not mutate the target repository directly. It compiles repository policy, runs validation in ephemeral worktrees, stores artifacts locally, and can optionally ask a local OpenAI-compatible model for patch candidates.

## Project Purpose

The project exists to make coding-agent repair workflows safer and more reproducible:

- compile repository instructions into structured policy
- validate in fresh git worktrees
- classify failures before retrying
- keep patches, logs, traces, and summaries as local artifacts
- stop on policy, security, environment, or budget limits

## Installation

Base install:

```bash
uv sync
```

Optional LangGraph support:

```bash
uv sync --extra orchestration
```

Optional OpenTelemetry support:

```bash
uv sync --extra observability
```

## Quickstart

Show the CLI:

```bash
.venv/bin/local-swe --help
```

Compile policy for the fixture repository:

```bash
.venv/bin/local-swe compile-policy --repo examples/fixture_python_repo
```

Validate the fixture repository:

```bash
.venv/bin/local-swe validate --repo examples/fixture_python_repo
```

Run a repair attempt with the fake model:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

List recorded runs:

```bash
.venv/bin/local-swe runs list
```

`runs list` shows finalized runs from the local artifact index.

## Core Commands

### `compile-policy`

Compile a deterministic policy snapshot from repository metadata:

```bash
.venv/bin/local-swe compile-policy \
  --repo examples/fixture_python_repo
```

Write the compiled JSON somewhere specific:

```bash
.venv/bin/local-swe compile-policy \
  --repo examples/fixture_python_repo \
  --output /tmp/policy.compiled.json
```

### `validate`

Validate a repository in an ephemeral git worktree:

```bash
.venv/bin/local-swe validate \
  --repo examples/fixture_python_repo
```

Validate against an existing compiled policy:

```bash
.venv/bin/local-swe validate \
  --repo examples/fixture_python_repo \
  --policy .local-swe/policies/some-policy.compiled.json
```

Keep the worktree for inspection:

```bash
.venv/bin/local-swe validate \
  --repo examples/fixture_python_repo \
  --keep-worktree
```

### `repair`

Run the bounded repair loop:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

Generate regression tests before patch generation:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake \
  --generate-tests
```

Use bounded retry and candidate limits:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake \
  --max-iters 2 \
  --max-candidates 2
```

Use the optional LangGraph orchestrator:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake \
  --orchestrator langgraph
```

### Run Inspection

List runs:

```bash
.venv/bin/local-swe runs list
```

Show one run:

```bash
.venv/bin/local-swe runs show 20260609T032038-98ed7a31
```

Generate a local PR summary from stored artifacts:

```bash
.venv/bin/local-swe runs pr-summary 20260609T032038-98ed7a31
```

Optionally create a GitHub PR from an existing branch:

```bash
GITHUB_TOKEN=... .venv/bin/local-swe runs create-pr \
  20260609T032038-98ed7a31 \
  --repo-owner example \
  --repo-name repo \
  --base main \
  --head repair/fix-tests
```

`local-swe-controller` never pushes a branch for you. `--head` must already exist.

### Benchmark Runner

Run local benchmark cases:

```bash
.venv/bin/local-swe bench run --cases "examples/benchmarks/*.yaml"
```

Show a saved benchmark run:

```bash
.venv/bin/local-swe bench show 20260609T032026-79ca164c
```

## Model Routing

Model routing is configured in `configs/model_profiles.example.yaml`.

Current behavior:

- routes are task-based, not free-form
- test routing must use `fake`
- non-local OpenAI-compatible endpoints are rejected
- explicit `--model-profile` overrides the routed profile for repair

The built-in fake profile is the default choice for tests and offline verification:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

For local model setup, copy the example profile file, point `base_url` at a local OpenAI-compatible server, and keep the endpoint on `localhost`, `127.0.0.1`, or `::1`.

## Security Gates

Security and policy checks run before candidate patches are accepted:

- forbidden commands are blocked
- shell launchers and shell metacharacters are blocked
- setup/bootstrap commands are skipped when default policy disallows network
- patch policy rejects or escalates risky changes such as dependency, lockfile, CI, license, test, and security-sensitive edits
- repair stops on policy, security, repeated failure, and environment failure conditions

See [docs/security.md](docs/security.md) and [docs/safety.md](docs/safety.md).

## Observability

Each validate or repair run stores local artifacts such as:

- `run.json`
- `summary.md`
- `trace.jsonl`
- command stdout/stderr artifacts
- patch artifacts when repair produces them

Benchmark runs store:

- `.local-swe/bench/<bench-run-id>/result.json`
- `.local-swe/bench/<bench-run-id>/summary.md`
- per-case logs and results

OpenTelemetry is optional and best-effort. If enabled without the dependency installed, the controller continues without telemetry.

By default, artifacts are written under `~/.local-swe` when that location is writable, with a temp-directory fallback otherwise. Set `LOCAL_SWE_ARTIFACT_ROOT` to override that location.

## Fake Model Testing

The test suite is designed to pass without a real LLM, internet access, Docker, sudo, or GPU:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

Use the fake model explicitly in manual repair runs when you want offline deterministic behavior:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

## Safety Model

The project enforces these invariants:

- local-first by default
- no direct target repository mutation
- no automatic push
- no automatic merge
- no arbitrary shell execution from model output
- no default cloud dependency
- artifacts preserved for review

## Limitations

Current limitations are intentional:

- local-first: the controller assumes local repositories and local artifact storage
- no default cloud: remote model APIs are not the default path
- no direct target repo mutation: patches are saved as artifacts instead of applied to the target repo
- optional scanner availability: some security scanners may be absent and are reported as warnings rather than hard requirements
- optional LangGraph: the `langgraph` orchestrator requires the `orchestration` extra
- optional GitHub API: PR creation is explicit, opt-in, and requires a token environment variable

## Further Reading

- [docs/policy_compiler.md](docs/policy_compiler.md)
- [docs/validation.md](docs/validation.md)
- [docs/repair_loop.md](docs/repair_loop.md)
- [docs/model_profiles.md](docs/model_profiles.md)
- [docs/security.md](docs/security.md)
- [docs/observability.md](docs/observability.md)
- [docs/orchestration.md](docs/orchestration.md)
- [docs/github.md](docs/github.md)
- [docs/benchmark.md](docs/benchmark.md)
- [docs/safety.md](docs/safety.md)
