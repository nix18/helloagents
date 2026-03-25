"""Unified post-test pipeline runtime model and executor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol


_DEVELOP_STAGE_FACTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "8A 基础验证",
        (
            "探测 lint、类型检查、单元测试、覆盖率、E2E、IDE 运行配置与控制电脑能力。",
            "先完成基础测试与静态分析，再决定是否进入测试审计。",
        ),
    ),
    (
        "8B 测试审计闭环",
        (
            "命中测试变更、核心逻辑覆盖不足或主代理显式标记风险时，必须触发 test_audit_cycle。",
            "审计固定检查 coverage_assessment、correctness_assessment、staleness_assessment。",
            "审计结果必须返回 blind_spots、repairs_applied、retest_commands、parent_guidance。",
        ),
    ),
    (
        "8C 修复与回归",
        (
            "若 repairs_applied 非空，必须重新运行受影响测试命令。",
            "blind_spots 必须进入验收摘要，不能只停留在子代理返回值中。",
        ),
    ),
    (
        "9A 运行入口恢复",
        (
            "有独立运行入口时，按 ide_restart_recovery 能力阶梯执行：IDE 原生运行配置 > 控制电脑类自动化 > 手动重启。",
            "运行入口恢复结果必须纳入最终交付验收。",
        ),
    ),
)


def render_develop_stage_capsule() -> str:
    """Render the DEVELOP stage capsule from the runtime fact source."""
    lines = [
        "[HelloAGENTS DEVELOP 阶段执行提醒]",
        "你当前处于开发实施阶段，必须按统一 post-test pipeline 执行关键闭环：",
        "",
    ]
    for title, facts in _DEVELOP_STAGE_FACTS:
        lines.append(f"{title}:")
        for fact in facts:
            lines.append(f"- {fact}")
        lines.append("")
    lines.extend(
        (
            "固定要求:",
            "- DEVELOP 与 ~test 入口不得各自维护独立后测流程，应复用同一 post-test pipeline。",
            "- reviewer 保持只读；测试审计使用独立 test_auditor 语义。",
            "- test_audit 的结构化结果必须被消费到回归、验收和事件流。",
            "- 只要本轮新增或修改了测试文件，就必须执行子代理侧测试审计；没有子代理审计结果不得结束流程。",
        )
    )
    return "\n".join(lines)


@dataclass(slots=True)
class TestCommandResult:
    """Normalized result of a single test command."""

    command: str
    passed: bool
    output: str = ""
    scope: str = ""
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(slots=True)
class AuditTriggerDecision:
    """Decision on whether post-test audit must run."""

    triggered: bool
    reason: str
    source: str
    requires_writable_subagent: bool = False
    hard_gate: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(slots=True)
class TestAuditResult:
    """Structured audit result consumed by runtime and reporting."""

    status: str = "skipped"
    executed: bool = False
    via: str = "not_run"
    coverage_assessment: str = ""
    correctness_assessment: str = ""
    staleness_assessment: str = ""
    blind_spots: list[str] = field(default_factory=list)
    repairs_applied: list[str] = field(default_factory=list)
    retest_commands: list[str] = field(default_factory=list)
    parent_guidance: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    needs_followup: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(slots=True)
class RuntimeRecoveryResult:
    """Result of runtime recovery after code changes."""

    required: bool = False
    executed: bool = False
    success: bool = True
    strategy: str = "not_required"
    notes: list[str] = field(default_factory=list)
    guidance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(slots=True)
class PostTestContext:
    """Input context for the unified post-test pipeline."""

    mode: str
    trigger_source: str
    changed_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    code_test_map: dict[str, list[str]] = field(default_factory=dict)
    test_results: list[TestCommandResult] = field(default_factory=list)
    coverage: dict[str, Any] | None = None
    risk_points: list[str] = field(default_factory=list)
    has_runtime_entry: bool = False
    runtime_target: str = ""
    audit_required: bool | None = None
    allow_direct_audit: bool = True
    subagent_capability: str = "available"


@dataclass(slots=True)
class PostTestReport:
    """Unified report for post-test audit, retest, and recovery."""

    mode: str
    trigger_source: str
    audit_decision: AuditTriggerDecision
    audit_result: TestAuditResult
    retest_required: bool
    retest_results: list[TestCommandResult] = field(default_factory=list)
    runtime_recovery: RuntimeRecoveryResult = field(default_factory=RuntimeRecoveryResult)
    acceptance_summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        data = asdict(self)
        data["blind_spots"] = list(self.audit_result.blind_spots)
        return data


class AuditRunner(Protocol):
    """Callable protocol for executing test audit."""

    def __call__(
        self,
        context: PostTestContext,
        decision: AuditTriggerDecision,
    ) -> TestAuditResult:
        """Execute test audit and return structured result."""


class RetestRunner(Protocol):
    """Callable protocol for rerunning impacted tests."""

    def __call__(
        self,
        commands: list[str],
        context: PostTestContext,
    ) -> list[TestCommandResult]:
        """Run impacted test commands and return normalized results."""


class RuntimeRecoveryRunner(Protocol):
    """Callable protocol for runtime recovery after code changes."""

    def __call__(
        self,
        context: PostTestContext,
        audit_result: TestAuditResult,
    ) -> RuntimeRecoveryResult:
        """Recover runtime entry and return structured result."""


def _default_event_payload(
    context: PostTestContext,
    decision: AuditTriggerDecision,
    audit_result: TestAuditResult | None = None,
) -> dict[str, Any]:
    """Build a consistent event payload."""
    payload: dict[str, Any] = {
        "mode": context.mode,
        "trigger_source": context.trigger_source,
        "changed_files": list(context.changed_files),
        "test_files": list(context.test_files),
        "risk_points": list(context.risk_points),
        "audit_decision": decision.to_dict(),
    }
    if audit_result is not None:
        payload["test_audit"] = audit_result.to_dict()
    return payload


class PostTestPipelineExecutor:
    """Execute the unified post-test workflow."""

    def __init__(
        self,
        audit_runner: AuditRunner | None = None,
        retest_runner: RetestRunner | None = None,
        runtime_recovery_runner: RuntimeRecoveryRunner | None = None,
        event_logger: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._audit_runner = audit_runner
        self._retest_runner = retest_runner
        self._runtime_recovery_runner = runtime_recovery_runner
        self._event_logger = event_logger

    def decide_audit_trigger(self, context: PostTestContext) -> AuditTriggerDecision:
        """Decide whether test audit must run."""
        if context.test_files:
            return AuditTriggerDecision(
                True,
                "本轮新增或修改了测试文件，必须执行子代理侧测试审计。",
                "test_files",
                requires_writable_subagent=True,
                hard_gate=True,
            )
        if context.audit_required is True:
            return AuditTriggerDecision(True, "显式要求执行测试审计", "explicit")
        if context.audit_required is False:
            return AuditTriggerDecision(False, "显式禁用测试审计", "explicit")
        if context.risk_points:
            return AuditTriggerDecision(
                True,
                "存在需要二次审计的高风险点或覆盖盲区",
                "risk_points",
            )
        if context.coverage and context.coverage.get("insufficient"):
            return AuditTriggerDecision(True, "覆盖率信号显示存在明显空洞", "coverage")
        return AuditTriggerDecision(False, "未命中测试审计触发条件", "none")

    def execute(self, context: PostTestContext) -> PostTestReport:
        """Execute the pipeline for a given context."""
        decision = self.decide_audit_trigger(context)
        self._emit("test_audit.triggered", _default_event_payload(context, decision))

        audit_result = self._run_audit(context, decision)
        retest_results: list[TestCommandResult] = []
        retest_required = bool(audit_result.repairs_applied)

        if retest_required:
            self._emit(
                "test_audit.retest_started",
                _default_event_payload(context, decision, audit_result),
            )
            if self._retest_runner:
                retest_results = self._retest_runner(
                    list(audit_result.retest_commands),
                    context,
                )
            self._emit(
                "test_audit.retest_completed",
                {
                    **_default_event_payload(context, decision, audit_result),
                    "retest_results": [result.to_dict() for result in retest_results],
                },
            )

        if decision.hard_gate and audit_result.status == "failed":
            acceptance_summary = self._build_acceptance_summary(
                decision,
                audit_result,
                retest_results,
                RuntimeRecoveryResult(required=False, strategy="blocked_by_test_audit"),
            )
            return PostTestReport(
                mode=context.mode,
                trigger_source=context.trigger_source,
                audit_decision=decision,
                audit_result=audit_result,
                retest_required=retest_required,
                retest_results=retest_results,
                runtime_recovery=RuntimeRecoveryResult(
                    required=False,
                    executed=False,
                    success=False,
                    strategy="blocked_by_test_audit",
                    guidance=["hard gate: 子代理侧测试审计未完成，禁止进入运行入口恢复和完成态。"],
                ),
                acceptance_summary=acceptance_summary,
            )

        runtime_recovery = self._recover_runtime(context, decision, audit_result)
        acceptance_summary = self._build_acceptance_summary(
            decision,
            audit_result,
            retest_results,
            runtime_recovery,
        )

        return PostTestReport(
            mode=context.mode,
            trigger_source=context.trigger_source,
            audit_decision=decision,
            audit_result=audit_result,
            retest_required=retest_required,
            retest_results=retest_results,
            runtime_recovery=runtime_recovery,
            acceptance_summary=acceptance_summary,
        )

    def _run_audit(
        self,
        context: PostTestContext,
        decision: AuditTriggerDecision,
    ) -> TestAuditResult:
        """Run or skip the audit stage."""
        if not decision.triggered:
            return TestAuditResult(
                status="skipped",
                executed=False,
                via="skipped",
                parent_guidance=["本次未命中 test_audit_cycle 触发条件。"],
            )

        self._emit("test_audit.dispatched", _default_event_payload(context, decision))
        if decision.requires_writable_subagent and self._audit_runner is None:
            audit_result = TestAuditResult(
                status="failed",
                executed=False,
                via="blocked_no_subagent",
                issues=["命中测试文件变更后必须执行子代理侧测试审计，当前未提供可写子代理执行器。"],
                parent_guidance=[
                    "新增或修改测试文件时，禁止主代理直接审计。",
                    "请先调度 test_auditor 子代理，再继续后测流程。",
                ],
                needs_followup=True,
            )
        elif self._audit_runner is not None:
            audit_result = self._audit_runner(context, decision)
        else:
            audit_result = self._build_direct_audit(context)

        if decision.requires_writable_subagent and audit_result.via == "direct":
            audit_result = TestAuditResult(
                status="failed",
                executed=False,
                via="blocked_wrong_executor",
                issues=[
                    "新增或修改测试文件后，测试审计必须由子代理执行，不能走主代理 direct 降级。"
                ],
                parent_guidance=[
                    "请改为调度可写 test_auditor 子代理。",
                    "完成子代理审计后，再进入 repairs_applied / retest / acceptance。",
                ],
                needs_followup=True,
            )
        elif decision.requires_writable_subagent and audit_result.via not in {
            "blocked_no_subagent",
            "blocked_wrong_executor",
        }:
            missing_fields = self._validate_subagent_audit_result(audit_result)
            if missing_fields:
                audit_result = TestAuditResult(
                    status="failed",
                    executed=False,
                    via="blocked_incomplete_audit",
                    issues=[
                        "新增或修改测试文件后，子代理测试审计结果不完整，缺少: "
                        + "、".join(missing_fields)
                    ],
                    parent_guidance=[
                        "请确保 test_auditor 实际执行，并返回完整的结构化 test_audit 字段。",
                    ],
                    needs_followup=True,
                )
        event_name = (
            "test_audit.blocked" if audit_result.status == "failed" else "test_audit.completed"
        )
        self._emit(
            event_name,
            _default_event_payload(context, decision, audit_result),
        )
        return audit_result

    def _build_direct_audit(self, context: PostTestContext) -> TestAuditResult:
        """Fallback audit for environments without a dedicated writable sub-agent."""
        guidance = [
            "当前环境未提供专用 test_auditor，已按统一协议走主代理降级路径。",
        ]
        if context.coverage and context.coverage.get("insufficient"):
            guidance.append("覆盖率信号提示存在空洞，建议补充针对高风险路径的测试。")
        if context.risk_points:
            guidance.append("请围绕 risk_points 再次核查断言是否真实验证行为。")
        return TestAuditResult(
            status="completed" if context.allow_direct_audit else "failed",
            executed=context.allow_direct_audit,
            via="direct",
            coverage_assessment="不含真实覆盖率数据时，按启发式规则评估。",
            correctness_assessment="需人工核对关键断言是否验证了真实行为。",
            staleness_assessment="需结合本轮改动确认测试是否因实现演进而过时。",
            blind_spots=list(context.risk_points),
            parent_guidance=guidance,
            issues=[] if context.allow_direct_audit else ["当前环境禁用主代理降级审计。"],
            needs_followup=bool(context.risk_points),
        )

    def _recover_runtime(
        self,
        context: PostTestContext,
        decision: AuditTriggerDecision,
        audit_result: TestAuditResult,
    ) -> RuntimeRecoveryResult:
        """Recover runtime entry when required."""
        if not context.has_runtime_entry:
            return RuntimeRecoveryResult(required=False, strategy="not_required")

        self._emit(
            "runtime_recovery.started",
            _default_event_payload(context, decision, audit_result),
        )
        if self._runtime_recovery_runner is None:
            result = RuntimeRecoveryResult(
                required=True,
                executed=False,
                success=False,
                strategy="manual",
                guidance=["当前环境无自动恢复能力，请手动重启主运行入口。"],
            )
        else:
            result = self._runtime_recovery_runner(context, audit_result)
        self._emit(
            "runtime_recovery.completed",
            {
                **_default_event_payload(context, decision, audit_result),
                "runtime_recovery": result.to_dict(),
            },
        )
        return result

    def _build_acceptance_summary(
        self,
        decision: AuditTriggerDecision,
        audit_result: TestAuditResult,
        retest_results: list[TestCommandResult],
        runtime_recovery: RuntimeRecoveryResult,
    ) -> list[str]:
        """Build acceptance notes from executed pipeline stages."""
        notes = [
            f"test_audit_cycle: {'已触发' if decision.triggered else '未触发'}（{decision.reason}）",
        ]
        if audit_result.executed:
            notes.append(f"测试审计执行方式: {audit_result.via}")
        if audit_result.blind_spots:
            notes.append("测试审计盲区: " + "；".join(audit_result.blind_spots))
        if audit_result.repairs_applied:
            notes.append("已执行修复: " + "；".join(audit_result.repairs_applied))
        if retest_results:
            notes.append(
                "回归结果: "
                + "；".join(
                    f"{result.command}={'PASS' if result.passed else 'FAIL'}"
                    for result in retest_results
                )
            )
        if runtime_recovery.required:
            notes.append(
                f"运行入口恢复: {runtime_recovery.strategy} / "
                f"{'成功' if runtime_recovery.success else '未完成'}"
            )
        return notes

    def _validate_subagent_audit_result(self, audit_result: TestAuditResult) -> list[str]:
        """Validate the minimum contract for a hard-gated sub-agent audit."""
        missing: list[str] = []
        if not audit_result.executed:
            missing.append("executed=true")
        if audit_result.via in {"direct", "not_run", "skipped", "blocked_no_subagent"}:
            missing.append("via=subagent")
        if not audit_result.coverage_assessment:
            missing.append("coverage_assessment")
        if not audit_result.correctness_assessment:
            missing.append("correctness_assessment")
        if not audit_result.staleness_assessment:
            missing.append("staleness_assessment")
        if not audit_result.parent_guidance:
            missing.append("parent_guidance")
        return missing

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit a structured event if an event logger is configured."""
        if self._event_logger is None:
            return
        self._event_logger(event_type, payload)
