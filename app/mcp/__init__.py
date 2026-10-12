"""Bangumi-syncer 内置 MCP 服务（FastMCP 同仓嵌入实现）。

本包将 MCP 服务直接嵌入 BS 进程，不经 HTTP 调用业务层。当前包含：

- ``tools``：MCP 工具实现（get_logs / get_current_config / update_config），直接调用 BS 业务层。

公共入口通过子模块直接导入（如 ``from app.mcp.tools import get_logs``），本文件不做符号再导出。
"""
