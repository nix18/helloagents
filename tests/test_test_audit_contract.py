"""Contract tests for the post-test audit and IDE restart recovery workflow."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    """Read a UTF-8 text file from the repository root."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


class TestAuditWorkflowContract(unittest.TestCase):
    """Ensure the rule-driven workflow exposes the new audit/recovery contracts."""

    def assert_contains_all(self, text: str, expected_tokens: set[str]) -> None:
        """Assert that all expected contract tokens exist in text."""
        for token in expected_tokens:
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_agent_result_schema_exposes_test_audit_fields(self) -> None:
        schema = json.loads(read_text("helloagents/rlm/schemas/agent_result.json"))
        test_audit = schema["properties"]["test_audit"]["properties"]
        expected_fields = {
            "coverage_assessment",
            "correctness_assessment",
            "staleness_assessment",
            "blind_spots",
            "repairs_applied",
            "retest_commands",
            "parent_guidance",
        }
        self.assertTrue(expected_fields.issubset(test_audit))

    def test_develop_stage_declares_post_test_and_post_run_pipeline(self) -> None:
        develop_text = read_text("helloagents/stages/develop.md")
        self.assert_contains_all(
            develop_text,
            {
                "test_audit_cycle",
                "ide_restart_recovery",
                "JetBrains 系 IDE",
                "blind_spots",
                "retest_commands",
            },
        )

    def test_subagent_protocols_declare_test_auditor_contract(self) -> None:
        protocol_text = read_text("helloagents/rules/subagent-protocols.md")
        self.assert_contains_all(
            protocol_text,
            {
                "test_audit_cycle / test_auditor",
                "blind_spots",
                "运行入口恢复（ide_restart_recovery）",
                "覆盖率充分性",
                "测试时效性",
            },
        )

    def test_codex_protocol_declares_audit_loop_and_jetbrains_recovery(self) -> None:
        codex_text = read_text("helloagents/rules/subagent-codex.md")
        self.assert_contains_all(
            codex_text,
            {
                "Codex CLI 下的 test_audit_cycle",
                "spawn_agent(agent_type=\"worker\"",
                "repairs_applied",
                "Codex CLI 下的 ide_restart_recovery",
                "IntelliJ IDEA / PyCharm / WebStorm",
            },
        )

    def test_other_cli_protocol_declares_degraded_contract(self) -> None:
        other_text = read_text("helloagents/rules/subagent-other.md")
        self.assert_contains_all(
            other_text,
            {
                "test_audit_cycle（通用要求）",
                "blind_spots（思维局限/盲区）",
                "ide_restart_recovery（通用要求）",
                "控制电脑类 MCP/skills",
                "手动重启",
            },
        )

    def test_skill_entry_mentions_new_capabilities(self) -> None:
        skill_text = read_text("SKILL.md")
        self.assert_contains_all(
            skill_text,
            {
                "post-test audit cycle",
                "runtime recovery verification",
                "JetBrains-family IDEs",
            },
        )

    def test_readmes_and_agents_sync_high_level_contracts(self) -> None:
        readme_en = read_text("README.md")
        readme_cn = read_text("README_CN.md")
        agents_text = read_text("AGENTS.md")
        self.assert_contains_all(
            readme_en,
            {
                "Post-Test Audit Cycle",
                "Runtime Recovery Verification",
                "JetBrains-family IDEs",
            },
        )
        self.assert_contains_all(
            readme_cn,
            {
                "测试后二次审计闭环",
                "运行入口恢复验证",
                "JetBrains 系 IDE",
            },
        )
        self.assert_contains_all(
            agents_text,
            {
                "测试后二次审计",
                "运行入口恢复验证",
            },
        )


if __name__ == "__main__":
    unittest.main()
