"""
MCP 工具函数测试（app/mcp/tools.py）

直接测试工具函数（不经 MCP server），用 mock/patch 隔离 config 写入。
覆盖 BDD 场景：
1. get_logs：参数透传、时间过滤、文件缺失返回空
2. get_current_config：脱敏生效
3. update_config：正常写入、非法段名拒绝、auth 段拒绝
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# 与项目日志格式一致的时间戳行
# ---------------------------------------------------------------------------

SAMPLE_LOG = (
    "[2026/09/03 10:00:00.000] [INFO] [run:sync_1_100] 开始同步\n"
    "[2026/09/03 12:00:00.000] [ERROR] [run:sync_1_100] 数据库连接失败\n"
    "[2026/09/03 14:00:00.000] [WARNING] [run:sync_1_100] 重试第 1 次\n"
    "[2026/09/03 16:00:00.000] [INFO] [run:sync_1_100] 同步结束: status=success\n"
)


# ===========================================================================
# get_logs 测试
# ===========================================================================


class TestGetLogs:
    """get_logs 工具：参数透传、时间过滤、文件缺失返回空。"""

    @staticmethod
    def _make_read_token():
        """创建含 read scope 的 mock token。"""
        mock_token = MagicMock()
        mock_token.scopes = ["read"]
        return mock_token

    @pytest.mark.asyncio
    async def test_get_logs_无参数_返回成功包络(self):
        """无参数调用 → 返回 success 包络，content 存在。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools.os.stat") as mock_stat:
                        mock_stat.return_value = MagicMock(
                            st_size=len(SAMPLE_LOG), st_mtime=1234567890.0
                        )
                        with patch("builtins.open", mock_open(read_data=SAMPLE_LOG)):
                            result = await tools.get_logs()

        assert result["status"] == "success"
        assert "content" in result["data"]
        assert "stats" in result["data"]
        assert result["data"]["stats"]["size"] == len(SAMPLE_LOG)
        assert result["data"]["stats"]["lines"] == 4
        assert result["data"]["stats"]["errors"] == 1
        assert result["data"]["stats"]["modified"] == 1234567890000.0

    @pytest.mark.asyncio
    async def test_get_logs_level过滤_仅返回指定级别(self):
        """level=ERROR → 仅返回 ERROR 行。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools.os.stat") as mock_stat:
                        mock_stat.return_value = MagicMock(
                            st_size=len(SAMPLE_LOG), st_mtime=1234567890.0
                        )
                        with patch("builtins.open", mock_open(read_data=SAMPLE_LOG)):
                            result = await tools.get_logs(level="ERROR")

        content = result["data"]["content"]
        assert "ERROR" in content
        assert "INFO" not in content
        assert "WARNING" not in content

    @pytest.mark.asyncio
    async def test_get_logs_时间范围过滤(self):
        """since/until → 仅返回时间窗口内的日志行。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools.os.stat") as mock_stat:
                        mock_stat.return_value = MagicMock(
                            st_size=len(SAMPLE_LOG), st_mtime=1234567890.0
                        )
                        with patch("builtins.open", mock_open(read_data=SAMPLE_LOG)):
                            result = await tools.get_logs(
                                since="2026-09-03T11:00:00",
                                until="2026-09-03T13:00:00",
                            )

        content = result["data"]["content"]
        assert "数据库连接失败" in content
        assert "开始同步" not in content
        assert "重试第 1 次" not in content
        assert "同步结束" not in content

    @pytest.mark.asyncio
    async def test_get_logs_文件未配置_返回空(self):
        """日志文件未配置（resolved_dev_log_file_path 返回 None）→ 空 content。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=None,
            ):
                result = await tools.get_logs()

        assert result["status"] == "success"
        assert result["data"]["content"] == ""
        assert result["data"]["stats"]["size"] == 0

    @pytest.mark.asyncio
    async def test_get_logs_文件不存在_返回空(self):
        """日志文件不存在 → 空 content 而非异常。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/nonexistent/log.txt"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=False):
                    result = await tools.get_logs()

        assert result["status"] == "success"
        assert result["data"]["content"] == ""
        assert result["data"]["stats"]["size"] == 0

    @pytest.mark.asyncio
    async def test_get_logs_search关键词过滤(self):
        """search=数据库 → 仅返回包含该关键词的行。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools.os.stat") as mock_stat:
                        mock_stat.return_value = MagicMock(
                            st_size=len(SAMPLE_LOG), st_mtime=1234567890.0
                        )
                        with patch("builtins.open", mock_open(read_data=SAMPLE_LOG)):
                            result = await tools.get_logs(search="数据库")

        content = result["data"]["content"]
        assert "数据库连接失败" in content
        assert "开始同步" not in content

    @pytest.mark.asyncio
    async def test_get_logs_异常_不泄露路径(self):
        """日志读取异常时，错误信息不应包含绝对路径。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/secret/path/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch(
                        "app.mcp.tools._dispatch_read_log",
                        new=AsyncMock(
                            side_effect=Exception(
                                "/secret/path/app.log: permission denied"
                            )
                        ),
                    ):
                        with pytest.raises(ToolError) as exc_info:
                            await tools.get_logs()

        msg = str(exc_info.value)
        assert "/secret/path" not in msg
        assert "permission denied" not in msg
        assert "获取日志失败" in msg

    @pytest.mark.asyncio
    async def test_get_logs_无token_拒绝(self):
        """无 access token 时 get_logs 应被拒绝（ToolError）。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        with patch("app.mcp.tools.get_access_token", return_value=None):
            with pytest.raises(ToolError) as exc_info:
                await tools.get_logs()

        assert "未找到访问令牌" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_logs_无read_scope_拒绝(self):
        """无 read scope 时 get_logs 应被拒绝（ToolError）。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_token = MagicMock()
        mock_token.scopes = ["write"]

        with patch("app.mcp.tools.get_access_token", return_value=mock_token):
            with pytest.raises(ToolError) as exc_info:
                await tools.get_logs()

        assert "需要 read scope" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_logs_非法level_抛出ToolError(self):
        """level=TRACE 不在白名单 → ToolError。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with pytest.raises(ToolError) as exc_info:
                await tools.get_logs(level="TRACE")

        assert "无效的日志级别" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_logs_WARN别名_归一化为WARNING(self):
        """level=WARN 归一化为 WARNING 后再过滤。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools.os.stat") as mock_stat:
                        mock_stat.return_value = MagicMock(
                            st_size=len(SAMPLE_LOG), st_mtime=1234567890.0
                        )
                        with patch("builtins.open", mock_open(read_data=SAMPLE_LOG)):
                            result = await tools.get_logs(level="WARN")

        content = result["data"]["content"]
        assert "WARNING" in content
        assert "INFO" not in content

    @pytest.mark.asyncio
    async def test_get_logs_默认limit透传(self):
        """默认 limit=50 应以字符串 "50" 透传给 _dispatch_read_log。"""
        from app.mcp import tools

        mock_dispatch = AsyncMock(
            return_value={
                "content": "",
                "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
            }
        )

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools._dispatch_read_log", new=mock_dispatch):
                        await tools.get_logs()

        assert mock_dispatch.call_args.args[3] == "50"

    @pytest.mark.asyncio
    async def test_get_logs_limit超上限_钳制为10000(self):
        """limit=99999 超过上限 → 透传 "10000"。"""
        from app.mcp import tools

        mock_dispatch = AsyncMock(
            return_value={
                "content": "",
                "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
            }
        )

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools._dispatch_read_log", new=mock_dispatch):
                        await tools.get_logs(limit=99999)

        assert mock_dispatch.call_args.args[3] == "10000"

    @pytest.mark.asyncio
    async def test_get_logs_limit下限_钳制为1(self):
        """limit=0 低于下限 → 透传 "1"。"""
        from app.mcp import tools

        mock_dispatch = AsyncMock(
            return_value={
                "content": "",
                "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
            }
        )

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools._dispatch_read_log", new=mock_dispatch):
                        await tools.get_logs(limit=0)

        assert mock_dispatch.call_args.args[3] == "1"

    @pytest.mark.asyncio
    async def test_get_logs_非法since格式_抛出ToolError(self):
        """since 无法解析为 ISO 时间 → ToolError。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with pytest.raises(ToolError) as exc_info:
                await tools.get_logs(since="not-a-date")

        assert "无效的 since 时间格式" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_logs_非法until格式_抛出ToolError(self):
        """until 无法解析为 ISO 时间 → ToolError。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with pytest.raises(ToolError) as exc_info:
                await tools.get_logs(until="not-a-date")

        assert "无效的 until 时间格式" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_logs_since晚于until_抛出ToolError(self):
        """since 晚于 until → ToolError。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with pytest.raises(ToolError) as exc_info:
                await tools.get_logs(
                    since="2026-09-03T13:00:00",
                    until="2026-09-03T11:00:00",
                )

        assert "since 时间必须早于或等于 until 时间" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_logs_时间边界_包含端点行(self):
        """since/until 端点对应的日志行应被包含（闭区间）。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools.os.stat") as mock_stat:
                        mock_stat.return_value = MagicMock(
                            st_size=len(SAMPLE_LOG), st_mtime=1234567890.0
                        )
                        with patch("builtins.open", mock_open(read_data=SAMPLE_LOG)):
                            result = await tools.get_logs(
                                since="2026-09-03T10:00:00",
                                until="2026-09-03T12:00:00",
                            )

        content = result["data"]["content"]
        assert "开始同步" in content
        assert "数据库连接失败" in content
        assert "重试第 1 次" not in content


