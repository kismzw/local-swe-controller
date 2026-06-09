# Validation

Validation is the first operational layer of the controller. It executes policy-defined checks inside an ephemeral git worktree and records artifacts locally.

## Command

```bash
.venv/bin/local-swe validate --repo examples/fixture_python_repo
```

Useful options:

```bash
.venv/bin/local-swe validate \
  --repo examples/fixture_python_repo \
  --keep-worktree
```

```bash
.venv/bin/local-swe validate \
  --repo examples/fixture_python_repo \
  --policy .local-swe/policies/some-policy.compiled.json
```

## Behavior

Validation:

- compiles or loads policy
- creates an ephemeral worktree
- runs allowed commands as explicit argv lists
- captures stdout, stderr, exit codes, and durations
- classifies failures
- writes local artifacts and a summary

## Artifacts

Validate runs store:

- `run.json`
- `summary.md`
- `trace.jsonl`
- per-command stdout/stderr artifacts

## Safety

- the target repository is not modified directly
- `shell=False` command execution is enforced
- shell launchers and shell metacharacters are rejected
- setup commands may be skipped when default policy disallows network/bootstrap work
