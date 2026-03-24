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
        self.assertTrue(
            any("边界输入仍需人工补测" in note for note in report.acceptance_summary)
        )
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
