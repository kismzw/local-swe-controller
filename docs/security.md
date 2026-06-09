# Security

Security behavior in `local-swe-controller` is conservative by default.

## Command Safety

Command execution is explicit-argv only:

- no shell strings
- no shell launchers with `-c`
- no shell metacharacters
- no direct execution of model-generated shell output

Examples of forbidden patterns include:

- `rm -rf`
- `git push --force`
- `sudo`
- `curl | bash`
- `wget | bash`

## Patch Safety

Patch policy can reject or escalate risky edits such as:

- dependency changes
- lockfile changes
- CI changes
- license changes
- test weakening or removal
- security-sensitive path changes

## Security Scanners

Security scanner commands may be optional. If a configured optional scanner is unavailable, the controller records a warning rather than assuming the scanner exists everywhere.

## Default Network Posture

The default policy disables network access during validation-oriented workflows. Setup/bootstrap commands may therefore be skipped when they would violate the local safety posture.
