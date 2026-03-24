# ~test 命令 - 运行测试

本模块定义 `~test` 的执行规则。当前版本不再把 `~test` 视为一个只负责“跑测试 + 给失败摘要”的独立工具命令，而是把它作为统一 `post-test pipeline` 的入口之一。

---

## 命令说明

```yaml
命令: ~test
类型: 场景确认类
功能: 探测测试框架、运行测试、按统一 post-test pipeline 决定是否触发 test_audit_cycle、必要时回归并生成结构化总结
评估: 需求理解 + EHRB 检测（不评分不追问）
```

---

## 执行模式适配

```yaml
规则:
  1. `~test` 是统一后测执行器的入口，而不是独立旧流程
  2. 命令本身不改变 WORKFLOW_MODE，但必须共享 DEVELOP 的 post-test 规则
  3. 无法探测测试框架时请求用户提供命令
  4. 触发条件命中时，必须进入 test_audit_cycle，而不是止步于测试结果摘要
  5. 若 `repairs_applied` 非空，必须重新运行受影响测试命令
```

---

## 执行流程

### 步骤1: 需求理解 + EHRB 检测

```yaml
无独立输出，直接进入下一步
```

### 步骤2: 扫描测试环境

```yaml
扫描（多个独立检测项，同一消息中发起并行工具调用）:
  并行读取: package.json / pyproject.toml / Makefile 中的测试配置
  + 常见测试目录（test/tests/__tests__/）与文件命名模式（*_test.*/*.spec.*）
  → 汇总识别测试框架、测试命令、覆盖率工具、可用回归能力

多框架共存: 询问用户选择
无法检测: 请求用户提供测试命令

输出: 确认（测试框架选择）
⛔ END_TURN
用户确认后: 选择框架N / 全部运行 / 输入测试命令 / 取消(→状态重置)
```

### 步骤3: 运行测试并构造统一上下文

```yaml
执行: 运行测试命令（带超时保护）→ 捕获输出和退出码

结果分析:
  1. 解析通过/失败/跳过统计
  2. 提取失败测试的文件路径和行号
  3. 分析错误信息，构造统一 post-test context
  4. 识别 changed_files / test_files / code_test_map / risk_points / coverage 信号

统一要求:
  不得在此处直接结束为“测试结果摘要”
  必须把结果交给统一 post-test pipeline 进行后续决策
```

### 步骤4: 执行统一 post-test pipeline

```yaml
执行器: 统一 post-test pipeline
模式: `test-only`（默认）或 `full`（当用户明确要求连同恢复/验收一起完成时）

固定顺序:
  1. 审计触发判定
  2. 触发时进入 test_audit_cycle
  3. 消费 `blind_spots / repairs_applied / retest_commands / parent_guidance`
  4. `repairs_applied` 非空时强制回归
  5. 需要运行入口恢复时进入 ide_restart_recovery

规则:
  reviewer 保持只读，不得复用 reviewer 代替 test_auditor
  无稳定可写子代理能力时，主代理按同一协议降级执行
  不得把 test_audit_cycle 简化成“再跑一次测试”
```

### 步骤5: 后续操作

```yaml
存在阻断性失败时:
  输出: 确认（测试结果摘要 + 审计结论 + 失败列表 + 回归状态 + 修复建议）
  ⛔ END_TURN
  用户确认后:
    生成修复方案: 创建 plan/YYYYMMDDHHMM_fix-tests/ 方案包
    仅记录报告: 输出完整摘要
    跳过: 不执行后续操作

全部通过时:
  输出完整 post-test 摘要，至少包含:
    - 是否触发了 test_audit_cycle
    - blind_spots
    - repairs_applied
    - retest_commands 与回归结果
    - 是否执行 ide_restart_recovery

→ 状态重置
```

---

## 不确定性处理

| 场景 | 处理 |
|------|------|
| 无法检测测试框架 | 请求用户提供测试命令 |
| 多框架共存 | 列出选项请用户选择 |
| 测试超时 | 询问是否延长或终止 |
| 输出无法解析 | 返回原始输出，并标注未完成统一上下文归一化 |
| 无子代理能力 | 主代理按同一 test_audit 协议降级执行 |
