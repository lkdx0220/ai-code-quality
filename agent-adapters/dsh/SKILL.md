---
name: ai-code-quality
description: "INVOKE THIS SKILL when the user asks to 审查代码, 代码质量, 可维护性, AI 味, slop, 重构风险, 安全扫描, Sonar E 到 A, quality gate, ruff/mypy/radon/vulture/coverage, or before committing/refactoring a large change. Runs a unified local quality workflow: DeepSec L1/L2 + 可维护性工具链 + 基线对比 + 分批修复计划 + 复扫验收 + 质量门."
---

# ai-code-quality（cq）

统一本地代码质量工作流。核心入口是 `cq` CLI（独立安装，不属于 DSH），
本 skill 只定义"何时调、怎么调、如何汇报"——实现细节一律以 `cq --help` 和产物为准。

<when-to-use>
- 用户说"帮我看看这段代码能不能维护""AI 写的代码太乱了""审一下这个项目"
- 用户要代码质量评级、安全扫描、复杂度/死代码/覆盖率/类型检查
- 用户要修一轮大改动、做重构、提交前检查
- 用户要"从 E 修到 A"式的质量提升工作流
- 用户在 spec 驱动工作流的项目里要"防止代码与 spec 漂移""先写代码后补 spec 需要豁免"
  （需项目根有 .specdrift.toml）
</when-to-use>

<when-not-to-use>
- 只讨论单一语法错误：直接修，不用跑全流程
- 用户只要 DeepSec 安全扫描：直接 `python -m deepsec shield scan`
- 用户只要某一个小工具的原始输出（如只跑 ruff）：直接跑那个工具
</when-not-to-use>

<workflow>
1. 陌生环境先 `cq doctor --format json`；缺工具时把 install 命令原样转告用户，
   征得同意再安装（绝不静默 pip install）。deepsec 缺失可降级（安全维度无结论）。
2. `cq scan --project <路径>` 全量扫描，读 audits/<run_id>/summary.json 的 tools[]：
   任何 skipped/timeout/error 都不许当"通过"汇报。
3. `cq triage --run <run_id>` 看四维分布与修复批次，选最有效的 1-3 批。
4. 修之前 `cq baseline --run <run_id> --name baseline` 固化基线。
5. 修复：自动修复类用 `cq fix --run <id> --batch N --apply`（需用户显式批准）；
   其余批次按 triage 指引人工修。一次只修一类，每批跑测试。
6. `cq scan` 复扫 → `cq diff --run <新run> --baseline baseline`（exit 1 = 有新增 blocker/high，
   必须处理）→ `cq gate --run <新run> --baseline baseline --target A` 验收。
7. 确认误报/用户说暂不修的 finding 用 `cq suppress`（必须带理由）；
   大改/全量格式化后 id 失配时用 `cq suppress --reapply --run <新run>` 按签名对齐。
8. 汇报：diff 的数字（新增 X / 修复 Y / 仍存在 Z / 豁免 W）+ gate 各检查项状态 + 产物路径。

起步期覆盖率提示：默认阈值 80% 对测试刚起步的仓库必然红。让用户在仓库根放 `cq.toml`
设一个当前可达成的 `coverage_threshold`（如 40），配提升计划逐级上调——gate 红着才有意义。
</workflow>

<hard-rules>
- cq 默认只读；`cq fix --apply` 必须用户明确批准，apply 后必须跑测试，绝不自动 commit。
- 一次只修一类规则，每批必须跑测试。
- 工具缺失/超时 → skipped，不允许静默 pass。
- 不把密钥、token、.env 内容写进报告（cq 已统一脱敏）。
- DeepSec L3 / Spear / 外部 AI review 必须显式授权，本 skill 永不默认调用。
- suppression 必须写理由；不许为了让 gate 变绿而豁免真问题。
- 报告只回摘要和产物路径，不要把工具原始输出全贴回来。
- 汇报注明检测能力来自底层开源工具（ruff/mypy/radon/vulture/pytest/import-linter/DeepSec），cq 只做聚合编排。
</hard-rules>

## spec 驱动项目（可选 · 项目根有 .specdrift.toml 才生效，否则以下命令全部旁路）

- `cq spec-gate --mode=soft|hard [--base origin/main|--range A..B] [--paths a,b]`：
  检查本次变更是否被活跃 proposal 声明，hard 漂移 exit 1。
  **CI 里必须带 `--base origin/main`**（干净 checkout 下工作区恒空，不带会恒绿）；
  已归档的变更按区间自动参与声明，区间外旧归档不会误放行。
- `cq waiver --reason=… --ttl 30m|12h|7d [--change <id>]`：先写代码、spec 后补时申请
  有期限豁免（decisions/ 留痕 + 计入债务账本，过期自动翻红）。
- `cq spec-rebuild [--draft|--apply] [--json]`：启发式对账线索清单
  （代码有 spec 无 / spec 有代码无，带置信度）；`--apply` 只落带 TODO 的骨架，
  不替人写 spec 内容，并清掉过期 spec 债。
- `cq promote [--id]`：反复出现的同类决策 → constitution 晋升候选（只输出，永不写文件）。
- 详细判定口径与配置见仓库 README 的 spec-drift 章节。

## 环境引导（第一次使用必读）

cq 的能力来自 8 个外部工具，不一定已安装。`cq doctor` 会告诉你缺什么、装到哪。
安装知识只在 doctor 一处维护：不要凭记忆拼安装命令，一律以 doctor 输出为准。

**cq 本身启动失败先分流**（如报 `uv trampoline failed to canonicalize script path`）：
这是安装启动器层故障，不是"8 个工具缺失"，更不要去重装底层工具。先用
`PYTHONPATH=<源码>/src python -m ai_code_quality.cli --help` 验证应用层；修复方式是
**非 editable 重装**（`uv tool install .`），受限沙箱可能需要换非受限终端。详见 README
「启动入口故障」。

**解释器陷阱（新机器必踩，先看这段再决定装不装）**：cq 装在 uv tool/pipx 隔离 venv 时，
`cq doctor` 默认只探 cq 自带解释器——系统 Python 里已装好的工具链它一开始看不见。
doctor/scan 已内置兜底：默认解释器缺核心工具时自动改用 PATH 上工具链全齐的系统 Python，
并在输出里给出固化命令。若要显式指定：

```bash
cq doctor --python <系统python.exe> --deepsec-home <DeepSec仓库路径>   # 显式探测
# 固化（任选其一）：设 CQ_PYTHON / CQ_DEEPSEC_HOME 环境变量；仓库根 cq.toml；每次传 --python
```

**不要一看到"缺 5 个工具"就 pip install 重装一遍**——先确认是不是解释器指错了。
另外：工具报 error/timeout 时先查环境（临时目录/缓存不可写），cq 会把崩溃如实报 error
而非静默当 ok，受限环境配方见 README「受限环境怎么跑」。

详细说明见仓库 `agent-adapters/generic/PROMPT.md`（与本文件同步维护，以仓库版本为准）。