# ===========================================================================
# get_current_config 测试
# ===========================================================================


class TestGetCurrentConfig:
    """get_current_config 工具：脱敏生效。"""

    @staticmethod
    def _make_read_token():
        """创建含 read scope 的 mock token。"""
        mock_token = MagicMock()
        mock_token.scopes = ["read"]
        return mock_token

    @pytest.mark.asyncio
    async def test_get_current_config_返回成功包络(self):
        """无参数调用 → 返回 success 包络。"""
        from app.mcp import tools

        mock_cm = MagicMock()
        mock_cm.get_all_config.return_value = {
            "sync": {"match_confidence_threshold": 0.6},
        }

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch("app.mcp.tools.config_manager", mock_cm):
                result = await tools.get_current_config()

        assert result["status"] == "success"
        assert "data" in result

    @pytest.mark.asyncio
    async def test_get_current_config_敏感字段已脱敏(self):
        """auth.password/secret_key、auth.webhook_key、llm.api_key 应被掩码，
        非敏感字段明文保留，且返回体中不含原始哈希/密钥原文。"""
        import json

        from app.mcp import tools

        plain_password_hash = "pbkdf2:sha256:260000$salt$deadbeefhash"
        plain_secret_key = "super-secret-master-key-please-hide-me"

        mock_cm = MagicMock()
        mock_cm.get_all_config.return_value = {
            "auth": {
                "username": "admin",
                "password": plain_password_hash,
                "secret_key": plain_secret_key,
                "webhook_key": "plain-webhook-key-123",
                "session_timeout": 3600,
            },
            "llm": {
                "api_key": "sk-plain-key",
                "provider": "openai_compat",
            },
            "sync": {
                "match_confidence_threshold": 0.6,
            },
        }

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch("app.mcp.tools.config_manager", mock_cm):
                result = await tools.get_current_config()

        data = result["data"]
        # 敏感字段 → 掩码
        assert data["auth"]["password"] == "***"
        assert data["auth"]["secret_key"] == "***"
        assert data["auth"]["webhook_key"] == "***"
        assert data["llm"]["api_key"] == "***"
        # 非敏感字段 → 明文
        assert data["auth"]["username"] == "admin"
        assert data["auth"]["session_timeout"] == 3600
        assert data["sync"]["match_confidence_threshold"] == 0.6
        # 返回体序列化后不得含原文
        serialized = json.dumps(result, ensure_ascii=False)
        assert plain_password_hash not in serialized
        assert plain_secret_key not in serialized
        assert "plain-webhook-key-123" not in serialized

    @pytest.mark.asyncio
    async def test_get_current_config_调用_config_manager(self):
        """应委托 config_manager.get_all_config()。"""
        from app.mcp import tools

        mock_cm = MagicMock()
        mock_cm.get_all_config.return_value = {}

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch("app.mcp.tools.config_manager", mock_cm):
                await tools.get_current_config()

        mock_cm.get_all_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_current_config_无token_拒绝(self):
        """无 access token 时 get_current_config 应被拒绝（ToolError）。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        with patch("app.mcp.tools.get_access_token", return_value=None):
            with pytest.raises(ToolError) as exc_info:
                await tools.get_current_config()

        assert "未找到访问令牌" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_current_config_无read_scope_拒绝(self):
        """无 read scope 时 get_current_config 应被拒绝（ToolError）。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_token = MagicMock()
        mock_token.scopes = ["write"]

        with patch("app.mcp.tools.get_access_token", return_value=mock_token):
            with pytest.raises(ToolError) as exc_info:
                await tools.get_current_config()

        assert "需要 read scope" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_current_config_多实例与账号段_敏感字段已脱敏(self):
        """真实 get_all_config 输出为下划线段名，敏感字段仍须脱敏。

        回归 E2E 泄露：mock 使用连字符段名时假绿，实际 get_all_config 返回
        bangumi_oauth / notify_email_1 等下划线形态，is_sensitive_field 漏判。
        """
        from app.mcp import tools

        mock_cm = MagicMock()
        mock_cm.get_all_config.return_value = {
            "bangumi_oauth": {
                "client_id": "app-client-id",
                "client_secret": "app-client-secret",
            },
            "notify_email_1": {
                "smtp_host": "smtp.example.com",
                "smtp_password": "p@ss",
                "url": "https://example.com",
            },
            "notify_wecom_1": {
                "key": "wecom-key",
                "url": "https://qyapi.weixin.qq.com/hook",
            },
            "notify_dingtalk_1": {
                "access_token": "dingtok",
                "secret": "dingsecret",
                "url": "https://oapi.dingtalk.com/hook",
            },
        }

        with patch(
            "app.mcp.tools.get_access_token",
            return_value=self._make_read_token(),
        ):
            with patch("app.mcp.tools.config_manager", mock_cm):
                result = await tools.get_current_config()

        data = result["data"]
        # 敏感字段 → 掩码
        assert data["bangumi_oauth"]["client_secret"] == "***"
        assert data["notify_email_1"]["smtp_password"] == "***"
        assert data["notify_wecom_1"]["key"] == "***"
        assert data["notify_dingtalk_1"]["access_token"] == "***"
        assert data["notify_dingtalk_1"]["secret"] == "***"
        # 非敏感字段 → 明文
        assert data["bangumi_oauth"]["client_id"] == "app-client-id"
        assert data["notify_email_1"]["smtp_host"] == "smtp.example.com"
        assert data["notify_email_1"]["url"] == "https://example.com"
        assert data["notify_wecom_1"]["url"] == "https://qyapi.weixin.qq.com/hook"
        assert data["notify_dingtalk_1"]["url"] == "https://oapi.dingtalk.com/hook"


