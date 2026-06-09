"""CLI entrypoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from local_swe_controller import __version__
from local_swe_controller.benchmark import BenchmarkRunner
from local_swe_controller.exceptions import (
    BenchmarkError,
    ConfigError,
    LocalSweError,
    OrchestrationError,
    PolicyCompileError,
    PRWorkflowError,
    ValidationError,
)
from local_swe_controller.models import RunStatus
from local_swe_controller.orchestration.langgraph_adapter import LangGraphRepairAdapter
from local_swe_controller.policy.compiler import PolicyCompiler
from local_swe_controller.pr import PRService
from local_swe_controller.repair.controller import RepairController
from local_swe_controller.sandbox.commands import resolve_selected_python
from local_swe_controller.storage import ArtifactStore
from local_swe_controller.validation.runner import ValidationRunner

app = typer.Typer(
    no_args_is_help=True,
    help="Local-first deterministic SWE controller.",
)
runs_app = typer.Typer(help="Inspect stored controller runs.")
bench_app = typer.Typer(help="Run local benchmark cases.")
app.add_typer(runs_app, name="runs")
app.add_typer(bench_app, name="bench")


def _default_policy_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "default_policy.yaml"


def _default_model_profiles_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "model_profiles.example.yaml"


@app.callback()
def main() -> None:
    """Local-first deterministic SWE controller."""


@app.command("version")
def version() -> None:
    """Print the installed version."""

    typer.echo(__version__)


@app.command("compile-policy")
def compile_policy(
    repo: Annotated[
        Path,
        typer.Option(
            ...,
            exists=False,
            file_okay=False,
            dir_okay=True,
            help="Target repository path.",
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option(help="Output path for the compiled policy JSON."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Also print the compiled policy as JSON."),
    ] = False,
) -> None:
    """Compile deterministic repository policy from repo metadata."""

    compiler = PolicyCompiler(default_policy_path=_default_policy_path())

    try:
        policy = compiler.compile(repo)
        resolved_output = (
            output
            or ArtifactStore().compiled_policy_output_path(policy.repo_root)
        )
        written_path = compiler.write(policy, resolved_output)
    except (ConfigError, PolicyCompileError, LocalSweError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from None

    typer.echo(f"Compiled policy written to {written_path}")
    if json_output:
        typer.echo(policy.model_dump_json(indent=2))


@app.command("validate")
def validate(
    repo: Annotated[
        Path,
        typer.Option(
            ...,
            exists=False,
            file_okay=False,
            dir_okay=True,
            help="Target repository path.",
        ),
    ],
    policy: Annotated[
        Path | None,
        typer.Option(help="Path to an existing compiled policy JSON."),
    ] = None,
    keep_worktree: Annotated[
        bool,
        typer.Option(help="Keep the ephemeral worktree after validation."),
    ] = False,
    python_path: Annotated[
        Path | None,
        typer.Option("--python", help="Python interpreter to use inside worktrees."),
    ] = None,
    venv_path: Annotated[
        Path | None,
        typer.Option("--venv", help="Virtualenv whose bin/python should be used inside worktrees."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the validation report as JSON."),
    ] = False,
) -> None:
    """Validate a repository inside an ephemeral git worktree."""

    runner = ValidationRunner(default_policy_path=_default_policy_path())

    try:
        selected_python = resolve_selected_python(
            python_path=python_path,
            venv_path=venv_path,
        )
        report, run = runner.validate_and_record(
            repo,
            policy_path=policy,
            selected_python=selected_python,
            keep_worktree=keep_worktree,
            artifact_store=ArtifactStore(),
        )
    except (ConfigError, LocalSweError, PolicyCompileError, ValidationError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from None

    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        if report.failure_class is not None:
            raise typer.Exit(code=1)
        return

    typer.echo(f"Validation status: {report.status.value}")
    if report.failure_class is not None:
        typer.echo(f"Failure class: {report.failure_class.value}")
    if report.summary:
        typer.echo(report.summary)
    if report.artifact_dir is not None:
        typer.echo(f"Artifacts: {report.artifact_dir}")
    typer.echo(f"Run ID: {run.run_id}")
    if report.worktree_path is not None:
        typer.echo(f"Worktree: {report.worktree_path}")
    if report.warnings:
        for warning in report.warnings:
            typer.echo(f"Warning: {warning}")

    if report.failure_class is not None:
        raise typer.Exit(code=1)


@app.command("repair")
def repair(
    repo: Annotated[
        Path,
        typer.Option(
            ...,
            exists=False,
            file_okay=False,
            dir_okay=True,
            help="Target repository path.",
        ),
    ],
    goal: Annotated[str, typer.Option(..., help="Repair goal to pursue.")],
    model_profile: Annotated[
        str | None,
        typer.Option(help="Model profile name from configs/model_profiles.example.yaml."),
    ] = None,
    model_config: Annotated[
        Path | None,
        typer.Option(help="Path to a model profile YAML file."),
    ] = None,
    generate_tests: Annotated[
        bool,
        typer.Option(help="Generate regression tests in a worktree before repair."),
    ] = False,
    max_iters: Annotated[
        int,
        typer.Option(help="Maximum repair iterations. Defaults to 1."),
    ] = 1,
    max_candidates: Annotated[
        int | None,
        typer.Option(help="Maximum candidates to try per iteration."),
    ] = None,
    max_diff_lines: Annotated[
        int | None,
        typer.Option(help="Maximum allowed added/removed diff lines."),
    ] = None,
    timeout_per_command: Annotated[
        int | None,
        typer.Option(help="Timeout in seconds for each validation command."),
    ] = None,
    max_total_runtime_seconds: Annotated[
        int | None,
        typer.Option(help="Maximum total runtime for the repair run."),
    ] = None,
    keep_worktree: Annotated[
        bool,
        typer.Option(help="Keep the ephemeral candidate worktree after repair."),
    ] = False,
    python_path: Annotated[
        Path | None,
        typer.Option("--python", help="Python interpreter to use inside worktrees."),
    ] = None,
    venv_path: Annotated[
        Path | None,
        typer.Option("--venv", help="Virtualenv whose bin/python should be used inside worktrees."),
    ] = None,
    orchestrator: Annotated[
        str,
        typer.Option(help="Repair orchestrator to use: deterministic or langgraph."),
    ] = "deterministic",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the repair result as JSON."),
    ] = False,
) -> None:
    """Run the deterministic one-shot repair pipeline."""

    controller = RepairController(
        default_policy_path=_default_policy_path(),
        model_profiles_path=model_config or _default_model_profiles_path(),
        artifact_store=ArtifactStore(),
    )

    try:
        selected_python = resolve_selected_python(
            python_path=python_path,
            venv_path=venv_path,
        )
        if orchestrator == "deterministic":
            result = controller.repair(
                repo_path=repo,
                goal=goal,
                model_profile_name=model_profile,
                generate_tests=generate_tests,
                max_iters=max_iters,
                max_candidates=max_candidates,
                max_diff_lines=max_diff_lines,
                timeout_per_command=timeout_per_command,
                max_total_runtime_seconds=max_total_runtime_seconds,
                keep_worktree=keep_worktree,
                selected_python=selected_python,
            )
        elif orchestrator == "langgraph":
            adapter = LangGraphRepairAdapter(controller)
            from local_swe_controller.orchestration.models import RepairGraphState

            result = adapter.execute(
                RepairGraphState(
                    repo_path=repo,
                    goal=goal,
                    model_profile_name=model_profile,
                    generate_tests=generate_tests,
                    max_iters=max_iters,
                    max_candidates=max_candidates,
                    max_diff_lines=max_diff_lines,
                    timeout_per_command=timeout_per_command,
                    max_total_runtime_seconds=max_total_runtime_seconds,
                    keep_worktree=keep_worktree,
                    selected_python=selected_python,
                )
            )
        else:
            raise OrchestrationError(
                f"Unsupported orchestrator '{orchestrator}'. Use 'deterministic' or 'langgraph'."
            )
    except (
        ConfigError,
        LocalSweError,
        OrchestrationError,
        PolicyCompileError,
        ValidationError,
    ) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from None

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo(f"Repair status: {result.status.value}")
        typer.echo(f"Run ID: {result.run_id}")
        typer.echo(f"Artifacts: {result.artifact_dir}")
        if result.patch_path is not None:
            typer.echo(f"Patch: {result.patch_path}")
        if result.generated_test_patch_path is not None:
            typer.echo(f"Generated tests: {result.generated_test_patch_path}")
        typer.echo(result.summary)
        if result.generated_test_rejection_reasons:
            for reason in result.generated_test_rejection_reasons:
                typer.echo(f"Generated test rejected: {reason}")
        if result.rejection_reasons:
            for reason in result.rejection_reasons:
                typer.echo(f"Rejected: {reason}")

    if result.status not in {RunStatus.SUCCESS, RunStatus.NO_ACTION_NEEDED}:
        raise typer.Exit(code=1)


@runs_app.command("list")
def runs_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List stored controller runs."""

    store = ArtifactStore()
    records = store.list_runs()
    if json_output:
        typer.echo(json.dumps([record.model_dump(mode="json") for record in records], indent=2))
        return
    if not records:
        typer.echo("No runs found.")
        return
    for record in records:
        goal_suffix = f" goal={record.goal!r}" if record.goal else ""
        typer.echo(
            f"{record.run_id} {record.run_type} {record.status.value} "
            f"repo={record.repo_root}{goal_suffix}"
        )


