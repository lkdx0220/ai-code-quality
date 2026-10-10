# agent-adapters：薄适配层

核心思路（交接方案 §14）：`cq` CLI 是唯一执行入口，不依赖任何 Agent 框架；
每个 Agent 只需要一份很薄的"何时调 cq、怎么读报告"说明文件。

| Agent | 适配文件 | 状态 |
|---|---|---|
| 任何 Agent（ZCode / Codex / 通用） | `generic/PROMPT.md` | ✅ Phase 1 交付 |
| DSH | `dsh/SKILL.md`（安装到 `~/.dsh/skills/ai-code-quality/`） | ✅ Phase 3 |
| Codex | `codex/AGENTS.md.snippet` | ✅ Phase 3 |
| Claude Code | `claude/CLAUDE.md.snippet` | ✅ Phase 3 |
| Cursor | `cursor/ai-code-quality.mdc` | ✅ Phase 3 |
| MCP 宿主 | `cq mcp` 子命令 | ⏳ 可选，未做 |

新增一个 Agent = 新增一个 markdown 文件，cq 本体零改动。
适配文件只允许写"何时调、怎么调、如何汇报"，禁止复制 cq 的实现细节，避免多份说明各自漂移。
各文件的唯一详细版是 `generic/PROMPT.md`，其余文件只做触发与流程摘要并指向它。
