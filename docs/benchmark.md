# Benchmark Runner

The benchmark runner executes local validate or repair cases from YAML files and stores aggregate results under `.local-swe/bench/`.

## Commands

Run cases:

```bash
.venv/bin/local-swe bench run --cases "examples/benchmarks/*.yaml"
```

Show a saved benchmark result:

```bash
.venv/bin/local-swe bench show 20260609T032026-79ca164c
```

## Case Schema

Each benchmark case supports:

- `name`
- `repo`
- `mode`
- `goal`
- `model_profile`
- `generate_tests`
- `orchestrator`
- `max_iters`
- `max_candidates`
- `expected_status`
- `acceptance_commands`
- `notes`

## Stored Output

Each benchmark run stores:

- `result.json`
- `summary.md`
- per-case `result.json`
- per-case acceptance stdout/stderr logs

## Metrics

Aggregate metrics include:

- `total`
- `passed`
- `failed`
- `status_counts`
- `failure_class_counts`
- `duration_seconds`

## Safety

- benchmark cases must point at local repositories
- repair benchmark cases must use a fake model profile
- acceptance commands run through the same explicit-argv command runner
- benchmark runs record whether repo git status changed
