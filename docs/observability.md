# Observability

The controller preserves local evidence for validate, repair, PR summary, and benchmark workflows.

## Stored Artifacts

Validate and repair runs write local artifacts under the configured artifact root, which defaults to `~/.local-swe/` when writable and otherwise falls back to a temp-directory root, including:

- run reports
- markdown summaries
- trace files
- command stdout/stderr artifacts
- patch artifacts when repair produces them

Run inspection commands:

```bash
.venv/bin/local-swe runs list
.venv/bin/local-swe runs show 20260609T032038-98ed7a31
```

`runs list` reports finalized runs only. In-progress internal index rows are not shown.

## Structured Traces

`trace.jsonl` captures structured events such as:

- `run_started`
- `policy_compiled`
- `validation_started`
- `validation_finished`
- `model_called`
- `patch_generated`
- `patch_validation_finished`
- `run_finished`

## OpenTelemetry

OpenTelemetry support is optional:

```bash
uv sync --extra observability
```

If enabled in config but the dependency is missing, the controller continues without telemetry and reports a warning internally instead of failing the run.
