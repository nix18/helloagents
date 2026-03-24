#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HelloAGENTS 双向上下文注入脚本。

通过 Hook 为主代理和子代理注入最小但关键的上下文，避免阶段规则在长对话、
压缩恢复或不同 CLI 间漂移。
"""

from __future__ import annotations

import io
import json
import re
import sys
import tempfile
from pathlib import Path


if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding="utf-8",
            errors="replace",
        )
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer,
            encoding="utf-8",
            errors="replace",
        )
    if hasattr(sys.stdin, "buffer"):
        sys.stdin = io.TextIOWrapper(
            sys.stdin.buffer,
            encoding="utf-8",
            errors="replace",
        )

try:
    from helloagents.runtime.post_test_pipeline import render_develop_stage_capsule
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runtime.post_test_pipeline import render_develop_stage_capsule


MAX_MAIN_AGENT_CHARS = 20000
MAX_SUBAGENT_CHARS = 15000


DESIGN_RULES = """[HelloAGENTS DESIGN 阶段执行提醒]
你当前处于方案设计阶段，必须按 stages/design.md 执行：

1. 完成 Phase1 上下文收集与复杂度判断。
2. 完成 Phase2 方案构思与方案包生成。
3. R3 标准流程必须做多方案对比，完成后再进入 DEVELOP。
4. 不得跳过方案包验收，不得设计完成后直接写代码。
"""

GENERIC_RULES = """[HelloAGENTS 核心流程提醒]
- G4 路由：R0 直接响应 | R1 快速流程 | R2 简化流程 | R3 标准流程
- G5 阶段链：评估 -> 方案设计 -> 开发实施 -> 知识库同步 -> 完成
- G7 模块加载：进入 DESIGN 读 stages/design.md；进入 DEVELOP 读 stages/develop.md
- G9/G10：满足条件时必须编排子代理，等待真实返回后才能继续
"""


def _fallback_reminder() -> dict:
    """Return a minimal fallback reminder when injection fails."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "CRITICAL: Load and strictly enforce HelloAGENTS protocol from "
                "~/.claude/CLAUDE.md and ~/.claude/rules/helloagents/ before "
                "processing any request."
            ),
        }
    }


def _extract_tasks_status_score(pkg_dir: Path) -> tuple[int, float]:
    """Score a plan package to locate the active package more reliably."""
    score = 0
    proposal_path = pkg_dir / "proposal.md"
    tasks_path = pkg_dir / "tasks.md"
    status_path = pkg_dir / ".status.json"

    if proposal_path.is_file():
        score += 1
    if tasks_path.is_file():
        score += 2

    if status_path.is_file():
        try:
            status_data = json.loads(status_path.read_text(encoding="utf-8"))
            if status_data.get("status") != "completed":
                score += 5
            return score, status_path.stat().st_mtime
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass

    if tasks_path.is_file():
        try:
            content = tasks_path.read_text(encoding="utf-8")
            if "@status: in_progress" in content:
                score += 4
            if "[ ]" in content or "[?]" in content:
                score += 3
            if "[√]" in content or "[X]" in content:
                score += 1
        except (OSError, UnicodeDecodeError):
            pass
        return score, tasks_path.stat().st_mtime

    return score, pkg_dir.stat().st_mtime


def _get_active_package(plan_dir: Path) -> Path | None:
    """Return the most likely active package in .helloagents/plan."""
    pkg_dirs = [pkg for pkg in plan_dir.iterdir() if pkg.is_dir()]
    if not pkg_dirs:
        return None
    return sorted(pkg_dirs, key=_extract_tasks_status_score)[-1]


def detect_stage(cwd: str) -> str:
    """Detect the current HelloAGENTS stage from .helloagents/plan."""
    plan_dir = Path(cwd) / ".helloagents" / "plan"
    if not plan_dir.is_dir():
        return ""

    active_pkg = _get_active_package(plan_dir)
    if active_pkg is None:
        return ""

    proposal_path = active_pkg / "proposal.md"
    tasks_path = active_pkg / "tasks.md"
    if tasks_path.is_file() and proposal_path.is_file():
        return "DEVELOP"
    if proposal_path.is_file():
        return "DESIGN"
    return ""


def extract_critical_rules(content: str) -> str:
    """Extract CRITICAL-marked sections from AGENTS.md/CLAUDE.md."""
    lines = content.splitlines()
    blocks: list[str] = []
    in_critical = False
    current: list[str] = []

    for line in lines:
        if "CRITICAL" in line.upper():
            if current:
                blocks.append("\n".join(current))
            current = [line]
            in_critical = True
            continue

        if in_critical and re.match(r"^#{1,3}\s", line):
            if current:
                blocks.append("\n".join(current))
            current = []
            in_critical = False
            continue

        if in_critical:
            current.append(line)

    if current:
        blocks.append("\n".join(current))

    result = "\n---\n".join(blocks)
    if len(result) > MAX_MAIN_AGENT_CHARS:
        result = result[:MAX_MAIN_AGENT_CHARS] + "\n...(已截断)"
    return result


