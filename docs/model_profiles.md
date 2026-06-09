# Model Profiles

Model configuration lives in `configs/model_profiles.example.yaml`.

## Providers

Current supported provider types:

- `fake`
- `openai_compatible`

The fake provider is used in tests and offline validation runs.

## Routing

Task routing is configured by task kind, for example:

- `failure_summary`
- `patch_generation`
- `test_generation`

`test_routing` is separate from normal routing and must resolve to fake profiles.

## Local Endpoint Requirement

For `openai_compatible` profiles, the router only accepts local endpoints:

- `localhost`
- `127.0.0.1`
- `::1`

Non-local endpoints are rejected.

## Example Workflow

Offline deterministic repair:

```bash
.venv/bin/local-swe repair \
  --repo examples/fixture_python_repo \
  --goal "Fix failing tests" \
  --model-profile fake
```

Using a local OpenAI-compatible server:

1. copy `configs/model_profiles.example.yaml`
2. point `base_url` at your local server
3. set the needed environment variable for the API key if your server expects one
4. run `local-swe repair --model-config <your-config> --model-profile <profile-name>`

## Practical Notes

- route selection also enforces context-window checks
- explicit `--model-profile` overrides task routing for repair
- benchmark repair cases require fake profiles to preserve no-network guarantees
