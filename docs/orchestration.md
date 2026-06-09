# Orchestration

`local-swe-controller` currently supports two repair runtimes:

- `deterministic`
- `langgraph`

## Deterministic

This is the default runtime and the main supported path.

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake \
  --orchestrator deterministic
```

It runs the verifier-first controller directly and preserves the current repair behavior.

## LangGraph

LangGraph support is optional and requires the extra dependency:

```bash
uv sync --extra orchestration
```

Then:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake \
  --orchestrator langgraph
```

The LangGraph runtime reuses the same underlying services:

- policy compiler
- validation runner
- model router
- patch parser and patch policy checks
- artifact store

## Shared Safety Guarantees

Both runtimes preserve the same invariants:

- no direct target repository mutation
- no arbitrary shell execution from model output
- bounded retries and candidate counts
- stop on policy, security, environment, and repeated-failure conditions