def _get_active_agents_context() -> str:
    """Read active child-agent state from the latest session metadata."""
    try:
        session_root = Path(tempfile.gettempdir()) / "helloagents_rlm"
        if not session_root.is_dir():
            return ""

        sessions = sorted(
            (
                item
                for item in session_root.iterdir()
                if item.is_dir() and item.name.startswith("session_")
            ),
            key=lambda item: item.stat().st_mtime,
        )
        if not sessions:
            return ""

        metadata_path = sessions[-1] / "metadata.json"
        if not metadata_path.is_file():
            return ""

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        latest_by_agent: dict[str, dict] = {}
        for record in metadata.get("agent_history", []):
            agent_id = record.get("agent_id", "")
            if agent_id:
                latest_by_agent[agent_id] = record

        active = [
            record
            for record in latest_by_agent.values()
            if record.get("status") not in {"completed", "failed", "cancelled"}
        ]
        if not active:
            return ""

        lines = ["[活跃子代理]"]
        for record in active:
            lines.append(
                "- "
                f"{record.get('agent_id', '?')}: "
                f"role={record.get('role', '?')}, "
                f"task={record.get('task', '?')[:60]}, "
                f"status={record.get('status', '?')}"
            )
        return "\n".join(lines)
    except Exception:
        return ""


def _build_develop_context(agents_ctx: str) -> str:
    """Build injected DEVELOP context from the runtime fact source."""
    ctx = render_develop_stage_capsule()
    if agents_ctx:
        ctx += "\n\n" + agents_ctx
    return ctx


def handle_user_prompt_submit(cwd: str) -> dict:
    """Inject main-agent context according to the detected stage."""
    cwd_path = Path(cwd)
    stage = detect_stage(cwd)
    agents_ctx = _get_active_agents_context()

    if stage == "DEVELOP":
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": _build_develop_context(agents_ctx),
            }
        }

    if stage == "DESIGN":
        ctx = DESIGN_RULES
        if agents_ctx:
            ctx += "\n\n" + agents_ctx
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": ctx,
            }
        }

    rule_file = None
    for name in ("CLAUDE.md", "AGENTS.md"):
        candidate = cwd_path / name
        if candidate.is_file():
            rule_file = candidate
            break

    if rule_file is None:
        return _fallback_reminder()

    try:
        content = rule_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _fallback_reminder()

    summary = GENERIC_RULES + "\n---\n" + extract_critical_rules(content)
    if agents_ctx:
        summary += "\n\n" + agents_ctx
    if len(summary) > MAX_MAIN_AGENT_CHARS:
        summary = summary[:MAX_MAIN_AGENT_CHARS] + "\n...(已截断)"

    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"[HelloAGENTS] 规则提醒（来自 {rule_file.name}）\n{summary}"
            ),
        }
    }


def handle_subagent_start(cwd: str) -> dict:
    """Inject the latest plan package context for child agents."""
    ha_dir = Path(cwd) / ".helloagents"
    if not ha_dir.is_dir():
        return {}

    parts: list[str] = []

    context_path = ha_dir / "context.md"
    if context_path.is_file():
        try:
            context_text = context_path.read_text(encoding="utf-8").strip()
            if context_text:
                parts.append(f"## 项目上下文\n{context_text[:4000]}")
        except (OSError, UnicodeDecodeError):
            pass

    guidelines_path = ha_dir / "guidelines.md"
    if guidelines_path.is_file():
        try:
            guidelines_text = guidelines_path.read_text(encoding="utf-8").strip()
            if guidelines_text:
                parts.append(f"## 技术指南\n{guidelines_text[:3000]}")
        except (OSError, UnicodeDecodeError):
            pass

    plan_dir = ha_dir / "plan"
    active_pkg = _get_active_package(plan_dir) if plan_dir.is_dir() else None
    if active_pkg is not None:
        proposal_path = active_pkg / "proposal.md"
        tasks_path = active_pkg / "tasks.md"

        if proposal_path.is_file():
            try:
                proposal_text = proposal_path.read_text(encoding="utf-8").strip()
                if proposal_text:
                    parts.append(f"## 当前方案 ({active_pkg.name})\n{proposal_text[:6000]}")
            except (OSError, UnicodeDecodeError):
                pass

        if tasks_path.is_file():
            try:
                tasks_text = tasks_path.read_text(encoding="utf-8").strip()
                if tasks_text:
                    parts.append(f"## 任务清单\n{tasks_text[:1500]}")
            except (OSError, UnicodeDecodeError):
                pass

        parts.append(
            "## DEVELOP 后测闭环摘要\n"
            + render_develop_stage_capsule()[:2500]
        )

    if not parts:
        return {}

    combined = "\n\n".join(parts)
    if len(combined) > MAX_SUBAGENT_CHARS:
        combined = combined[:MAX_SUBAGENT_CHARS] + "\n...(已截断)"

    return {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": (
                "[HelloAGENTS] 方案包上下文（自动注入）:\n"
                + combined
            ),
        }
    }


def main() -> None:
    """Dispatch hook events from stdin."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    event_map = {
        "BeforeAgent": "UserPromptSubmit",
    }
    event = event_map.get(data.get("hookEventName", ""), data.get("hookEventName", ""))
    cwd = data.get("cwd", ".")

    if event == "UserPromptSubmit":
        result = handle_user_prompt_submit(cwd)
    elif event == "SubagentStart":
        result = handle_subagent_start(cwd)
    else:
        sys.exit(0)

    if result:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