# ===========================================================================
# update_config 测试
# ===========================================================================


class TestUpdateConfig:
    """update_config 工具：正常写入、非法段名拒绝、auth 段拒绝。"""

    @staticmethod
    def _make_write_token():
        """创建含 write scope 的 mock token。"""
        mock_token = MagicMock()
        mock_token.scopes = ["read", "write"]
        return mock_token

    @pytest.mark.asyncio
    async def test_update_config_合法段_返回成功(self):
        """合法段修改 → 返回成功。"""
        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section="sync",
                    key="match_confidence_threshold",
                    value=0.7,
                )

        assert result["status"] == "success"
        mock_cm.set_config.assert_called_with("sync", "match_confidence_threshold", 0.7)

    @pytest.mark.asyncio
    async def test_update_config_下划线段名_自动转连字符(self):
        """下划线段名（如 bangumi_data）应归一化为连字符。"""
        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section="bangumi_data",
                    key="cache_ttl_days",
                    value=14,
                )

        assert result["status"] == "success"
        mock_cm.set_config.assert_called_with("bangumi-data", "cache_ttl_days", 14)

    @pytest.mark.asyncio
    async def test_update_config_非法段名_抛出ToolError(self):
        """不存在的段名 → ToolError。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(
                        section="nonexistent_section",
                        key="key",
                        value="value",
                    )

        assert "nonexistent_section" in str(exc_info.value)
        assert "未知配置段" in str(exc_info.value)
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_config_auth段_拒绝(self):
        """auth 段禁止通过 MCP 修改 → ToolError。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(
                        section="auth",
                        key="enabled",
                        value=False,
                    )

        assert "auth" in str(exc_info.value).lower()
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_config_auth变体段名_拒绝(self):
        """auth 的大小写/空白/下划线变体均应被拒绝，且不调用 set_config。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        for section in ("Auth", " auth ", "auth_"):
            mock_cm = MagicMock()

            with patch("app.mcp.tools.config_manager", mock_cm):
                with patch(
                    "app.mcp.tools.get_access_token",
                    return_value=self._make_write_token(),
                ):
                    with pytest.raises(ToolError):
                        await tools.update_config(
                            section=section,
                            key="enabled",
                            value=False,
                        )

            mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_config_多实例段_正常写入(self):
        """多实例段（如 notify-webhook-1）应正常写入。"""
        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section="notify-webhook-1",
                    key="url",
                    value="https://example.com/hook",
                )

        assert result["status"] == "success"
        mock_cm.set_config.assert_called_with(
            "notify-webhook-1", "url", "https://example.com/hook"
        )

    @pytest.mark.asyncio
    async def test_update_config_read_scope_token_拒绝写入(self):
        """read scope token 调 update_config 应被拒绝（ToolError）。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        # 模拟 get_access_token 返回仅含 read scope 的 token
        mock_token = MagicMock()
        mock_token.scopes = ["read"]

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch("app.mcp.tools.get_access_token", return_value=mock_token):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(
                        section="sync",
                        key="match_confidence_threshold",
                        value=0.7,
                    )

        assert "需要 write scope" in str(exc_info.value)
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_config_no_token_拒绝写入(self):
        """无 access token（None）时 update_config 应被拒绝。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch("app.mcp.tools.get_access_token", return_value=None):
                with pytest.raises(ToolError):
                    await tools.update_config(
                        section="sync",
                        key="match_confidence_threshold",
                        value=0.7,
                    )

        mock_cm.set_config.assert_not_called()


# ---------------------------------------------------------------------------
# update_config key/value 合法性校验（P1-4）
# ---------------------------------------------------------------------------


class TestUpdateConfigKeyValueValidation:
    """验证 update_config 的 key/value 合法性校验。"""

    def _make_write_token(self):
        """创建含 write scope 的 mock token。"""
        mock_token = MagicMock()
        mock_token.scopes = ["read", "write"]
        return mock_token

    @pytest.mark.asyncio
    async def test_update_config_非法key_拒绝(self):
        """不在 schema 中的 key 应被拒绝。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(
                        section="sync",
                        key="nonexistent_key_xyz",
                        value="anything",
                    )

        assert "非法配置键" in str(exc_info.value)
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_config_超长value_拒绝(self):
        """超过长度上限的 value 应被拒绝。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(
                        section="sync",
                        key="match_confidence_threshold",
                        value="x" * 10001,
                    )

        assert "长度" in str(exc_info.value) or "long" in str(exc_info.value).lower()
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_config_value恰好上限_通过(self):
        """value 长度恰好等于上限（10000）时应正常写入。"""
        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section="sync",
                    key="match_confidence_threshold",
                    value="x" * 10000,
                )

        assert result["status"] == "success"
        mock_cm.set_config.assert_called_once_with(
            "sync", "match_confidence_threshold", "x" * 10000
        )
