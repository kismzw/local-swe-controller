# Safety Model

Safety is the primary product invariant for `local-swe-controller`.

## Core Rules

- do not mutate the target repository directly
- validate and apply candidate patches only in worktrees
- never execute model output as shell
- preserve local evidence for every run
- keep cloud integrations optional
- require explicit opt-in for networked GitHub PR creation

## Practical Consequences

- validate and repair write artifacts under `.local-swe/`
- patch artifacts are saved for review and manual application
- `runs create-pr` is optional and requires an explicit token and branch
- benchmark repair cases are restricted to fake profiles

## Limitations By Design

- local-first operation
- no default cloud dependence
- no automatic push or merge
- optional scanner availability depends on the local machine
- optional LangGraph and OpenTelemetry extras
