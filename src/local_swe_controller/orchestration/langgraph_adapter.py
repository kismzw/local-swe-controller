"""Optional LangGraph runtime for repair orchestration."""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from typing import Any

from local_swe_controller.exceptions import OrchestrationError
from local_swe_controller.models import FailureClass, RunStatus
from local_swe_controller.orchestration.models import (
    GraphExecutionStatus,
    RepairGraphNode,
    RepairGraphResult,
    RepairGraphState,
)
from local_swe_controller.repair.controller import (
    GeneratedTestStage,
    RepairController,
    RepairResult,
)


class LangGraphRepairAdapter:
    """Lazy LangGraph-backed repair runner."""

    def __init__(self, controller: RepairController) -> None:
        self.controller = controller

    @staticmethod
    def is_available() -> bool:
        try:
            importlib.import_module("langgraph.graph")
        except ImportError:
            return False
        return True

    @classmethod
    def require_available(cls) -> None:
        if cls.is_available():
            return
        raise OrchestrationError(
            "LangGraph orchestrator requested but LangGraph is not installed. "
            "Install the optional dependency group with `langgraph` support first."
        )

    def execute(self, state: RepairGraphState) -> RepairResult:
        self.require_available()
        graph = self.build_repair_graph()
        result_state = graph.invoke(
            state.checkpoint_payload(),
            config={"configurable": {"thread_id": self._thread_id_for(state)}},
        )
        final_state = RepairGraphState.model_validate(result_state)
        if final_state.result is None or final_state.result.repair_result is None:
            raise OrchestrationError("LangGraph execution finished without a repair result.")
        return final_state.result.repair_result

    def build_repair_graph(self) -> Any:
        self.require_available()
        graph_mod = importlib.import_module("langgraph.graph")
        checkpoint_mod = importlib.import_module("langgraph.checkpoint.memory")
        StateGraph = graph_mod.StateGraph
        END = graph_mod.END
        MemorySaver = checkpoint_mod.MemorySaver

        graph = StateGraph(dict)
        graph.add_node(RepairGraphNode.INTAKE.value, self._intake)
        graph.add_node(RepairGraphNode.COMPILE_POLICY.value, self._compile_policy)
        graph.add_node(RepairGraphNode.BASELINE_VALIDATE.value, self._baseline_validate)
        graph.add_node(RepairGraphNode.CLASSIFY_FAILURE.value, self._classify_failure)
        graph.add_node(RepairGraphNode.GENERATE_TESTS.value, self._generate_tests)
        graph.add_node(RepairGraphNode.GENERATE_PATCH.value, self._generate_patch)
        graph.add_node(RepairGraphNode.PATCH_POLICY_CHECK.value, self._patch_policy_check)
        graph.add_node(RepairGraphNode.VALIDATE_PATCH.value, self._validate_patch)
        graph.add_node(RepairGraphNode.STORE_ARTIFACTS.value, self._store_artifacts)
        graph.add_node(RepairGraphNode.ESCALATE.value, self._escalate)

        graph.set_entry_point(RepairGraphNode.INTAKE.value)
        graph.add_edge(RepairGraphNode.INTAKE.value, RepairGraphNode.COMPILE_POLICY.value)
        graph.add_edge(
            RepairGraphNode.COMPILE_POLICY.value,
            RepairGraphNode.BASELINE_VALIDATE.value,
        )
        graph.add_conditional_edges(
            RepairGraphNode.BASELINE_VALIDATE.value,
            self._route_after_baseline,
            {
                RepairGraphNode.STORE_ARTIFACTS.value: RepairGraphNode.STORE_ARTIFACTS.value,
                RepairGraphNode.ESCALATE.value: RepairGraphNode.ESCALATE.value,
                RepairGraphNode.CLASSIFY_FAILURE.value: RepairGraphNode.CLASSIFY_FAILURE.value,
            },
        )
        graph.add_conditional_edges(
            RepairGraphNode.CLASSIFY_FAILURE.value,
            self._route_after_classification,
            {
                RepairGraphNode.GENERATE_TESTS.value: RepairGraphNode.GENERATE_TESTS.value,
                RepairGraphNode.GENERATE_PATCH.value: RepairGraphNode.GENERATE_PATCH.value,
            },
        )
        graph.add_edge(RepairGraphNode.GENERATE_TESTS.value, RepairGraphNode.GENERATE_PATCH.value)
        graph.add_edge(
            RepairGraphNode.GENERATE_PATCH.value,
            RepairGraphNode.PATCH_POLICY_CHECK.value,
        )
        graph.add_conditional_edges(
            RepairGraphNode.PATCH_POLICY_CHECK.value,
            self._route_after_patch_policy,
            {
                RepairGraphNode.ESCALATE.value: RepairGraphNode.ESCALATE.value,
                RepairGraphNode.VALIDATE_PATCH.value: RepairGraphNode.VALIDATE_PATCH.value,
            },
        )
        graph.add_conditional_edges(
            RepairGraphNode.VALIDATE_PATCH.value,
            self._route_after_patch_validation,
            {
                RepairGraphNode.STORE_ARTIFACTS.value: RepairGraphNode.STORE_ARTIFACTS.value,
                RepairGraphNode.ESCALATE.value: RepairGraphNode.ESCALATE.value,
                RepairGraphNode.GENERATE_PATCH.value: RepairGraphNode.GENERATE_PATCH.value,
            },
        )
        graph.add_edge(RepairGraphNode.STORE_ARTIFACTS.value, END)
        graph.add_edge(RepairGraphNode.ESCALATE.value, END)
        return graph.compile(checkpointer=MemorySaver())

    def _thread_id_for(self, state: RepairGraphState) -> str:
        repo_name = state.repo_path.expanduser().resolve().name or "repo"
        return state.run_id or f"{repo_name}-{uuid.uuid4().hex}"

    def _intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = RepairGraphState.model_validate(payload)
        repo_root = state.repo_path.expanduser().resolve()
        run = self.controller.artifact_store.create_run(
            run_type="repair",
            repo_root=repo_root,
            goal=state.goal,
        )
        self.controller.artifact_store.append_trace(
            run,
            "run_started",
            {
                "repo_root": str(repo_root),
                "goal": state.goal,
                "command_type": "repair",
                "generate_tests": state.generate_tests,
                "max_iters": state.max_iters,
                "max_candidates": state.max_candidates or self._budget_defaults.max_candidates,
                "max_diff_lines": state.max_diff_lines or self._budget_defaults.max_diff_lines,
                "timeout_per_command": (
                    state.timeout_per_command or self._budget_defaults.timeout_seconds
                ),
                "max_total_runtime_seconds": (
                    state.max_total_runtime_seconds
                    or self._budget_defaults.max_total_runtime_seconds
                ),
            },
        )
        state.execution_status = GraphExecutionStatus.RUNNING
        state.current_node = RepairGraphNode.INTAKE
        state.node_history.append(RepairGraphNode.INTAKE)
        state.completed_nodes.append(RepairGraphNode.INTAKE)
        state.run_id = run.run_id
        state.resolved_model_profile = self.controller.router.resolve(
            "patch_generation",
            profile_name=state.model_profile_name,
        ).profile_name
        return state.checkpoint_payload()

    def _compile_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, run = self._load_state_and_run(payload)
        policy = self.controller.policy_compiler.compile(state.repo_path)
        policy_path = self.controller.artifact_store.write_policy_snapshot(
            run,
            policy.model_dump_json(indent=2),
        )
        self.controller.artifact_store.append_trace(
            run,
            "policy_compiled",
            {
                "policy_path": str(policy_path),
                "policy_hash": self.controller._policy_hash(policy_path),
            },
        )
        state.policy_path = policy_path
        state.current_node = RepairGraphNode.COMPILE_POLICY
        state.node_history.append(RepairGraphNode.COMPILE_POLICY)
        state.completed_nodes.append(RepairGraphNode.COMPILE_POLICY)
        return state.checkpoint_payload()

    def _baseline_validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, run = self._load_state_and_run(payload)
        policy = self.controller.policy_compiler.compile(state.repo_path)
        report = self.controller.validation_runner.validate(
            state.repo_path,
            policy=policy,
            selected_python=state.selected_python,
            artifact_dir=run.log_dir / "baseline",
            keep_worktree=state.keep_worktree,
            timeout_per_command=state.timeout_per_command,
            trace_callback=lambda event, event_payload: self.controller.artifact_store.append_trace(
                run,
                event,
                event_payload,
            ),
            phase="baseline",
        )
        state.baseline_report = report
        state.failure_class = report.failure_class
        state.current_node = RepairGraphNode.BASELINE_VALIDATE
        state.node_history.append(RepairGraphNode.BASELINE_VALIDATE)
        state.completed_nodes.append(RepairGraphNode.BASELINE_VALIDATE)
        return state.checkpoint_payload()

    def _classify_failure(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = RepairGraphState.model_validate(payload)
        report = state.baseline_report
        if report is None:
            raise OrchestrationError("Baseline report is missing before classification.")
        state.failure_class = report.failure_class
        state.current_node = RepairGraphNode.CLASSIFY_FAILURE
        state.node_history.append(RepairGraphNode.CLASSIFY_FAILURE)
        state.completed_nodes.append(RepairGraphNode.CLASSIFY_FAILURE)
        return state.checkpoint_payload()

    def _generate_tests(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, run = self._load_state_and_run(payload)
        policy = self.controller.policy_compiler.compile(state.repo_path)
        stage = self.controller._generate_tests(
            run=run,
            repo_root=state.repo_path.resolve(),
            goal=state.goal,
            policy=policy,
            baseline_report=self._require_report(state.baseline_report, "baseline"),
            requested_profile_name=state.model_profile_name,
            max_diff_lines=state.max_diff_lines or self._budget_defaults.max_diff_lines,
            timeout_per_command=(
                state.timeout_per_command or self._budget_defaults.timeout_seconds
            ),
            keep_worktree=state.keep_worktree,
            selected_python=state.selected_python,
        )
        self._apply_generated_test_stage(state, stage)
        state.current_node = RepairGraphNode.GENERATE_TESTS
        state.node_history.append(RepairGraphNode.GENERATE_TESTS)
        state.completed_nodes.append(RepairGraphNode.GENERATE_TESTS)
        return state.checkpoint_payload()

    def _generate_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, run = self._load_state_and_run(payload)
        state.iteration += 1 if state.candidate_index == 0 else 0
        state.candidate_index += 1
        if state.iteration > state.max_iters:
            state.stop_reason = "max_iterations_reached"
            state.escalation_reason = "Bounded retry limit reached."
            return state.checkpoint_payload()
        max_candidates = state.max_candidates or self._budget_defaults.max_candidates
        if state.candidate_index > max_candidates:
            state.stop_reason = "max_candidates_reached"
            state.escalation_reason = "Bounded candidate limit reached."
            return state.checkpoint_payload()

        report = (
            state.validation_report
            or state.generated_test_validation_report
            or state.baseline_report
        )
        baseline_report = self._require_report(report, "failure context")
        policy = self.controller.validation_runner._load_policy(  # noqa: SLF001
            state.repo_path,
            Path(state.policy_path) if state.policy_path else None,
            None,
        )
        route = self.controller.router.resolve(
            "patch_generation",
            profile_name=state.resolved_model_profile,
        )
        prompt = self.controller._build_prompt(
            repo_root=state.repo_path.resolve(),
            goal=state.goal,
            report=baseline_report,
            iteration=state.iteration,
            profile=route.profile,
            policy=policy,
        )
        route = self.controller.router.resolve(
            "patch_generation",
            profile_name=route.profile_name,
            prompt_text=prompt,
            system_prompt=(
                "You are a deterministic patch proposer. Return only a unified diff patch."
            ),
        )
        self.controller.artifact_store.append_trace(
            run,
            "model_called",
            {
                "purpose": "patch_generation",
                "model_profile": route.profile_name,
                "estimated_input_tokens": route.estimated_input_tokens,
            },
        )
        client = self.controller._build_client(route.profile_name, route.profile)
        patch_text = client.generate_patch(
            system_prompt=(
                "You are a deterministic patch proposer. Return only a unified diff patch."
            ),
            user_prompt=prompt,
        )
        patch_path = self.controller.artifact_store.write_patch(run, patch_text)
        self.controller.artifact_store.append_trace(
            run,
            "patch_generated",
            {
                "iteration": state.iteration,
                "candidate": state.candidate_index,
                "model_profile": route.profile_name,
                "patch_path": str(patch_path),
            },
        )
        state.patch_path = patch_path
        state.current_node = RepairGraphNode.GENERATE_PATCH
        state.node_history.append(RepairGraphNode.GENERATE_PATCH)
        state.completed_nodes.append(RepairGraphNode.GENERATE_PATCH)
        return state.checkpoint_payload()

    def _patch_policy_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, run = self._load_state_and_run(payload)
        if state.patch_path is None:
            raise OrchestrationError("Patch path is missing before patch policy check.")
        policy = self.controller.policy_compiler.compile(state.repo_path)
        patch_text = state.patch_path.read_text(encoding="utf-8")
        try:
            parsed_patch = self.controller.patch_parser.parse(patch_text)
        except Exception as exc:
            state.rejection_reasons = [str(exc)]
            state.escalation_reason = str(exc)
            state.stop_reason = "malformed_patch"
            return state.checkpoint_payload()
        check = self.controller.patch_parser.check(
            parsed_patch,
            policy=policy,
            max_diff_lines=state.max_diff_lines or self._budget_defaults.max_diff_lines,
        )
        self.controller.artifact_store.append_trace(
            run,
            "patch_policy_checked",
            {
                "iteration": state.iteration,
                "candidate": state.candidate_index,
                "accepted": check.accepted,
                "reasons": check.reasons,
            },
        )
        if not check.accepted:
            state.rejection_reasons = check.reasons
            state.escalation_reason = "Patch rejected by static safety checks."
            state.stop_reason = "patch_static_rejection"
        state.current_node = RepairGraphNode.PATCH_POLICY_CHECK
        state.node_history.append(RepairGraphNode.PATCH_POLICY_CHECK)
        state.completed_nodes.append(RepairGraphNode.PATCH_POLICY_CHECK)
        return state.checkpoint_payload()

    def _validate_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, run = self._load_state_and_run(payload)
        if state.patch_path is None:
            raise OrchestrationError("Patch path is missing before validation.")
        policy = self.controller.policy_compiler.compile(state.repo_path)
        pre_patch_paths = (
            [state.generated_test_patch_path] if state.generated_test_patch_path else []
        )
        self.controller.artifact_store.append_trace(
            run,
            "patch_validation_started",
            {
                "iteration": state.iteration,
                "candidate": state.candidate_index,
                "patch_path": str(state.patch_path),
                "worktree_path": None,
            },
        )
        report, worktree_path = self.controller._apply_and_validate(
            repo_root=state.repo_path.resolve(),
            policy=policy,
            patch_path=state.patch_path,
            artifact_dir=run.log_dir / f"candidate-{state.iteration}-{state.candidate_index}",
            keep_worktree=state.keep_worktree,
            timeout_per_command=(
                state.timeout_per_command or self._budget_defaults.timeout_seconds
            ),
            selected_python=state.selected_python,
            pre_patch_paths=pre_patch_paths,
            trace_callback=lambda event, event_payload: self.controller.artifact_store.append_trace(
                run,
                event,
                event_payload,
            ),
            phase=f"patch-{state.iteration}-{state.candidate_index}",
        )
        self.controller.artifact_store.append_trace(
            run,
            "patch_validation_finished",
            {
                "iteration": state.iteration,
                "candidate": state.candidate_index,
                "status": report.status.value,
                "failure_class": report.failure_class.value if report.failure_class else None,
                "worktree_path": str(worktree_path),
                "target_repo_changed": report.target_repo_changed,
            },
        )
        state.validation_report = report
        state.worktree_path = worktree_path
        state.failure_class = report.failure_class
        state.current_node = RepairGraphNode.VALIDATE_PATCH
        state.node_history.append(RepairGraphNode.VALIDATE_PATCH)
        state.completed_nodes.append(RepairGraphNode.VALIDATE_PATCH)
        if report.status != RunStatus.NO_ACTION_NEEDED:
            state.rejection_reasons = [report.summary or "Validation failed."]
        return state.checkpoint_payload()

    def _store_artifacts(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, run = self._load_state_and_run(payload)
        repair_result = self._build_result_from_state(state, run, escalated=False)
        finalized = self.controller._finalize(run, repair_result)
        state.result = RepairGraphResult(
            status=GraphExecutionStatus.COMPLETED,
            run_status=finalized.status,
            stop_reason=finalized.stop_reason,
            repair_result=finalized,
        )
        state.execution_status = GraphExecutionStatus.COMPLETED
        state.current_node = RepairGraphNode.STORE_ARTIFACTS
        state.node_history.append(RepairGraphNode.STORE_ARTIFACTS)
        state.completed_nodes.append(RepairGraphNode.STORE_ARTIFACTS)
        return state.checkpoint_payload()

    def _escalate(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, run = self._load_state_and_run(payload)
        repair_result = self._build_result_from_state(state, run, escalated=True)
        finalized = self.controller._finalize(run, repair_result)
        state.result = RepairGraphResult(
            status=GraphExecutionStatus.ESCALATED,
            run_status=finalized.status,
            stop_reason=finalized.stop_reason,
            repair_result=finalized,
            escalation_reason=state.escalation_reason,
        )
        state.execution_status = GraphExecutionStatus.ESCALATED
        state.current_node = RepairGraphNode.ESCALATE
        state.node_history.append(RepairGraphNode.ESCALATE)
        state.completed_nodes.append(RepairGraphNode.ESCALATE)
        return state.checkpoint_payload()

    def _route_after_baseline(self, payload: dict[str, Any]) -> str:
        state = RepairGraphState.model_validate(payload)
        report = self._require_report(state.baseline_report, "baseline")
        if report.status == RunStatus.NO_ACTION_NEEDED:
            return RepairGraphNode.STORE_ARTIFACTS.value
        if report.failure_class in {
            FailureClass.SECURITY,
            FailureClass.POLICY_VIOLATION,
            FailureClass.ENVIRONMENT,
        }:
            state.escalation_reason = report.summary
            return RepairGraphNode.ESCALATE.value
        return RepairGraphNode.CLASSIFY_FAILURE.value

    def _route_after_classification(self, payload: dict[str, Any]) -> str:
        state = RepairGraphState.model_validate(payload)
        return (
            RepairGraphNode.GENERATE_TESTS.value
            if state.generate_tests
            else RepairGraphNode.GENERATE_PATCH.value
        )

    def _route_after_patch_policy(self, payload: dict[str, Any]) -> str:
        state = RepairGraphState.model_validate(payload)
        if state.stop_reason in {"malformed_patch", "patch_static_rejection"}:
            return RepairGraphNode.ESCALATE.value
        return RepairGraphNode.VALIDATE_PATCH.value

    def _route_after_patch_validation(self, payload: dict[str, Any]) -> str:
        state = RepairGraphState.model_validate(payload)
        report = self._require_report(state.validation_report, "patch validation")
        if report.status == RunStatus.NO_ACTION_NEEDED:
            return RepairGraphNode.STORE_ARTIFACTS.value
        if report.failure_class in {
            FailureClass.SECURITY,
            FailureClass.POLICY_VIOLATION,
            FailureClass.ENVIRONMENT,
        }:
            state.escalation_reason = report.summary
            return RepairGraphNode.ESCALATE.value
        max_iters = state.max_iters
        max_candidates = state.max_candidates or self._budget_defaults.max_candidates
        if state.iteration >= max_iters or state.candidate_index >= max_candidates:
            state.escalation_reason = report.summary
            state.stop_reason = "max_iterations_reached"
            return RepairGraphNode.ESCALATE.value
        return RepairGraphNode.GENERATE_PATCH.value

    def _load_state_and_run(self, payload: dict[str, Any]) -> tuple[RepairGraphState, Any]:
        state = RepairGraphState.model_validate(payload)
        if state.run_id is None:
            raise OrchestrationError("Run ID is missing from graph state.")
        run = self.controller.artifact_store.get_run(state.run_id)
        if run is None:
            raise OrchestrationError(f"Run not found for graph state: {state.run_id}")
        # Rehydrate the known paths from the indexed run record.
        from local_swe_controller.storage.manager import RunContext

        return state, RunContext(
            run_id=run.run_id,
            run_type=run.run_type,
            repo_root=run.repo_root,
            goal=run.goal,
            created_at=run.created_at,
            artifact_dir=run.artifact_dir,
            log_dir=self.controller.artifact_store.logs_dir / run.run_id,
            trace_path=run.trace_path,
            patch_path=(
                run.patch_path
                or (self.controller.artifact_store.patches_dir / f"{run.run_id}.patch")
            ),
            generated_test_patch_path=(
                self.controller.artifact_store.patches_dir / f"{run.run_id}.generated-tests.patch"
            ),
            policy_path=(
                run.policy_path
                or (self.controller.artifact_store.policies_dir / f"{run.run_id}.compiled.json")
            ),
            report_path=run.report_path or (run.artifact_dir / "run.json"),
            summary_path=run.summary_path or (run.artifact_dir / "summary.md"),
        )

    def _apply_generated_test_stage(
        self,
        state: RepairGraphState,
        stage: GeneratedTestStage,
    ) -> None:
        state.generated_test_patch_path = stage.patch_path
        state.generated_test_validation_report = stage.validation_report
        state.generated_test_rejection_reasons = list(stage.rejection_reasons or [])
        if not stage.accepted and stage.rejection_reasons:
            state.escalation_reason = "; ".join(stage.rejection_reasons)

    def _build_result_from_state(
        self,
        state: RepairGraphState,
        run: Any,
        *,
        escalated: bool,
    ) -> RepairResult:
        baseline_report = self._require_report(state.baseline_report, "baseline")
        if (
            not escalated
            and state.validation_report is not None
            and state.validation_report.status == RunStatus.NO_ACTION_NEEDED
        ):
            return RepairResult(
                run_id=run.run_id,
                repo_root=state.repo_path.resolve(),
                goal=state.goal,
                status=RunStatus.SUCCESS,
                summary="Patch applied in a fresh worktree and validation passed.",
                model_profile=state.resolved_model_profile or "unknown",
                artifact_dir=run.artifact_dir,
                policy_path=Path(state.policy_path) if state.policy_path else run.policy_path,
                trace_path=run.trace_path,
                summary_path=run.summary_path,
                patch_path=state.patch_path,
                generated_test_patch_path=state.generated_test_patch_path,
                baseline_report=baseline_report,
                generated_test_validation_report=state.generated_test_validation_report,
                validation_report=state.validation_report,
                rejection_reasons=state.rejection_reasons,
                generated_test_rejection_reasons=state.generated_test_rejection_reasons,
                worktree_path=state.worktree_path,
                target_repo_changed=False,
                iterations_attempted=state.iteration,
                candidates_attempted=max(state.candidate_index, 0),
                stop_reason="validation_passed",
            )

        report = (
            state.validation_report
            or state.generated_test_validation_report
            or baseline_report
        )
        stop_reason = state.stop_reason or "graph_escalation"
        if report.failure_class == FailureClass.SECURITY:
            status = RunStatus.STOPPED_BY_SECURITY
            summary = "Stopped after security failure."
        elif report.failure_class == FailureClass.POLICY_VIOLATION:
            status = RunStatus.STOPPED_BY_POLICY
            summary = "Stopped after policy violation."
        elif report.failure_class == FailureClass.ENVIRONMENT:
            status = RunStatus.STOPPED_BY_ENVIRONMENT
            summary = "Stopped after environment failure."
        elif stop_reason in {"max_iterations_reached", "max_candidates_reached"}:
            status = RunStatus.STOPPED_BY_BUDGET
            summary = "Stopped after reaching bounded retry limits."
        elif baseline_report.status == RunStatus.NO_ACTION_NEEDED:
            status = RunStatus.NO_ACTION_NEEDED
            summary = "Baseline validation already passes; no repair was attempted."
        else:
            status = RunStatus.PATCH_REJECTED
            summary = state.escalation_reason or "LangGraph repair execution escalated."

        return RepairResult(
            run_id=run.run_id,
            repo_root=state.repo_path.resolve(),
            goal=state.goal,
            status=status,
            summary=summary,
            model_profile=state.resolved_model_profile or "unknown",
            artifact_dir=run.artifact_dir,
            policy_path=Path(state.policy_path) if state.policy_path else run.policy_path,
            trace_path=run.trace_path,
            summary_path=run.summary_path,
            patch_path=state.patch_path,
            generated_test_patch_path=state.generated_test_patch_path,
            baseline_report=baseline_report,
            generated_test_validation_report=state.generated_test_validation_report,
            validation_report=state.validation_report,
            rejection_reasons=state.rejection_reasons,
            generated_test_rejection_reasons=state.generated_test_rejection_reasons,
            worktree_path=state.worktree_path,
            target_repo_changed=False,
            iterations_attempted=state.iteration,
            candidates_attempted=max(state.candidate_index, 0),
            stop_reason=stop_reason,
        )

    def _require_report(self, report: Any, label: str):
        if report is None:
            raise OrchestrationError(f"{label.capitalize()} report is missing in graph state.")
        return report

    @property
    def _budget_defaults(self):
        return self.controller.validation_runner.default_policy.defaults