@runs_app.command("show")
def runs_show(
    run_id: Annotated[str, typer.Argument(help="Run ID to display.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one stored controller run."""

    store = ArtifactStore()
    record = store.get_run(run_id)
    if record is None:
        raise typer.Exit(code=_print_error(f"Run not found: {run_id}"))
    if json_output:
        typer.echo(json.dumps(record.model_dump(mode="json"), indent=2))
        return
    typer.echo(f"Run ID: {record.run_id}")
    typer.echo(f"Type: {record.run_type}")
    typer.echo(f"Status: {record.status.value}")
    typer.echo(f"Repo: {record.repo_root}")
    if record.goal:
        typer.echo(f"Goal: {record.goal}")
    if record.summary:
        typer.echo(f"Summary: {record.summary}")
    if record.summary_path is not None:
        typer.echo(f"Summary Path: {record.summary_path}")
    typer.echo(f"Artifacts: {record.artifact_dir}")
    typer.echo(f"Trace: {record.trace_path}")


@runs_app.command("pr-summary")
def runs_pr_summary(
    run_id: Annotated[str, typer.Argument(help="Run ID to summarize for PR review.")],
) -> None:
    """Generate a local PR summary markdown artifact for one run."""

    try:
        result = PRService(ArtifactStore()).generate_pr_summary(run_id)
    except PRWorkflowError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from None
    typer.echo(f"PR summary written to {result.path}")


@runs_app.command("create-pr")
def runs_create_pr(
    run_id: Annotated[str, typer.Argument(help="Run ID to use for PR creation.")],
    repo_owner: Annotated[str, typer.Option(..., help="GitHub repository owner.")],
    repo_name: Annotated[str, typer.Option(..., help="GitHub repository name.")],
    base: Annotated[str, typer.Option(..., help="Base branch for the pull request.")],
    head: Annotated[
        str | None,
        typer.Option(
            "--head",
            help="Head branch for the pull request. Required because local-swe never pushes.",
        ),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option(help="Optional pull request title. Defaults to the run goal."),
    ] = None,
    token_env_var: Annotated[
        str,
        typer.Option(help="Environment variable that contains the GitHub token."),
    ] = "GITHUB_TOKEN",
) -> None:
    """Create a GitHub pull request from an existing branch using the local PR summary."""

    try:
        result = PRService(ArtifactStore()).create_pull_request(
            run_id,
            repo_owner=repo_owner,
            repo_name=repo_name,
            base=base,
            head=head,
            token_env_var=token_env_var,
            title=title,
        )
    except PRWorkflowError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from None

    typer.echo(f"PR created: #{result.number} {result.url}")
    typer.echo(f"PR summary: {result.summary_path}")
    typer.echo(f"GitHub response: {result.response_path}")


@bench_app.command("run")
def bench_run(
    cases: Annotated[
        str,
        typer.Option(..., help="Glob pattern selecting benchmark YAML case files."),
    ],
) -> None:
    """Run local benchmark cases and save aggregated artifacts."""

    runner = BenchmarkRunner(
        default_policy_path=_default_policy_path(),
        model_profiles_path=_default_model_profiles_path(),
        artifact_store=ArtifactStore(),
    )
    try:
        result = runner.run(cases_glob=cases)
    except (BenchmarkError, ConfigError, LocalSweError, OrchestrationError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from None

    typer.echo(f"Benchmark run ID: {result.bench_run_id}")
    typer.echo(f"Artifacts: {result.artifact_dir}")
    typer.echo(f"Passed: {result.passed}/{result.total}")
    if result.failed:
        raise typer.Exit(code=1)


@bench_app.command("show")
def bench_show(
    bench_run_id: Annotated[str, typer.Argument(help="Benchmark run ID to display.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one benchmark run result."""

    runner = BenchmarkRunner(
        default_policy_path=_default_policy_path(),
        model_profiles_path=_default_model_profiles_path(),
        artifact_store=ArtifactStore(),
    )
    try:
        result = runner.load_run(bench_run_id)
    except (BenchmarkError, ConfigError, LocalSweError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from None
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    typer.echo(f"Benchmark Run ID: {result.bench_run_id}")
    typer.echo(f"Artifacts: {result.artifact_dir}")
    typer.echo(f"Total: {result.total}")
    typer.echo(f"Passed: {result.passed}")
    typer.echo(f"Failed: {result.failed}")
    typer.echo(f"Duration Seconds: {result.duration_seconds:.3f}")
    typer.echo(f"Status Counts: {json.dumps(result.status_counts, sort_keys=True)}")
    typer.echo(
        "Failure Class Counts: "
        f"{json.dumps(result.failure_class_counts, sort_keys=True)}"
    )
    for case in result.cases:
        typer.echo(
            f"{case.name} passed={str(case.passed).lower()} "
            f"expected={case.expected_status.value if case.expected_status else 'n/a'} "
            f"actual={case.actual_status.value if case.actual_status else 'ERROR'}"
        )


def _print_error(message: str) -> int:
    typer.echo(f"Error: {message}", err=True)
    return 1


if __name__ == "__main__":
    app()
