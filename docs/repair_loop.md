# Repair Loop

The repair loop is bounded, verifier-first, and artifact-driven.

## Command

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

Optional generated-test stage:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake \
  --generate-tests
```

Bounded retry and candidate limits:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake \
  --max-iters 2 \
  --max-candidates 2
```

## Flow

The controller:

1. compiles policy
2. runs baseline validation
3. optionally generates regression tests in a worktree
4. routes the patch generation task to a model profile
5. parses and statically checks each patch
6. validates accepted patches in fresh worktrees
7. stores summaries, traces, reports, and patch artifacts
8. stops on success or a configured safety/budget condition

## Stop Conditions

Repair stops on:

- validation success
- repeated failure
- policy violation
- security failure
- environment failure
- explicit iteration/candidate/runtime limits

## Notes

- model output is never executed as shell
- accepted patches are saved as artifacts, not applied to the target repo
- the default path is a single bounded iteration
