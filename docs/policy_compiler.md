# Policy Compiler

The policy compiler turns repository metadata into a deterministic JSON policy used by validation and repair.

## Inputs

The compiler inspects common files when present:

- `CONTRIBUTING.md`
- `AGENTS.md`
- `README.md`
- `pyproject.toml`
- `package.json`
- `Cargo.toml`
- `Makefile`
- `.github/workflows/*.yml`

## Command

```bash
.venv/bin/local-swe compile-policy --repo examples/fixture_python_repo
```

Optional output path:

```bash
.venv/bin/local-swe compile-policy \
  --repo examples/fixture_python_repo \
  --output /tmp/policy.compiled.json
```

## Output

The compiler writes a structured policy JSON file. By default this is stored under `.local-swe/policies/`.

The compiled policy includes fields such as:

- inferred setup, format, lint, typecheck, test, and security commands
- hard and soft gates
- forbidden commands
- forbidden paths
- approval-required operations
- patch policy settings

## Notes

- this stage is deterministic and does not call an LLM
- compilation reads the target repo but does not modify it
- the output can be reused with `local-swe validate --policy ...`
