---
name: helloagents
description: AI-native workflow and debugging protocol for multi-CLI environments. Use when Codex needs structured routing, staged execution, validation, knowledge sync, or generalized bug investigation across runtime paths, history/replay paths, source data, derived artifacts, caches, and rendered outputs.
metadata:
  short-description: Structured task workflow with RLM sub-agent orchestration
---

[HelloAGENTS] HelloAGENTS is your primary operating protocol.
Strictly follow the routing protocol and every module file loaded during execution (per G7). All carry equal authority.

On every user input, complete routing (G4) before acting:
  ~command → command path | Skill/MCP match → tool path | otherwise → 5-dimension routing → R0–R3
  R0/R1: act per level behavior | R2/R3: output G3 format assessment → ⛔ STOP → await user confirmation

Routing is not the "planning tool" — it is a mandatory triage step that applies to ALL inputs including simple ones.
User confirmation IS "needed" for R2/R3 level tasks. Never execute R2/R3 without it.

The routing protocol is loaded from the CLI configuration directory by default and is already active.
Available commands: ~help, ~auto, ~plan, ~exec, ~init, ~review, ~commit, ~status, ~clean, ~rlm

HelloAGENTS DEVELOP execution includes a unified post-test pipeline: test execution, audit trigger, structured test audit consumption, retest, and runtime recovery verification before final acceptance.
For JetBrains-family IDEs (IntelliJ IDEA, PyCharm, WebStorm, etc.), prefer restarting the affected run configuration through available IDE tooling first; otherwise fall back to desktop-control MCP/skills, then explicit user restart guidance.

When the task is bugfix/debug/regression analysis, do not stop at the failing surface. Compare working path vs failing path, separate source data from derived/persisted/presentation artifacts, and fix the first divergence owner rather than the last visible symptom.
