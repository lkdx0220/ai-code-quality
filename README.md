# ai-code-quality（`cq`）

通用、本地、只读的代码质量扫描 CLI：把 AI 写的代码按可复现的流程往质量门 A 级带。

一句话：`cq scan --project <任意Python仓库>` → `audits/<run_id>/` 下的统一 findings 报告。

## 设计原则

- **核心 CLI + 薄适配层**：`cq` 不依赖任何 Agent 框架（DSH/Codex/ZCode 各自只需一份说明文件，见 `agent-adapters/`）。
- **零运行时依赖**：核心包只用 Python 标准库；外部工具全部 subprocess 调用。
- **不许静默 pass**：工具缺失/超时/解析失败 → `skipped` / `timeout` / `error` + 原因，绝不当作通过。
- **只读**：不改代码、不 commit；所有工具缓存重定向到 `audits/`，目标仓库 `git status` 不变（只多 `audits/`）。
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
| DeepSec（本地工具，v0.2.0） | 安全扫描 L1/L2（硬编码密钥、危险写法、注入） | MIT | 本地源码仓库，经 CLI 调用 |

cq 自身代码为原创，以 MIT 许可证发布（见 `LICENSE`）。
如果你在报告中引用 cq 的扫描结果，建议同时注明底层工具，例如
"扫描结果由 ruff / mypy / radon / vulture / pytest / import-linter / DeepSec 产出，cq 聚合"。

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
```

退出码：`0` 完成；`1` 参数错误；`2` 有工具 error/timeout（结果不完整但不丢弃）。

`triage / fix / gate / baseline / diff` 属 Phase 2+，已在 CLI 占位、未实现。

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

## 路线图

- **Phase 2**：DeepSec 结果并入 triage；`baseline` / `diff`（fixed/new/existing）/ `gate`（blocker/high 新增即失败，本地 local_rating 近似 Sonar）。
- **Phase 3**：DSH `SKILL.md` 与各 Agent 适配文件；可选 toolkit 插件。
- **Phase 4**：SonarCloud/SonarQube 接入、自研确定性 AI slop 规则、可选 AI review。
