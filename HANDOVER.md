# 交接文件：ai-code-quality（cq）项目 · 待续工作

> 交接对象：接手的 Agent。撰写者：ZCode（本项目的初始开发者，额度耗尽前的最后一份产出）。
> 日期：2026-10-16。本文按"你接手后 10 分钟内搞清楚该干什么"组织。
> **先读本文，再动手。遇到不确定的决策问用户（lkdx0220），不要自己拍板——这是本项目的一贯规矩。**

---

## 0. 一句话现状

cq（本地代码质量 CLI + AI Agent Skill，GitHub 公开仓库）已发布 v0.5.2，一切可用；
**当前正在进行的工作是把 cq 包成 DSH 生态的插件**（`dsh-ai-code-quality`），
实验场在独立目录，已经过三轮外部审查 + 一轮真宿主复验 + 两轮缺陷修复，
**卡在最后一步：等待用户重启官方 DSH 客户端做最终验证**。

## 1. 关键路径速查（全部绝对路径）

| 是什么 | 路径 |
|---|---|
| **主仓库**（cq 本体，GitHub 同名公开仓库的本地克隆） | `C:\Users\24701\Desktop\skills开发\ai-code-quality\` |
| **实验场**（主仓库副本 + 插件，本体绝不动的约定见 §3） | `C:\Users\24701\Desktop\skills开发\dsh-ai-code-quality-lab\` |
| **插件源码**（实验场内，有独立 git） | `...\dsh-ai-code-quality-lab\dsh-plugin\`（index.js / lib/cq.js / test/smoke.mjs / package.json / cordis.patch.yml / README.md） |
| 用户实际使用的 DSH home（官方客户端） | `C:\Users\24701\.dsh-official\`（启动脚本 `launch-official-dsh.cmd`） |
| 另一个 DSH home（community 桌面端，**不是**主用的） | `C:\Users\24701\.dsh\` |
| DeepSec 源码仓库（cq 的安全扫描依赖，只读引用） | `C:\Users\24701\Desktop\AI开发模板\DeepSec\` |
| 外部审查/缺陷报告（DEEPSEEK 方产出，质量很高，值得信） | `C:\Users\24701\Desktop\_sandbox_probe\dsh-ai-code-quality-plugin-defect-report.md` |

远程：`https://github.com/lkdx0220/ai-code-quality-skill`（gh 已登录该账号；main 分支，CI 双平台八作业）。
插件 git 目前**只在本地**，未推远端（归宿未定，见 §5）。

## 2. 项目是什么（30 秒版）

cq = 确定性代码质量流水线：`cq scan/doctor/triage/baseline/diff/gate/fix/suppress/spec-gate/waiver/spec-rebuild/promote`，
聚合 ruff/mypy/radon/vulture/pytest-cov/import-linter/DeepSec 八个外部工具，
统一 finding schema + 基线对比 + 质量门 + 豁免账本。双形态分发：

1. **AI Agent Skill**（SKILL.md 装进 8 个 harness：zcode/.dsh/.dsh-official/.claude/.codex/.trae-cn/.workbuddy/kimi-desktop，同步命令在 `agent-adapters/README.md`）
2. **DSH 插件**（进行中）：把 cq 的 12 个只读工具面暴露为 `cq_*` 类型化工具。设计红线：**`fix --apply` 和 `spec-rebuild --apply` 刻意不在插件工具面**——改代码必须走 CLI 显式批准。

完整背景在主仓库 `README.md`（叙事层很全）和 `agent-adapters/generic/PROMPT.md`。

## 3. 铁律（违反会破坏用户的信任结构）

