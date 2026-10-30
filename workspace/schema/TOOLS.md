# LocalWiki 工具约定

本文件描述智能体可用工具的本地约定。具体 ToolSpec 以运行时注册表为准。

## 原则
- 正式写入 `wiki/` 必须经 CommitService，不得直接改文件。
- 外部写入与联网默认关闭，需用户授权。
- Skill / MCP / Composio 调用遵守权限清单与审批模式。
