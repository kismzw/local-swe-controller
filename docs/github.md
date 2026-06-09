# GitHub PR Creation

GitHub integration is optional and disabled by default.

## Local PR Summary

Generate a local markdown PR summary from a stored run:

```bash
.venv/bin/local-swe runs pr-summary 20260609T032038-98ed7a31
```

This writes `pr_summary.md` into the run artifact directory.

## Optional PR Creation

Create a GitHub PR only when explicitly requested:

```bash
GITHUB_TOKEN=... .venv/bin/local-swe runs create-pr \
  20260609T032038-98ed7a31 \
  --repo-owner example \
  --repo-name repo \
  --base main \
  --head repair/fix-tests
```

## Safety Gates

The GitHub path is intentionally narrow:

- no network call unless `runs create-pr` is invoked
- `GITHUB_TOKEN` is required by default
- `--head` must be provided explicitly
- the controller never pushes a branch
- the controller never merges a PR
- tests mock GitHub API behavior and do not use live network access
