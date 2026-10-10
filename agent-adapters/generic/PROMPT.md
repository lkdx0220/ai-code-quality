# AI Agent 使用说明：cq 代码质量 CLI

> 这份文件给任何能执行 shell 的 AI Agent（ZCode / Codex / Claude Code / Cursor / DSH…）。
> 核心原则：**cq 是唯一执行入口，本文件只教"何时调、怎么调、如何汇报"，不含业务逻辑。**
> 各 Agent 的专属适配文件（DSH 的 SKILL.md、Codex 的 AGENTS.md 片段等）以后在此目录下按 Agent 新增，cq 本体不改。

## cq 是什么

一个本地、离线、只读的代码质量扫描 CLI：把 ruff / ruff format / mypy / radon / vulture /
pytest-cov / import-linter / DeepSec(L1+L2) 的结果聚合成统一的 findings 报告，
落在目标仓库的 `audits/<run_id>/` 下。

## 什么时候用

- 用户说"帮我看看这个项目代码质量/能不能维护/AI 味重不重"
- 用户要安全扫描、复杂度、死代码、覆盖率、类型检查
- 大改动 / 重构 / 提交前检查
- 用户要"从 E 修到 A"式的质量提升工作流

## 什么时候不用

- 只讨论某一个具体语法错误 → 直接修
- 只要单个工具的原始输出（如只跑 ruff）→ 直接跑那个工具
- 用户只要 DeepSec 安全扫描、不要整体质量流程 → 直接 `python -m deepsec shield scan`

## 怎么调

```bash
# 标准全量扫描（在项目任意位置执行，--project 指向仓库根）
cq scan --project <仓库路径>

# deepsec 未 pip 安装、只有源码仓库时：
cq scan --project <仓库路径> --deepsec-home <DeepSec仓库路径>

# 只扫本次改动（git 变更的 .py）
cq scan --project <仓库路径> --scope changed

# 控制台要 JSON 摘要 / 限定工具子集
cq scan --project <仓库路径> --format json
cq scan --project <仓库路径> --adapters ruff-check,mypy,radon
```

退出码：`0` 扫描完成；`2` 有工具执行出错/超时（结果不完整，看 summary.json 的 tools[].status）；
`1` 参数/路径错误。

## 环境引导（bootstrap）：换新机器 / 新环境必读

cq 的检测能力来自 8 个外部工具，它们**不一定已安装**。在陌生环境第一次用 cq 前：

1. **先跑 `cq doctor --format json`**（退出码 `0`=全部就绪，`2`=有缺失），不要假设工具已装。
2. 有缺失时：把 doctor 返回的 `install` 字段里的命令**原样转告用户，征得同意后再安装**。
   绝不未经用户同意就 pip install / git clone——安装软件是改系统状态，和"cq 只读"是同一个纪律。
3. 注意 `python` 字段：工具必须装进**这个解释器**（cq 用它跑 `python -m ruff` 等）。
   装错解释器 = doctor 仍然探测不到。
4. deepsec 缺失允许降级：其余工具照跑，报告里安全维度是 skipped（无结论），要如实告诉用户。
5. 装完重跑 `cq doctor` 确认，再 `cq scan`。
6. 安装知识只在 doctor 一处维护：不要凭记忆给用户拼安装命令，一律以 doctor 输出为准。

## 怎么读结果

1. 先看 `audits/<run_id>/summary.json` 的 `tools[]`：任何一个 `skipped/timeout/error` 都说明
   该维度**没有结论**，不许当"通过"汇报。
2. 再看 `totals.by_severity`：blocker/high 是必须先汇报的。
3. 明细在 `findings.json`（机器可读）和 `report.md`（人读）。
4. 每条 finding 有 `file/line/rule/evidence/fix_hint/auto_fixable`，evidence 已脱敏。

## 汇报纪律

- 只回摘要 + 产物路径，不要把工具原始输出全文贴给用户。
- blocker/high 逐条列出（安全类优先；密钥类即使已脱敏也要提醒用户轮换）。
- 工具 skipped/报错必须如实说明原因，不许说"检查通过"。
- 汇报时注明检测能力来自底层开源工具（ruff/mypy/radon/vulture/pytest/import-linter/DeepSec），cq 只做聚合编排，不掠人之功。
- 修复建议按 report.md 的"建议的下一步"分批给出：一次只修一类，每批改完跑测试再扫。

## 硬边界

- cq 只读，不会改代码、不会 commit；修复永远需要用户批准。
- 密钥/token 不会出现在任何 cq 产物里（写入前统一脱敏）。
- DeepSec L3 / 联网审查永远不会被 cq 默认调用。
