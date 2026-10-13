# ai-code-quality（`cq`）· AI Agent Skill

[![CI](https://github.com/lkdx0220/ai-code-quality-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/lkdx0220/ai-code-quality-skill/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**这是一个 AI Agent Skill**：一份可安装到 DSH / Claude Code / Codex / Cursor 等任何 AI Agent 的
说明文件（[`agent-adapters/`](agent-adapters/)），外加一个由它驱动的本地 CLI `cq`——
把 AI 写的代码按可复现的流程，从"能跑但 AI 味重"带到质量门 A 级。

> **🆕 v0.3.0 · spec-drift 二期**：`cq spec-rebuild`（启发式逆向对账——代码符号 vs spec 文本
> 的**线索清单**，带置信度；`--draft` 只读、`--apply` 落骨架模板并清过期 spec 债）与
> `cq promote`（反复出现的同类决策 → constitution 晋升候选与草稿，**永不自动写入**）。
> v0.2.0 起的 **spec-drift 防漂移门**（`spec-gate` 强制变更声明 / `waiver` 带期限豁免）继续可用；
> **默认关闭**：项目根放 `.specdrift.toml` 才启用，未配置的仓库行为逐项不变（CI 专项回归锁死）。
> 👉 详见下方 [spec-drift 章节](#spec-driftv020--可选spec-驱动项目的防漂移门)。

## 它解决什么问题

AI 编程助手让"能跑的代码"变得廉价，也让这类问题变得高频：

- 空 `except` 吞错、`NotImplementedError` 占位、复制粘贴的重复实现
- 零类型标注、圈复杂度爆炸的百行函数、测试覆盖率接近零
- 硬编码密钥进了仓库、`debug=True` 直接上线
- 修 A 坏 B：没有基线，改动无法归因，"上次到底改好了没有"说不清

传统应对只有两条路：**人肉审查**（不可复现、不可审计、换个 AI 结论就变），或者**上 Sonar**
（要服务器/账号/联网，规则黑盒，AI 也只是拿到一个分数）。

这个 skill 是第三条路：把"从烂到 A"的整套工作流**本地化、可复现、可审计**——

- **检测交给 8 个开源工具**（ruff/mypy/radon/vulture/pytest/import-linter/DeepSec…）：
  规则透明、版本可查、人人可复现；
- **聚合交给 cq**：统一 finding schema、统一严重度与产物链、基线对比、质量门判定、豁免留痕；
- **语义与执行交给 AI Agent**：读报告定批次、动手修（每一步经你批准）、运行时验证行为——
  这些是确定性工具做不了、而 Agent 真正擅长的事。

三者各干各的，谁也不替谁编造结论。

## 它如何运作

双形态架构：**SKILL.md 是 Agent 的行为说明书，cq 是确定性的执行核心**。

```
你："帮我审一下这个项目的代码质量"
         │
         ▼
┌────────────────────────────────────────────────┐
│ AI Agent（DSH / Claude Code / Codex / ZCode /…）│
│  读 SKILL.md：何时调 cq、怎么分批、何时必须停下问你 │
└───────────────────┬────────────────────────────┘
                    │  cq scan / triage / fix / gate …
                    ▼
┌────────────────────────────────────────────────┐
│ cq CLI（确定性执行核心，纯标准库、零运行时依赖）    │
│  解析扫描目标 → subprocess 调 8 个外部工具        │
│  → 统一 finding schema → 密钥脱敏                │
│  → 落盘 audits/<run_id>/（summary/findings/     │
│     report/gate/fix-plan/diff，全部可复核）       │
└───────────────────┬────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────────┐
│ ruff   mypy   radon   vulture   pytest   DeepSec │
│       （检测能力全部来自开源工具，cq 不重新发明）    │
└────────────────────────────────────────────────┘
```

工作流是一台状态机，每一步都有命令、有产物、有退出码：

```
scan ──► triage ──► baseline ──► fix（分批+跑测试）──► rescan ──► diff ──► gate
 │                                                  ▲                   │
 └────────────── suppress 豁免（必须写理由，落盘留痕）◄───── 未达标就再来一轮 ◄──┘
```

关键机制（详见下文各节）：

| 机制 | 解决什么 |
|---|---|
| 统一 finding schema（sha1 id / severity / category） | 8 个工具的输出变成同一种货币，diff/gate 才有比较基准 |
| 产物链 `audits/<run_id>/` | 每次扫描都有 summary/findings/report/gate JSON，可复核可对比 |
| 基线 + diff（行号漂移不算新增） | "修 A 坏 B"当场现形：新增 blocker/high，diff 退出码直接非 0 |
| gate 质量门（0/1/2/3 退出码） | 工具不完整=2、有 blocker=1、达标=0，AI 和 CI 都能直接判定 |
| 豁免留痕 `.cq/suppressions.json` | 误报不是口头说说：理由、时间、来源 run 全部落盘可评审 |
| apply 门禁 + 只读默认 | 改代码只有 `fix --apply` 一个入口且需你批准；其余一切只读 |

## 端到端：一次完整的质量修复长什么样

以下是一次真实运行（本仓库自举，数字可复核）：

1. **你说**："帮我审一下这个项目的代码质量。"
2. **Agent 体检环境**：`cq doctor`——8 个工具谁没装、装到哪个解释器，缺了先问你。
3. **Agent 全量扫描**：`cq scan` → 控制台给出工具状态表 + 分级统计，
   `audits/<run_id>/report.md` 里是每条 finding 的文件/行号/规则/证据（密钥已脱敏）。
4. **归因**：`cq triage` 把 findings 分成四维（安全/可靠性/可维护性/测试）并给出批次建议——
   例如"安全类先人工核对、188 条可自动修复放最后"。
5. **固化基线**：`cq baseline`——从这一刻起，"修没修好、有没有引入新问题"有了裁判。
6. **分批修复**：自动批次 `cq fix --batch 1 --apply`（要你批准）；复杂度/安全批次按指引人工修；
   每批跑测试。
7. **复扫对比**：`cq diff` → "新增 X（其中 blocker/high N）/ 修复 Y / 仍存在 Z"；
   行号漂移不算新增，格式化 100 个文件不会制造 300 条假警报。
8. **验收**：`cq gate --target A` → 逐项检查 + local_rating 评级 + 退出码；
   AI 据此向你汇报："还差覆盖率一项，其余全绿。"

本仓库自己的战绩：**73 条 findings → 3 条**（1 条覆盖率债务 + 2 条已留痕豁免的误报），
五个批次、每批都有测试和 CI 背书——这条流水线首先吃的是自己的狗粮。

## 30 秒上手

```bash
# 一行安装（任选其一）
pipx install git+https://github.com/lkdx0220/ai-code-quality-skill
uv tool install git+https://github.com/lkdx0220/ai-code-quality-skill
pip install -e .   # 开发态

cq doctor                     # ① 环境体检：缺什么工具，按提示装（系统已有会自动发现）
cq scan --project <你的仓库>   # ② 扫描 → audits/<run_id>/report.md
cq gate --run <run_id> --target A   # ③ 质量门：blocker/high/复杂度/覆盖率/新增，exit 0=达标
```

## 怎么接进你的 AI Agent（适配器矩阵）

| Agent | 接法 | 文件 |
|---|---|---|
| DSH | 复制到各 home 的 `skills/ai-code-quality/` | [`agent-adapters/dsh/SKILL.md`](agent-adapters/dsh/SKILL.md) |
| Claude Code | 复制到 `~/.claude/skills/ai-code-quality/` | 同上（SKILL.md 通吃） |
| Codex | 片段追加到项目/用户级 `AGENTS.md` | [`agent-adapters/codex/AGENTS.md.snippet`](agent-adapters/codex/AGENTS.md.snippet) |
| Cursor | 放进 `.cursor/rules/` | [`agent-adapters/cursor/ai-code-quality.mdc`](agent-adapters/cursor/ai-code-quality.mdc) |
| 任何 Agent | 直接整份喂给模型 | [`agent-adapters/generic/PROMPT.md`](agent-adapters/generic/PROMPT.md) |

装好后对 Agent 说"帮我审一下这个项目的代码质量"即可；各 harness 安装点与同步命令见
[`agent-adapters/README.md`](agent-adapters/README.md)。

## 设计原则

- **核心 CLI + 薄适配层**：`cq` 不依赖任何 Agent 框架（DSH/Codex/ZCode 各自只需一份说明文件，见 `agent-adapters/`）。
- **零运行时依赖**：核心包只用 Python 标准库；外部工具全部 subprocess 调用。
- **不许静默 pass**：工具缺失/超时/解析失败 → `skipped` / `timeout` / `error` + 原因，绝不当作通过。
- **只读**：不改代码、不 commit；所有工具缓存重定向到 `audits/`，目标仓库 `git status` 不变（只多 `audits/`）。
- **扫描范围与工具对齐**：扫描遵循目标仓库根 `.gitignore`（目录名/通配/相对路径的轻量子集，
  不支持 `!` 否定——见到会提示；精确排除用 `cq.toml` 的 `respect_gitignore = false` 关闭或另行配置）。
- **脱敏**：密钥/token 在任何产物落盘前统一替换为 `***REDACTED***`（含 raw 原始输出）。
- **离线**：DeepSec L3 / SonarCloud / AI review 一律 opt-in，当前版本未实现、不会调用。

## 开源工具声明与致谢（必读）

**cq 是扫描编排器（orchestrator）：它不包含、不分发、不修改下列任何工具的源代码**，
只是在运行时通过命令行调用用户环境中已安装的这些工具，并聚合其输出。
全部检测能力来自这些优秀的开源项目，版权归各自作者，遵循各自许可证：

| 工具 | 在 cq 中的作用 | 许可证 | 上游 |
|---|---|---|---|
| [ruff](https://github.com/astral-sh/ruff) | lint、未使用导入、危险写法、格式检查 | MIT | Astral |
| [mypy](https://github.com/python/mypy) | 静态类型检查 | MIT | python/mypy |
| [radon](https://github.com/rubik/radon) | 圈复杂度、可维护性指数 | MIT | rubik/radon |
| [vulture](https://github.com/jendrikseipp/vulture) | 死代码检测 | MIT | jendrikseipp/vulture |
| [pytest](https://github.com/pytest-dev/pytest) | 测试执行 | MIT | pytest-dev |
| [pytest-cov](https://github.com/pytest-dev/pytest-cov) | 覆盖率统计 | MIT | pytest-dev |
| [import-linter](https://github.com/seddony/import-linter) | 架构依赖契约检查 | **BSD 2-Clause** | seddony/import-linter |
| [DeepSec](https://github.com/Unclecheng-li/DeepSec) | 安全扫描 L1/L2（硬编码密钥、危险写法、注入） | MIT | Unclecheng-li/DeepSec |

cq 自身代码为原创，以 MIT 许可证发布（见 `LICENSE`）。
如果你在报告中引用 cq 的扫描结果，建议同时注明底层工具，例如
"扫描结果由 ruff / mypy / radon / vulture / pytest / import-linter / DeepSec 产出，cq 聚合"。

## 前置工具：跑 `cq doctor`，缺什么按提示装

cq 是编排器，检测能力来自上表 8 个外部工具，**它们需要你自行安装**（cq 不捆绑、不自动安装）。
装没装齐，一条命令看结果：

```bash
cq doctor                # 人读：✅/❌ + 版本 + 缺失工具的安装命令
cq doctor --format json  # 给 AI Agent 消费（见 agent-adapters/generic/PROMPT.md 的 bootstrap 纪律）
```

**解释器提示（uv tool / pipx 安装的必读）**：cq 装在隔离 venv 时，默认只探自带的解释器，
系统 Python 里已装好的工具链它一开始看不见——别急着 pip install 重装一遍。
doctor/scan 已内置兜底：默认解释器缺核心工具时自动改用 PATH 上工具链全齐的系统 Python，
并在输出里给出固化命令（`CQ_PYTHON` 环境变量 / 仓库根 `cq.toml` / `--python`，任选其一）。
deepsec 的源码仓库路径同理用 `CQ_DEEPSEC_HOME` 固化。显式复核：

```bash
cq doctor --python <系统python.exe> --deepsec-home <DeepSec仓库路径>
```

手动安装（装进 cq 使用的那个 Python，默认当前解释器，`CQ_PYTHON` 可覆盖）：

```bash
# 一次装齐 6 个核心+可选（MIT 许可，PyPI 直装）
pip install ruff mypy radon vulture pytest pytest-cov import-linter

# deepsec 二选一：
pip install git+https://github.com/Unclecheng-li/DeepSec        # 形态一：pip 安装
git clone https://github.com/Unclecheng-li/DeepSec <任意路径>    # 形态二：源码 + 环境变量
set CQ_DEEPSEC_HOME=<clone 出来的路径>                            #   cq 经 PYTHONPATH 调用，无需安装
```

实测通过的版本（2026-10 · Python 3.13 · Windows）：ruff 0.16.9、mypy 2.3.1、radon 6.0.1、
vulture 2.16、pytest 9.1.1、pytest-cov 7.1.0、import-linter 2.15、deepsec 0.2.0。
ruff 等工具的默认规则集会随版本变化，跨版本对比扫描结果时留意。

缺工具不会阻塞 `cq scan`（对应工具显示 `skipped` + 原因），但相应维度没有结论；
其中 ruff/mypy/radon/vulture/pytest 是核心，pytest-cov/import-linter/deepsec 属可选增强。

### 受限环境（CI 容器 / 只读缓存 / 杀软拦截）怎么跑

临时目录或缓存不可写时，工具会以各种姿势崩溃——cq 会把崩溃**如实报为 error 而不是静默当 ok**
（含"pytest 退出码 1 但零结果"的启动崩溃防御），但更好的做法是主动重定向：

```powershell
$env:TEMP = $env:TMP = "<可写目录>"                          # 临时目录
ruff check --no-cache src tests                              # 免缓存
ruff format --check --no-cache src tests
mypy --cache-dir "<可写目录>/mypy" src                       # 缓存重定向（被拒时会伪装成 INTERNAL ERROR）
pytest tests --basetemp="<可写目录>/pt" -p no:cacheprovider  # 注意：basetemp 不能是 cwd 或其父目录
```

看到 `mypy INTERNAL ERROR` 或 `pytest No usable temporary directory` 时，先查写权限再怀疑工具本身。

## 安装与运行

```bash
# 方式一：直接从源码跑（开发态）
cd ai-code-quality
PYTHONPATH=src python -m ai_code_quality scan --project <目标仓库>

# 方式二：装到 PATH（推荐，供任何 Agent 直接调 cq）
uv tool install --editable .
#   注意：隔离 venv 里没有 ruff/mypy 等工具时，用 CQ_PYTHON 指到装了工具的解释器：
#   set CQ_PYTHON=C:/Python313/python.exe
# 或 pipx install . / pip install -e .

# PowerShell 里（文件产物一律 UTF-8）
python -m ai_code_quality scan --project . --format md
```

## 用法

```
cq scan --project <path> [--scope full|changed] [--format json|md] [--timeout N]
        [--adapters ruff-check,ruff-format,mypy,radon,vulture,pytest,import-linter,deepsec]
        [--python <解释器>] [--deepsec-home <DeepSec源码仓库>] [--deepsec-python <解释器>]
        [--no-deepsec]

cq doctor [--format json|md] [--python <解释器>] [--deepsec-home <路径>] [--deepsec-python <解释器>]

cq triage    --run <run_id> [--project <path>] [--format json|md]
cq baseline  --run <run_id> [--name baseline] [--project <path>]
cq diff      --run <run_id> --baseline <基线名或run_id> [--project <path>] [--format json|md]
cq gate      --run <run_id> [--baseline <基线名或run_id>] [--target A|B] [--project <path>] [--format json|md]
cq suppress  --run <run_id> --finding <id前缀≥8位> --action false_positive|debt_accepted --reason "必填理由"
cq suppress  --run <run_id> --reapply                # 存量豁免按签名对齐到新 run（行号漂移后用）
cq fix       --run <run_id> [--batch N] [--apply] [--project <path>] [--format json|md]

# 以下两条需要项目根存在 .specdrift.toml（见「spec-drift」章节），否则旁路
cq spec-gate --mode soft|hard [--paths a,b] [--project <path>]   # hard 漂移 exit 1
cq waiver     --reason "必填理由" --ttl 30m|12h|7d [--change <id>] [--project <path>]
cq spec-rebuild [--from code] [--draft|--apply] [--json] [--project <path>]  # 启发式对账（默认 --draft 只读）
cq promote     [--id <decision标识>] [--project <path>]   # 晋升候选与草稿，永不写文件
```

`scan` 退出码：`0` 完成；`1` 参数错误；`2` 有工具 error/timeout（结果不完整但不丢弃）。
`doctor` 退出码：`0` 全部工具就绪；`2` 有缺失（不阻塞 scan，供脚本/Agent 判断）。
`diff` 退出码：`0` 无新增 blocker/high；`1` 有新增（供 CI/Agent 直接判断）；`3` run/基线解析失败。
`gate` 退出码：`0` 达标；`1` 未达标（含新增 blocker/high）；`2` 工具执行错误（结果不完整）；`3` 配置/加载错误。

## Phase 2 工作流：基线 → 修复 → 对比 → 质量门

```bash
cq scan --project .                          # 1. 全量扫描
cq baseline --run <run_id> --name baseline   # 2. 固化为基线（自包含，源 run 删了也能用）
#   ……分批修复，一次只修一类，每批跑测试……
cq scan --project .                          # 3. 复扫
cq diff --run <新run_id> --baseline baseline # 4. new/fixed/existing/suppressed
cq gate --run <新run_id> --baseline baseline --target A   # 5. 质量门验收
```

- **triage**：四维归类（安全/可靠性/可维护性/测试）+ 每条 finding 的处置建议
  （auto_fixable / needs_test / needs_refactor / needs_decision / needs_review）+ 最有效的修复批次。
- **diff**：两层匹配——精确 id 相同算 existing；同 (source,rule,file,message) 但行号漂移算
  existing(moved)，不算 new/fixed，避免行号平移制造假警报。
- **gate**：目标 A = 0 blocker / 0 high / 0 复杂度 D-E-F / 覆盖率 ≥ 阈值 / 新增 blocker/high = 0；
  目标 B 放宽到 ≤2 high。工具有 error/timeout 时直接 exit 2（结果不完整不许当达标）。
  附带 local_rating（security/reliability/maintainability 的 A-E 本地近似口径，非 Sonar 官方评级）。
- **suppress**：确认误报/暂不修的 finding 走豁免，**必须写理由**，落 `<项目>/.cq/suppressions.json`
  （**本仓库就提交了这份文件**——2 条 deepsec 误报各带理由/时间/来源 run，豁免留痕可评审）。
  豁免带行号无关签名（source+rule+file+message）：全量格式化/大改后行号漂移不再失配；
  存量条目用 `cq suppress --reapply --run <新run>` 按签名对齐刷新。
  命中后 status=suppressed：不计入 gate/diff 判定，报告单列"已豁免"节。
- 基线文件在 `audits/baselines/<名字>.json`，自包含 findings，源 run 清理后仍可用。
- **fix**：分批修复计划，默认 dry-run（只出计划落 `fix-plan.json`，不改代码）。
  `--apply` 是用户显式批准的载体：必须配 `--batch N`（一次一批），且只对"自动修复"批次开放
  （ruff check --fix + ruff format）；安全/复杂度/测试类批次给人工指引，apply 会明确拒绝。
  apply 后不自动 commit，打印"跑测试 → 复扫 → diff → gate"闭环提示。

## Adapter 一览

| adapter | 工具 | 治什么 | 启用条件 |
|---|---|---|---|
| ruff-check | ruff check | lint、未使用导入、try-except-pass、blind except | 工具可用 |
| ruff-format | ruff format --check | 格式一致性 | 工具可用 |
| mypy | mypy | 类型错误 | 工具可用；有项目配置则完全尊重之 |
| radon | radon cc/mi | 圈复杂度（默认 D+）、可维护性指数（默认 C+） | 工具可用 |
| vulture | vulture | 死代码/未使用符号（默认 60% 置信度） | 工具可用 |
| pytest | pytest+pytest-cov | 失败测试、收集错误、覆盖率（默认阈值 80%） | 仓库有测试 |
| import-linter | lint-imports | 架构契约违约 | 仓库有 import-linter 配置 |
| deepsec | deepsec shield L1+L2 | 硬编码密钥、危险写法、注入（离线安全扫描） | pip 安装 或 `--deepsec-home` 源码仓库 |

## 产物

```
audits/<run_id>/
  summary.json    # 工具状态（版本/耗时/status/reason）+ totals + git/环境信息 + 配置快照
  findings.json   # 全部 findings（统一 schema，见下）
  report.md       # 人读报告：工具状态表、分级统计、明细、下一步建议
  raw/            # 各工具原始输出（已脱敏）
```

finding schema（与交接方案 §6 一致）：

```json
{
  "id": "sha1(source|rule|file|line|message)",
  "source": "deepsec|ruff|ruff-format|mypy|radon|vulture|pytest|import-linter",
  "category": "security|reliability|maintainability|test|architecture",
  "severity": "blocker|high|medium|low|info",
  "file": "src/foo.py", "line": 42,
  "message": "…", "rule": "…", "evidence": "…(脱敏)", "fix_hint": "…",
  "auto_fixable": false, "status": "new", "confidence": "high",
  "raw_ref": "raw/<tool>.json"
}
```

## 配置

默认值 < 仓库根 `cq.toml`（示例见 `config.example.toml`） < 环境变量（`CQ_PYTHON` / `CQ_DEEPSEC_HOME` / `CQ_DEEPSEC_PYTHON` / `CQ_TIMEOUT`） < 命令行。

## 测试与验收

- `python -m pytest tests`：29 个单测（schema/脱敏/目标解析/各 adapter 纯解析），不依赖外部工具。
- `tests/fixtures/demo-flask-api/`：故意埋了安全/类型/复杂度/死代码/格式/测试问题的 fixture 仓库，
  用于验收"六类问题一次全报出"。

## spec-drift（v0.2.0 · 可选）：spec 驱动项目的防漂移门

面向采用 spec 工作流（constitution → 现状规格 → 变更提案 → 实施）的项目，把"人记得维护 spec"
升级为"自动强制"。**默认关闭**：项目根放 `.specdrift.toml` 才启用，未配置的仓库 `scan/gate`
行为与旧版逐项一致（CI 有专项回归锁死）。

```toml
# .specdrift.toml（项目根）
code_paths   = ["app/", "prompts/"]   # 受 spec 管辖的代码路径
spec_root    = "openspec/specs"        # 现状规格根
change_root  = "openspec/changes"      # 变更提案根（proposal.md 含 spec_ref）
waiver_dir   = "decisions"             # 豁免归档目录
soft_timestamp_check = true            # 时间戳只做软提示
```

```bash
cq spec-gate --mode=hard              # 检查本次变更是否在活跃 proposal 中声明（hard=漂移则 exit 1）
cq waiver --reason "先写代码，spec 周五前补齐" --ttl 48h   # 有期限豁免：decisions/ 生成文件 + 计入债务
```

- 判定：变更粒度 delta 校验（活跃 proposal 的 `spec_ref` 指向存在的 spec 且含
  `ADDED/MODIFIED/REMOVED`，文本按词边界引用变更文件或显式目录形态 `app/`）；时间戳仅软提示。
- waiver 过期后 `spec-gate --mode=hard` 视为漂移并标红；账本复用 `.cq/suppressions.json`
  （条目带 `kind`，finding 与 waiver 互不干扰）。
- **v0.3.0 · 二期** ✅ `cq spec-rebuild`：AST 提取 `code_paths` 模块/公开符号 → spec 全文词边界匹配
  + spec 章节标题反向匹配，产出带置信度的**线索清单**（`code_missing` / `spec_missing` /
  覆盖率，`--json` 可机读）；`--draft` 只读（默认），`--apply` 落骨架模板（带 TODO 章节，
  不替人写内容）并清掉过期 spec 债。`cq promote`：decisions/ + 账本 waiver 按 slug/关键词
  聚类（重叠系数），`promote_window`/`promote_threshold` 可配（默认 30d/3），只输出候选与
  constitution 草稿，**永不写文件**。

## 路线图

- ~~**Phase 1**~~ ✅ `cq scan` + `cq doctor`：8 adapter 统一报告、环境体检、安装引导。
- ~~**Phase 2**~~ ✅ `cq triage` / `baseline` / `diff` / `gate` / `suppress`：基线对比、
  新增 blocker/high 即失败、local_rating 近似评级、带理由豁免机制。
- ~~**Phase 3**~~ ✅ `cq fix`（分批修复计划 + 显式 --apply）；DSH `SKILL.md`（已安装
  `~/.dsh/skills/ai-code-quality/`）+ Codex/Claude/Cursor 薄适配文件（见 `agent-adapters/`）。
- ~~**v0.2.0 · spec-drift 一期**~~ ✅ `cq spec-gate` + `cq waiver`（外部需求单驱动，含兼容性红线回归）。
- **Phase 4**：SonarCloud/SonarQube 接入、自研确定性 AI slop 规则、可选 AI review、
  可选 toolkit 插件/MCP。
