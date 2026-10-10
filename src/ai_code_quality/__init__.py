"""ai-code-quality: 通用本地代码质量扫描 CLI（工作名 cq）。

分层设计（见交接方案 §14）：
- 核心层：本包，纯标准库，不依赖任何 Agent 框架。
- 工具层：ruff / mypy / radon / vulture / pytest / import-linter / deepsec，
  全部通过 subprocess 调用，缺失/超时/解析失败显式标记，绝不静默 pass。
- 适配层：见项目 agent-adapters/ 目录，只是"何时调 cq"的说明书。
"""

__version__ = "0.1.0"
