"""Behavior tests for the post-test pipeline and runtime recovery flow."""

from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from helloagents.rlm.session import SessionManager
from helloagents.runtime.post_test_pipeline import (
    PostTestContext,
    PostTestPipelineExecutor,
    RuntimeRecoveryResult,
    TestAuditResult,
    TestCommandResult,
    render_develop_stage_capsule,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
INJECT_CONTEXT_PATH = REPO_ROOT / "helloagents" / "scripts" / "inject_context.py"


def load_inject_context_module():
    """Load inject_context.py as a module from its file path."""
    spec = importlib.util.spec_from_file_location(
        "helloagents_inject_context",
        INJECT_CONTEXT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def read_text(relative_path: str) -> str:
    """Read a UTF-8 text file from the repository root."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


class TestPostTestPipeline(unittest.TestCase):
    """Validate runtime behavior instead of token-only contracts."""

    def test_runtime_capsule_contains_post_test_and_recovery(self) -> None:
        text = render_develop_stage_capsule()
        for token in (
            "8B 测试审计闭环",
            "test_audit_cycle",
            "blind_spots",
            "repairs_applied",
            "9A 运行入口恢复",
            "ide_restart_recovery",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_runtime_model_aligns_with_schema_contract_fields(self) -> None:
        schema = json.loads(read_text("helloagents/rlm/schemas/agent_result.json"))
        schema_fields = set(schema["properties"]["test_audit"]["properties"])
        runtime_fields = {field.name for field in fields(TestAuditResult)}
        self.assertTrue(schema_fields.issubset(runtime_fields))

    def test_executor_triggers_audit_consumes_result_and_retests(self) -> None:
        events: list[tuple[str, dict]] = []

        def audit_runner(
            context: PostTestContext,
            decision,
        ) -> TestAuditResult:
            self.assertTrue(decision.triggered)
            return TestAuditResult(
                status="completed",
                executed=True,
                via="worker",
                coverage_assessment="覆盖率基本充分",
                correctness_assessment="关键断言验证真实行为",
                staleness_assessment="测试未过时",
                blind_spots=["边界输入仍需人工补测"],
                repairs_applied=["补充 fixture 和一条断言"],
                retest_commands=["pytest tests/test_test_audit_contract.py"],
                parent_guidance=["把 blind_spots 纳入验收摘要"],
            )

        def retest_runner(
            commands: list[str],
            context: PostTestContext,
        ) -> list[TestCommandResult]:
            self.assertEqual(commands, ["pytest tests/test_test_audit_contract.py"])
            return [TestCommandResult(command=commands[0], passed=True)]

        def runtime_runner(
            context: PostTestContext,
            audit_result: TestAuditResult,
        ) -> RuntimeRecoveryResult:
            self.assertTrue(audit_result.executed)
            return RuntimeRecoveryResult(
                required=True,
                executed=True,
                success=True,
                strategy="ide-native",
                notes=["已恢复主运行入口"],
            )

        executor = PostTestPipelineExecutor(
            audit_runner=audit_runner,
            retest_runner=retest_runner,
            runtime_recovery_runner=runtime_runner,
            event_logger=lambda event_type, payload: events.append((event_type, payload)),
        )
        report = executor.execute(
            PostTestContext(
                mode="full",
                trigger_source="DEVELOP",
                changed_files=["helloagents/runtime/post_test_pipeline.py"],
                test_files=["tests/test_test_audit_contract.py"],
                risk_points=["关键路径刚完成重构"],
                has_runtime_entry=True,
                runtime_target="main-run-config",
            )
        )

        self.assertTrue(report.audit_decision.triggered)
        self.assertTrue(report.audit_result.executed)
        self.assertTrue(report.retest_required)
        self.assertEqual(report.runtime_recovery.strategy, "ide-native")
        self.assertTrue(any("边界输入仍需人工补测" in note for note in report.acceptance_summary))
        self.assertEqual(
            [event for event, _ in events],
            [
                "test_audit.triggered",
                "test_audit.dispatched",
                "test_audit.completed",
                "test_audit.retest_started",
                "test_audit.retest_completed",
                "runtime_recovery.started",
                "runtime_recovery.completed",
            ],
        )

    def test_test_file_changes_require_subagent_hard_gate(self) -> None:
        executor = PostTestPipelineExecutor()
        decision = executor.decide_audit_trigger(
            PostTestContext(
                mode="full",
                trigger_source="DEVELOP",
                test_files=["tests/test_demo.py"],
            )
        )

        self.assertTrue(decision.triggered)
        self.assertTrue(decision.requires_writable_subagent)
        self.assertTrue(decision.hard_gate)

    def test_test_file_hard_gate_overrides_explicit_disable(self) -> None:
        executor = PostTestPipelineExecutor()
        decision = executor.decide_audit_trigger(
            PostTestContext(
                mode="full",
                trigger_source="DEVELOP",
                test_files=["tests/test_demo.py"],
                audit_required=False,
            )
        )

        self.assertTrue(decision.triggered)
        self.assertEqual(decision.source, "test_files")

    def test_executor_blocks_when_test_files_changed_without_subagent_runner(self) -> None:
        executor = PostTestPipelineExecutor()
        report = executor.execute(
            PostTestContext(
                mode="full",
                trigger_source="DEVELOP",
                changed_files=["src/demo.py", "tests/test_demo.py"],
                test_files=["tests/test_demo.py"],
            )
        )

        self.assertEqual(report.audit_result.status, "failed")
        self.assertEqual(report.audit_result.via, "blocked_no_subagent")
        self.assertTrue(report.audit_result.needs_followup)
        self.assertEqual(report.runtime_recovery.strategy, "blocked_by_test_audit")

    def test_executor_blocks_direct_audit_for_test_file_changes(self) -> None:
        def direct_like_audit(context: PostTestContext, decision) -> TestAuditResult:
            return TestAuditResult(
                status="completed",
                executed=True,
                via="direct",
                parent_guidance=["pretend direct audit"],
            )

        executor = PostTestPipelineExecutor(audit_runner=direct_like_audit)
        report = executor.execute(
            PostTestContext(
                mode="full",
                trigger_source="DEVELOP",
                changed_files=["tests/test_demo.py"],
                test_files=["tests/test_demo.py"],
            )
        )

        self.assertEqual(report.audit_result.status, "failed")
        self.assertEqual(report.audit_result.via, "blocked_wrong_executor")

    def test_hard_gate_failure_skips_runtime_recovery_runner(self) -> None:
        runtime_called = False

        def runtime_runner(context: PostTestContext, audit_result: TestAuditResult):
            nonlocal runtime_called
            runtime_called = True
            return RuntimeRecoveryResult(required=True, executed=True, success=True)

        executor = PostTestPipelineExecutor(runtime_recovery_runner=runtime_runner)
        report = executor.execute(
            PostTestContext(
                mode="full",
                trigger_source="DEVELOP",
                changed_files=["tests/test_demo.py"],
                test_files=["tests/test_demo.py"],
                has_runtime_entry=True,
                runtime_target="demo",
            )
        )

        self.assertFalse(runtime_called)
        self.assertEqual(report.runtime_recovery.strategy, "blocked_by_test_audit")
        self.assertIn("子代理侧测试审计未完成", report.runtime_recovery.guidance[0])

    def test_executor_blocks_incomplete_subagent_audit_result(self) -> None:
        def incomplete_audit(context: PostTestContext, decision) -> TestAuditResult:
            return TestAuditResult(
                status="completed",
                executed=True,
                via="worker",
                parent_guidance=["missing core fields"],
            )

        executor = PostTestPipelineExecutor(audit_runner=incomplete_audit)
        report = executor.execute(
            PostTestContext(
                mode="full",
                trigger_source="DEVELOP",
                changed_files=["tests/test_demo.py"],
                test_files=["tests/test_demo.py"],
            )
        )

        self.assertEqual(report.audit_result.status, "failed")
        self.assertEqual(report.audit_result.via, "blocked_incomplete_audit")
        self.assertIn("coverage_assessment", report.audit_result.issues[0])

    def test_runtime_capsule_mentions_subagent_gate_for_test_changes(self) -> None:
        text = render_develop_stage_capsule()
        self.assertIn("新增或修改了测试文件", text)
        self.assertIn("子代理侧测试审计", text)

    def test_rule_docs_mark_test_file_changes_as_subagent_hard_gate(self) -> None:
        expectations = {
            "helloagents/stages/develop.md": "必须拿到一份子代理侧 test_audit 结果",
            "helloagents/functions/test.md": "必须完成一次子代理侧 test_audit",
            "helloagents/rules/subagent-protocols.md": "主代理 direct 审计不得用于放行完成态",
            "README.md": "must obtain a sub-agent-side audit result",
            "README_CN.md": "必须执行一次子代理侧 `test_audit_cycle`",
        }
        for relative_path, token in expectations.items():
            with self.subTest(path=relative_path):
                self.assertIn(token, read_text(relative_path))

    def test_test_function_doc_no_longer_allows_direct_fallback_for_test_files(self) -> None:
        text = read_text("helloagents/functions/test.md")
        self.assertIn("新增/修改测试文件触发源 → 视为阻断失败，不得 direct 放行", text)

    def test_inject_context_uses_runtime_capsule_for_develop(self) -> None:
        inject_context = load_inject_context_module()
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            pkg_dir = root / ".helloagents" / "plan" / "202603241800_demo"
            pkg_dir.mkdir(parents=True, exist_ok=True)
            (pkg_dir / "proposal.md").write_text("# demo\n", encoding="utf-8")
            (pkg_dir / "tasks.md").write_text(
                "@status: in_progress\n- [ ] 1.1 demo\n",
                encoding="utf-8",
            )

            result = inject_context.handle_user_prompt_submit(str(root))
            text = result["hookSpecificOutput"]["additionalContext"]
            self.assertIn("test_audit_cycle", text)
            self.assertIn("ide_restart_recovery", text)
            self.assertIn("blind_spots", text)

    def test_session_manager_records_structured_post_test_events(self) -> None:
        session = SessionManager("session_test_post_test_pipeline")
        try:
            session.record_test_audit_event(
                "completed",
                {"trigger_source": "~test", "blind_spots": ["边界输入"]},
            )
            session.record_runtime_recovery_event(
                "completed",
                {"strategy": "manual", "success": False},
            )
            event_types = [event["type"] for event in session.get_events()]
            self.assertIn("test_audit.completed", event_types)
            self.assertIn("runtime_recovery.completed", event_types)
        finally:
            session.cleanup()


if __name__ == "__main__":
    unittest.main()