1. **主仓库在实验场验证通过前不接插件**——用户原话"不动我们的 skills 本体，你先复制一份，在新的那份里改"。例外：**cq 本体自身的 bug 可以直接修主仓库并推送**（先例：spec-rebuild --json 分支顺序、radon 去重）。
2. **装 DSH 插件必须装 `.dsh-official`**（用户主用的官方客户端 home）。曾误装 `.dsh` 导致无效——两个客户端内核不同（0.1.5-rc.2 vs 0.2.0-rc.2）、home 隔离，混装会让官方端 boot 失败（launcher 注释原话）。`.dsh` 侧的误装已完整回滚。
3. 改 profile 前先 `.bak` 备份（跟随既有命名惯例，如 `package.json.bak-before-cq-20261014`）。
4. 插件**改完必须重启宿主才能验证**（不走注入器热重载，缺陷报告 §6.1 已证）。
5. cq 本体的所有"缺配置=旧行为"红线由 CI 的 `specdrift-compat` 作业常驻锁死，别破坏。
6. 发布走 minor tag（现有 v0.1.0~v0.5.2）；版本号改 `pyproject.toml` + `src/ai_code_quality/__init__.py` 两处，有 `test_version_guard.py` 守卫。

## 4. 插件安装状态（当前卡点所在）

已装进 `C:\Users\24701\.dsh-official\profiles\desktop\` 的四件套 + junction：

- `package.json`：依赖 `dsh-ai-code-quality: link:...\dsh-plugin`，bundles 数组已含
- `cordis.patch.yml`：条目 `- id: dsh-ai-code-quality` + `config.command: "cq"`
- `node_modules/dsh-ai-code-quality`：junction → 实验场插件目录
- 备份：`package.json.bak-before-cq-20261014` / `cordis.patch.yml.bak-before-cq-20261014`

**回滚**（如出问题）：恢复两个 .bak + 删 junction（PowerShell `[System.IO.Directory]::Delete(path, $false)` 只删链接）。

### 当前卡点：等用户重启官方客户端

复验报告（缺陷报告文件 §"复验"节，303 行起）确认：宿主 08:58 重启后 12/12 工具实测可用（含三个写状态工具在临时项目验证）。
但那之后我们为 N1/N2 又改了插件代码（见 §5 待办），**新版还没经过宿主验证**——又到了"需要用户重启"的节点。

用户已被告知需要重启验证。你接手后第一件事：**问用户是否已重启并测过 `cq_doctor`**；若测过且通过，进入 §5 的合流决策；若没测，提醒之。

## 5. 待办清单（按优先级）

1. **【等用户】宿主重启验证 N1/N2 修复版插件**（同 §4）。验证口径：会话里调 `cq_doctor`（应返回完整 JSON 而非报错）、`cq_scan`（返回应含 `tools` 数组与 `notes`）。
2. **【决策：问用户】插件合回流**：验证通过后把 `dsh-plugin/` 目录并入主仓库（建议 `agent-adapters/dsh-plugin/`）+ 主仓库 README 补插件章节 + 发版（插件 v0.2.0 / cq 可同轮 patch）。用户的原始约束"不动本体"以"实验场验证通过"为解除条件，但要用户点头。
3. **【债务】cq 本体 5 个 cc-D**（`run_spec_gate` cc21 / `run_scan` cc21 / `diff_findings` cc21 / `MypyAdapter.run` cc22 / `MypyAdapter` cc23，位置见最新自扫 findings.json）——spec-drift 快速迭代欠下的重构债。用户此前按批次修债的流程：拆函数 → 全测试 → 复扫 cc-D 归零 → 提交。
4. **【债务】覆盖率 64.2% < 80%**（gate 唯一红灯项）——缺 adapter 集成测试路径。
5. **【可选】插件 README/SKILL.md 的 vulture 报 `_jaccard` unused**（specrebuild.py:339，本体）——改用重叠系数后遗留，删或留看第 3 项一起做。
6. **【远期】Phase 4 路线图**：Sonar 接入 / AI slop 规则 / 多语言（Java 需求未立项）。见主仓库 README 路线图节。

## 6. 环境坑（踩过的，别再踩）

| 坑 | 现象 | 应对 |
|---|---|---|
| **本机安全软件拦 Python313 写盘**（已定位为 Nahimic 音频服务 DLL 注入，用户已禁用，若复发先查它） | python.exe 报 `No usable temporary directory` / `WinError 5` / 弹窗 `0xC0000142` | 用 cq 的 uv venv python（`C:\Users\24701\AppData\Roaming\uv\tools\ai-code-quality\Scripts\python.exe`）跑测试——它不被拦。临时目录指到仓库内 `.tmp/`：`TMP=".tmp" <venv-python> -m pytest tests -p no:cacheprovider --basetemp=.tmp/ptN` |
| **`uv tool install --force .` 清空 venv 手动装的包** | venv 里的 ruff/pytest 消失 | 每次重装后补：`uv pip install --python <venv-python> ruff pytest pytest-cov` |
| **裸 Node 加载插件必失败（设计如此）** | `@deepseek-ai/dsh-tools unavailable` | 该包只在宿主 asar overlay 里，磁盘上不存在（缺陷报告 §4.4 证实）。测试用 `npm test`（注入宿主同语义模拟 defineTool）；真验证必须进宿主 |
| Python313 直跑某些管道（radon -j）在沙箱下输出异常 | JSON 解析空 | 用 venv python，或文本模式 `radon cc -s` + grep |
| Git Bash 下 `reg`/`mklink` 引号问题 | "无效语法" | 用 PowerShell 等价命令 |
| heredoc 多层转义改代码易匹配失败 | python heredoc 里 `\\n` 等错乱 | 优先 Read+Edit 工具改文件，别用 bash heredoc 写代码 |

## 7. 验证命令速查

```bash
# cq 本体（在主仓库目录）
PYTHONPATH=src <venv-python> -m pytest tests -p no:cacheprovider --basetemp=.tmp/pt1 -q   # 全量测试（当前 142 个）
cq doctor                          # 环境体检（PATH 上 cq 0.5.2 可用）
cq scan --project . --no-deepsec   # 自扫（当前基线：8 findings，重复 id=0）

