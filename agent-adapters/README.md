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

## 多 harness 环境的部署（SKILL.md 的安装点）

如果你的机器上有多个 Agent harness（多个 DSH home、Claude Code、Codex CLI），它们的 skills
目录相互独立，SKILL.md 需要同步到每一个。以四个常见位置为例：

| harness | 路径 |
|---|---|
| ZCode | `~/.zcode/skills/ai-code-quality/SKILL.md` |
| DSH（各 home） | `<每个 home>/skills/ai-code-quality/SKILL.md` |
| Claude Code | `~/.claude/skills/ai-code-quality/SKILL.md` |
| Codex CLI | `~/.codex/skills/ai-code-quality/SKILL.md` |
| Trae（CN） | `~/.trae-cn/skills/ai-code-quality/SKILL.md` |
| WorkBuddy | `~/.workbuddy/skills/ai-code-quality/SKILL.md` |
| Kimi 桌面版 | ❌ 无本机 skill/rules 机制（Electron 聊天应用），不适用；要在 Kimi 里用只能手动粘贴 `generic/PROMPT.md` 内容作提示词 |

同步命令（以仓库版为准）：

```bash
SRC="agent-adapters/dsh/SKILL.md"
for home in ~/.zcode ~/.dsh ~/.dsh-official ~/.claude ~/.codex ~/.trae-cn ~/.workbuddy; do
  mkdir -p "$home/skills/ai-code-quality" && cp "$SRC" "$home/skills/ai-code-quality/SKILL.md"
done
```

注意：harness 一般在**会话启动时**扫描 skills，装完/更新后要新开会话才会生效。