# 插件（在 dsh-plugin 目录）
npm test                           # 20 项冒烟（宿主模拟契约路径）
SMOKE_STRICT=1 npm test            # CI 用：主路径 SKIP 视为失败

# DSH 装配结果检查（无需进会话）
grep "dsh-ai-code-quality" "C:\Users\24701\.dsh-official\profiles\desktop\cordis.yml"  # 有输出=已装配
```

## 8. 协作方（都是用户侧的 Agent，产出可信但都要复核）

- **DEEPSEEK 方**：提需求单（spec-drift 一二期 → v0.2~v0.5）、Issue（GitHub #1 #2 均已修复关闭）、插件三轮审查 + 缺陷报告 + 复验报告。沟通渠道是用户转发 markdown 文件（多在 `C:\Users\24701\Desktop\_sandbox_probe\` 或 `Desktop\原神剧情\`）。
- 工作惯例：他们的报告按编号（D1-D5 / N1-N3）逐条对账修复，修完在回复里逐条给证据。这个节奏用户很认可，保持。

## 9. 历史脉络（需要细节时去哪找）

- 主仓库 `git log`（消息写得很详）+ README（叙事层/路线图/各能力章节）
- `agent-adapters/README.md`（8 端部署矩阵 + 插件四件套要点）
- GitHub Releases v0.1.0~v0.5.2（每版 notes 即该阶段总结）
- 缺陷报告文件（§0 结论表 + §"复验"节）= 插件质量问题的权威记录
- 本会话的完整决策链若丢失：以本文 + 上述文件为准，不要凭空脑补

## 10. 最后三条忠告

1. **这个项目的文化是"证据优先"**：每轮修改都要有可复现的验证（测试/复扫/宿主实测），回复用户时逐条给证据。数字必须来自实测输出，不拍脑袋（曾因估计测试数被外部纠正）。
2. **外部报告先核实再修**：DEEPSEEK 的报告迄今 100% 准确，但每次我都先复现根因再动手（radon 双收录就是复现后发现是 class/顶层双输出，不是别的原因）。
3. 用户决策点（合流、发布、装哪里）**必须问，不要自作主张**。用户技术判断力很强，答复通常很快。

祝顺利。这个项目本身状态很好——CI 全绿、测试 142 个、五轮发版、双重形态——你接手的是收尾，不是救火。
