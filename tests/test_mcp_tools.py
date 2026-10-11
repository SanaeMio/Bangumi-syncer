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
    async def test_update_config_格式非法的key_拒绝(self):
        """key 含等号等 INI 注入字符时应被拒绝。

        判据已由「key 是否存在」改为「key 格式是否合法」：``SectionMeta.fields``
        只登记需特殊渲染的项，不是完整键清单，用它做存在性校验会误拒
        dev.debug / llm.model 等合法配置。
        """
        from fastmcp.exceptions import ToolError

        from app.core.config import _validate_section_key
        from app.mcp import tools

        mock_cm = MagicMock()
        # 复现 set_config 的真实行为：格式非法时抛 ValueError
        mock_cm.set_config.side_effect = lambda s, k, v: _validate_section_key(s, k)

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(
                        section="sync",
                        key="bad=key",
                        value="anything",
                    )

        assert "非法配置键名" in str(exc_info.value)
        mock_cm.set_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_config_未登记但格式合法的key_放行(self):
        """未在 SectionMeta.fields 登记、但格式合法的 key 应放行（③类策略）。"""
        from app.mcp import tools

        mock_cm = MagicMock()

        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section="sync",
                    key="nonexistent_key_xyz",
                    value="anything",
                )

        assert result["status"] == "success"
        mock_cm.set_config.assert_called_once()

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


# ===========================================================================
# update_config 写入策略测试（评审 #259 修复）
#
# 判据（不再是「键敏不敏感」，而是）：
#   ① 改完之后 MCP 自己不可用         → 禁改
#   ② 改完之后用户还需回 Web 页面操作 → 禁改
#   ③ 其余一律开放（等同 Web 界面）
# ===========================================================================


# ① + ② 禁改清单：显式列举，不从实现导入，保持测试为独立规格
DENIED_WRITE_KEYS = [
    # ① 登录通道本身：改完用户无法自行恢复，只能留在 Web 界面操作
    ("auth", "enabled"),  # 置 false 会让全站免鉴权（见 app/api/deps.py）
    ("auth", "secret_key"),  # verify_password 以其做 HMAC，改动使已存密码哈希全部失效
    ("auth", "password"),  # 用户不知新密码即永久失去访问权
]

# ② 类：允许修改，但需在返回值中提示用户执行的后续操作（见 _MCP_WRITE_FOLLOWUP）
FOLLOWUP_REQUIRED_KEYS = [
    ("auth", "username"),
    ("auth", "max_login_attempts"),
    ("auth", "lockout_duration"),
    ("auth", "webhook_auth_enabled"),
    ("trakt", "client_secret"),
    ("bangumi-oauth", "client_secret"),
]

# 只读段：Web 界面无写入入口，写入会被系统当作数据（而非配置）搬走。
# 见 app/mcp/tools.py::_MCP_WRITE_READONLY_SECTIONS
READONLY_SECTIONS = ["bangumi"]

# ③ 开放清单：含此前被 fields 白名单误拒的键，以及用户判据下可自恢复的凭据
ALLOWED_WRITE_KEYS = [
    # 此前因 fields 未登记而被误拒的合法配置
    ("sync", "enable_real_action"),
    ("sync", "test_skip_permission_check"),
    ("dev", "debug"),
    ("dev", "script_proxy"),
    ("dev", "log_file"),
    ("dev", "bgm_api_proxy"),
    ("llm", "model"),
    ("llm", "api_base"),
    ("feiniu", "db_path"),
    ("fongmi", "devices"),
    ("fongmi", "subnet"),
    ("bangumi-archive", "http_proxy"),
    ("bangumi-archive", "retry_interval"),
    ("bangumi-data", "http_proxy"),
    # 此前因 auth 整段拉黑而被误伤的无害键（② 类可改，见 FOLLOWUP_REQUIRED_KEYS）
    ("auth", "username"),
    ("auth", "max_login_attempts"),
    ("auth", "lockout_duration"),
    ("auth", "webhook_auth_enabled"),
    ("auth", "webhook_key"),
    ("auth", "session_timeout"),
    ("auth", "https_only"),
    # 可自行恢复的凭据类（判据③）
    ("llm", "api_key"),
    ("notify-email-1", "smtp_password"),
    ("notify-wecom-1", "key"),
    ("notify-dingtalk-1", "access_token"),
    ("notify-dingtalk-1", "secret"),
    # 字段表为空的段：全开放
    ("trakt", "client_id"),
    ("trakt", "redirect_uri"),
    ("web", "base_path"),
    ("bangumi-mapping", "some_key"),
    # 多实例段任意键（裸父段除外，见 TestUpdateConfigMultiInstanceSegment）
    ("notify-webhook-1", "url"),
    ("notify-webhook-abc", "url"),
]


class TestUpdateConfigWritePolicy:
    """写入策略：①MCP自身不可用 / ②需回页面的键禁改，其余开放。"""

    @staticmethod
    def _make_write_token():
        mock_token = MagicMock()
        mock_token.scopes = ["read", "write"]
        return mock_token

    async def _call(self, section, key, value="v"):
        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section=section, key=key, value=value
                )
        return result, mock_cm

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("section", "key"), DENIED_WRITE_KEYS)
    async def test_update_config_禁用清单内的键_抛出ToolError(self, section, key):
        """①MCP不可用 / ②需回页面的键 → 拒绝写入。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(section=section, key=key, value="v")

        assert "Web 界面" in str(exc_info.value)
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("section", "key"), ALLOWED_WRITE_KEYS)
    async def test_update_config_开放清单内的键_写入成功(self, section, key):
        """③类键 → 正常写入（含此前被误拒的合法配置）。"""
        result, mock_cm = await self._call(section, key)
        assert result["status"] == "success"
        mock_cm.set_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_config_所有登记段的合法示例键_均可写入(self):
        """不变式：config.example.ini 里除禁改清单外的全部键都应可写。

        回归防护：旧实现以 SectionMeta.fields 作白名单，会误拒未登记的
        合法键（dev.debug / llm.model 等），本用例锁死该行为不再出现。
        """
        import configparser
        from pathlib import Path

        from app.core.config_schema import SECTIONS

        denied = set(DENIED_WRITE_KEYS)
        ini_path = Path(__file__).resolve().parents[1] / "config.example.ini"
        parser = configparser.ConfigParser()
        parser.read(ini_path, encoding="utf-8")

        checked = 0
        for section in sorted(SECTIONS):
            if not parser.has_section(section):
                continue
            if section in READONLY_SECTIONS:
                continue  # 只读段由 TestUpdateConfigReadonlySections 单独验证
            for key in parser.options(section):
                if (section, key) in denied:
                    continue
                result, mock_cm = await self._call(section, key)
                assert result["status"] == "success", f"{section}.{key} 被误拒"
                checked += 1
        assert checked > 50, "示例配置解析异常，用例失去意义"

    @pytest.mark.asyncio
    async def test_update_config_下划线段名归一化后仍命中禁改清单(self):
        """auth 的下划线写法不能绕过①类禁改（归一化后仍按连字符比对）。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError):
                    await tools.update_config(section="AUTH", key="password", value="x")

        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_config_底层ValueError_转为ToolError(self):
        """set_config 抛 ValueError 时应转成 ToolError（评审 1c）。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()
        mock_cm.set_config.side_effect = ValueError("非法配置段名")
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError):
                    await tools.update_config(
                        section="sync", key="match_confidence_threshold", value="v"
                    )


# ===========================================================================
# get_logs 时间过滤（评审 #259 第 3/4 条 + 次要项）
#
# 场景：
#   1. 时区：带时区的输入应换算到服务器本地，而不是只剥掉标签
#   2. 顺序：有时间条件时应先过滤再取 limit，否则窗口靠前的日志被吞
#   3. 续行：多行 traceback 的续行沿用上一行时间戳，不应整段丢失
#   4. 自解释：文件日志被禁用时返回明确说明，而非静默空内容
# ===========================================================================

# 10 行日志，时间递增；末行为多行 traceback（含无时间戳的续行）
SAMPLE_LOG_SPAN = (
    "[2026/09/03 10:00:00.000] [INFO]  行1\n"
    "[2026/09/03 11:00:00.000] [INFO]  行2\n"
    "[2026/09/03 12:00:00.000] [INFO]  行3\n"
    "[2026/09/03 13:00:00.000] [INFO]  行4\n"
    "[2026/09/03 14:00:00.000] [INFO]  行5\n"
    "[2026/09/03 15:00:00.000] [INFO]  行6\n"
    "[2026/09/03 16:00:00.000] [INFO]  行7\n"
    "[2026/09/03 17:00:00.000] [ERROR] 行8 触发了异常\n"
    '  File "sync.py", line 42, in run\n'
    "    raise RuntimeError('boom')\n"
)


class TestGetLogsTimezone:
    """时区：带 tz 的输入应换算到服务器本地时区后再比较。"""

    def test_parse_iso_同一时刻的两种时区表示_结果相同(self):
        """'Z' 与等价偏移量应解析为同一时刻（而非各自剥标签后相差时差）。"""
        from app.mcp.tools import _parse_iso_datetime

        utc = _parse_iso_datetime("2026-09-03T10:00:00Z")
        offset = _parse_iso_datetime("2026-09-03T18:00:00+08:00")
        assert utc == offset
        assert utc.tzinfo is None

    def test_parse_iso_naive输入_按服务器本地原样保留(self):
        """无时区信息的输入视为服务器本地时间，不做换算。"""
        from datetime import datetime

        from app.mcp.tools import _parse_iso_datetime

        assert _parse_iso_datetime("2026-09-03T10:00:00") == datetime(
            2026, 9, 3, 10, 0, 0
        )

    def test_parse_iso_非法格式_抛ValueError(self):
        from app.mcp.tools import _parse_iso_datetime

        with pytest.raises(ValueError):
            _parse_iso_datetime("not-a-date")


class TestGetLogsFilterOrder:
    """顺序：应先按时间过滤、再应用 limit。"""

    @staticmethod
    def _make_read_token():
        mock_token = MagicMock()
        mock_token.scopes = ["read"]
        return mock_token

    @pytest.mark.asyncio
    async def test_get_logs_有时间条件时_读取全量而非尾部limit(self):
        """带 since/until 时应读全量，避免窗口靠前的日志被尾部 limit 吞掉。"""
        from app.mcp import tools

        mock_dispatch = AsyncMock(
            return_value={
                "content": SAMPLE_LOG_SPAN,
                "stats": {"size": 1, "lines": 10, "modified": None, "errors": 0},
            }
        )
        with patch(
            "app.mcp.tools.get_access_token", return_value=self._make_read_token()
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools._dispatch_read_log", new=mock_dispatch):
                        await tools.get_logs(limit=3, since="2026-09-03T10:00:00")

        assert mock_dispatch.call_args.args[3] == "all"

    @pytest.mark.asyncio
    async def test_get_logs_无时间条件时_仍按limit读尾部(self):
        """无 since/until 时保持原有行为：只读尾部 N 行。"""
        from app.mcp import tools

        mock_dispatch = AsyncMock(
            return_value={
                "content": "",
                "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
            }
        )
        with patch(
            "app.mcp.tools.get_access_token", return_value=self._make_read_token()
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools._dispatch_read_log", new=mock_dispatch):
                        await tools.get_logs(limit=7)

        assert mock_dispatch.call_args.args[3] == "7"

    @pytest.mark.asyncio
    async def test_get_logs_窗口靠前且超出limit_仍能命中(self):
        """since 落在较早区间时，即使 limit 很小也应返回该窗口内的日志。

        旧实现先按 limit 取尾部 N 行再过滤，窗口不在尾部时会返回空，
        表现为「这段时间没有日志」的假象。
        """
        from app.mcp import tools

        mock_dispatch = AsyncMock(
            return_value={
                "content": SAMPLE_LOG_SPAN,
                "stats": {"size": 1, "lines": 10, "modified": None, "errors": 0},
            }
        )
        with patch(
            "app.mcp.tools.get_access_token", return_value=self._make_read_token()
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools._dispatch_read_log", new=mock_dispatch):
                        result = await tools.get_logs(
                            limit=3,
                            since="2026-09-03T12:00:00",
                            until="2026-09-03T14:00:00",
                        )

        content = result["data"]["content"]
        # 窗口内为 行3/行4/行5，共 3 行，不应被 limit 截成空
        assert "行3" in content
        assert "行5" in content
        assert "行1" not in content

    @pytest.mark.asyncio
    async def test_get_logs_过滤后仍受limit截断(self):
        """全量读取后应按 limit 截断，避免返回整个文件。"""
        from app.mcp import tools

        mock_dispatch = AsyncMock(
            return_value={
                "content": SAMPLE_LOG_SPAN,
                "stats": {"size": 1, "lines": 10, "modified": None, "errors": 0},
            }
        )
        with patch(
            "app.mcp.tools.get_access_token", return_value=self._make_read_token()
        ):
            with patch(
                "app.mcp.tools.resolved_dev_log_file_path",
                return_value=Path("/fake/app.log"),
            ):
                with patch("app.mcp.tools.os.path.exists", return_value=True):
                    with patch("app.mcp.tools._dispatch_read_log", new=mock_dispatch):
                        result = await tools.get_logs(
                            limit=2,
                            since="2026-09-03T10:00:00",
                            until="2026-09-03T17:00:00",
                        )

        assert result["data"]["content"].count("\n") <= 2


class TestGetLogsContinuationLines:
    """续行：多行 traceback 的无时间戳行应沿用上一行时间戳。"""

    def test_filter_续行沿用上一行时间戳_窗口内应保留(self):
        from datetime import datetime

        from app.mcp.tools import _filter_by_time_range

        out = _filter_by_time_range(
            SAMPLE_LOG_SPAN, datetime(2026, 9, 3, 17, 0, 0), None
        )
        assert 'File "sync.py"' in out
        assert "raise RuntimeError" in out

    def test_filter_首行无时间戳_应保留而非丢弃(self):
        """文件开头若有无时间戳行（如 banner），不应被整条丢弃。"""
        from datetime import datetime

        from app.mcp.tools import _filter_by_time_range

        content = "==== 启动 banner ====\n" + SAMPLE_LOG_SPAN
        out = _filter_by_time_range(
            content, datetime(2026, 9, 3, 10, 0, 0), datetime(2026, 9, 3, 23, 0, 0)
        )
        assert "启动 banner" in out


class TestGetLogsDisabledNotice:
    """自解释：文件日志被禁用时应给出明确说明。"""

    @staticmethod
    def _make_read_token():
        mock_token = MagicMock()
        mock_token.scopes = ["read"]
        return mock_token

    @pytest.mark.asyncio
    async def test_get_logs_日志被禁用时_返回说明而非静默空(self):
        """[dev] log_file 置空会禁用文件日志，此时应说明原因。"""
        from app.mcp import tools

        with patch(
            "app.mcp.tools.get_access_token", return_value=self._make_read_token()
        ):
            with patch("app.mcp.tools.resolved_dev_log_file_path", return_value=None):
                result = await tools.get_logs()

        data = result["data"]
        assert data["content"] == ""
        assert "log_file" in data["notice"]


class TestUpdateConfigMultiInstanceSegment:
    """多实例段名须带后缀：裸父段运行时读不到，是永不生效的死段。

    运行时以 ``startswith("notify-webhook-")`` 枚举实例（notification_service.py），
    ``[notify-webhook]`` 缺尾部横杠不匹配，写入后永远不会被读取。
    后缀内容不限（``-abc`` 会被当真实渠道使用，与页面操作等价）。
    """

    @staticmethod
    def _make_write_token():
        mock_token = MagicMock()
        mock_token.scopes = ["read", "write"]
        return mock_token

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "section",
        [
            "notify-webhook",
            "notify-email",
            "notify-wecom",
            "notify-dingtalk",
            "notify-rule",
            "summary",
        ],
    )
    async def test_update_config_裸父段_拒绝(self, section):
        """裸父段（无 -N 后缀）应拒绝：写入后运行时读不到。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(section=section, key="url", value="x")

        assert "实例" in str(exc_info.value)
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "section",
        ["notify-webhook-1", "notify-webhook-abc", "notify-email-2", "summary-1"],
    )
    async def test_update_config_带后缀的实例段_放行(self, section):
        """带后缀的实例段应放行（后缀不限数字，因其会被真实读取）。"""
        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section=section, key="url", value="x"
                )

        assert result["status"] == "success"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("section", ["notify-airing-today", "notify-in-app"])
    async def test_update_config_非多实例的notify段_不受后缀限制(self, section):
        """notify-airing-today / notify-in-app 不是多实例段，不应被误伤。"""
        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section=section, key="enabled", value="true"
                )

        assert result["status"] == "success"


class TestUpdateConfigFollowupNotice:
    """② 类键：允许修改，但返回值须提示用户后续需执行的操作。

    这些键本身可逆（用户回 Web 界面即可修正或完成授权），
    硬性禁止会削弱 MCP 的实用性；改为写入成功后主动提示。
    """

    @staticmethod
    def _make_write_token():
        mock_token = MagicMock()
        mock_token.scopes = ["read", "write"]
        return mock_token

    async def _call(self, section, key, value="v"):
        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                result = await tools.update_config(
                    section=section, key=key, value=value
                )
        return result, mock_cm

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("section", "key"), FOLLOWUP_REQUIRED_KEYS)
    async def test_update_config_需后续操作类键_写入成功且带后续提示(
        self, section, key
    ):
        """② 类键应写入成功，且 notice 说明用户需做什么。"""
        result, mock_cm = await self._call(section, key)

        assert result["status"] == "success"
        mock_cm.set_config.assert_called_once()
        assert result.get("notice"), f"{section}.{key} 缺少后续操作提示"
        assert len(result["notice"]) > 5

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("section", "key"), FOLLOWUP_REQUIRED_KEYS)
    async def test_update_config_需后续操作类键_提示逐键不同(self, section, key):
        """提示文案应按 (段,键) 区分，避免所有键共用一句空话。"""
        from app.mcp.tools import _MCP_WRITE_FOLLOWUP

        assert (section, key) in _MCP_WRITE_FOLLOWUP
        assert _MCP_WRITE_FOLLOWUP[(section, key)].strip()

    @pytest.mark.asyncio
    async def test_update_config_普通键_不返回提示(self):
        """③ 类键无需提示，避免噪音。"""
        result, _ = await self._call("sync", "match_confidence_threshold", "0.7")
        assert result["status"] == "success"
        assert not result.get("notice")

    @pytest.mark.asyncio
    async def test_update_config_禁改键_不返回提示只报错(self):
        """① 类键应直接拒绝，不给「已写入」语义的提示。"""
        from fastmcp.exceptions import ToolError

        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await tools.update_config(section="auth", key="password", value="x")

        assert "Web 界面" in str(exc_info.value)
        mock_cm.set_config.assert_not_called()


class TestUpdateConfigNewKeyHint:
    """新增键提示：AI 拼错键名时静默写入会留下垃圾配置，需主动提醒复核。"""

    @staticmethod
    def _make_write_token():
        mock_token = MagicMock()
        mock_token.scopes = ["read", "write"]
        return mock_token

    @staticmethod
    def _mock_cm(key_exists: bool) -> MagicMock:
        parser = MagicMock()
        parser.has_option.return_value = key_exists
        cm = MagicMock()
        cm.get_config_parser.return_value = parser
        return cm

    async def _call(self, section, key, key_exists):
        from app.mcp import tools

        mock_cm = self._mock_cm(key_exists)
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                return await tools.update_config(section=section, key=key, value="v")

    @pytest.mark.asyncio
    async def test_update_config_新增键_提示复核(self):
        """键此前不存在时应提示「疑似拼写错误，请复核」。"""
        result = await self._call("sync", "made_up_key", key_exists=False)
        assert result["status"] == "success"
        assert "此前不存在" in result["notice"]
        assert "get_current_config" in result["notice"]

    @pytest.mark.asyncio
    async def test_update_config_已有键_不提示新增(self):
        """键已存在时不应产生噪音提示。"""
        result = await self._call("sync", "match_confidence_threshold", key_exists=True)
        assert result["status"] == "success"
        assert not result.get("notice")

    @pytest.mark.asyncio
    async def test_update_config_新增键且属需后续操作类_两段提示合并(self):
        """②类键同时是新键时，两条提示应合并而非互相覆盖。"""
        result = await self._call("trakt", "client_secret", key_exists=False)
        notice = result["notice"]
        assert "OAuth" in notice
        assert "此前不存在" in notice


class TestUpdateConfigReadonlySections:
    """只读段：Web 界面无写入入口，写入会被系统当作数据（而非配置）搬走。

    典型是 [bangumi]：schema 标注 visible_in_ui=False，hidden_reason 声明
    「仅为旧版 config.ini 的迁移来源」。写入后会在下次启动被
    migrate_ini_accounts_to_db() 搬进数据库成为可用账号，且 INI 痕迹随后
    被清除，用户无法从配置文件察觉。
    """

    @staticmethod
    def _make_write_token():
        mock_token = MagicMock()
        mock_token.scopes = ["read", "write"]
        return mock_token

    async def _probe(self, section, key, value="v"):
        from app.mcp import tools

        mock_cm = MagicMock()
        with patch("app.mcp.tools.config_manager", mock_cm):
            with patch(
                "app.mcp.tools.get_access_token",
                return_value=self._make_write_token(),
            ):
                try:
                    result = await tools.update_config(
                        section=section, key=key, value=value
                    )
                    return ("放行", result, mock_cm)
                except Exception as e:  # noqa: BLE001
                    return ("拒绝", e, mock_cm)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "key",
        [
            "access_token",
            "refresh_token",
            "username",
            "private",
            "media_server_username",
            "made_up_key",
        ],
    )
    async def test_update_config_bangumi段任意键_拒绝(self, key):
        """[bangumi] 段的任何键都不可写（含未登记的键）。"""
        from fastmcp.exceptions import ToolError

        verdict, payload, mock_cm = await self._probe("bangumi", key)

        assert verdict == "拒绝"
        assert isinstance(payload, ToolError)
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_config_bangumi段_错误信息给出正确入口(self):
        """拒绝时必须告知正确修改方式，而不是只说「不允许」。"""
        verdict, exc, _ = await self._probe("bangumi", "access_token", "ATTACKER_TOKEN")

        msg = str(exc)
        assert "迁移来源" in msg, "未说明该段为何不可写"
        assert "Bangumi 账号" in msg, "未指明正确入口"
        assert "Web" in msg, "未指明入口位置"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("section", ["BANGUMI", "bangumi_", "Bangumi"])
    async def test_update_config_bangumi段名变形_不能绕过(self, section):
        """段名变形不能绕过拒绝（由段名白名单先行拦截，只读判定不必兜住）。

        大小写变体不在 _valid_section_names() 中、``bangumi_`` 归一化后为
        ``bangumi-`` 同样不在，二者在只读判定之前即被拒，故不会误伤也无需
        依赖只读清单做归一化。
        """
        verdict, _, mock_cm = await self._probe(section, "access_token")

        assert verdict == "拒绝"
        mock_cm.set_config.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("section", "key"),
        [
            ("bangumi-data", "cache_ttl_days"),
            ("bangumi-replay", "enabled"),
            ("bangumi-archive", "enabled"),
            ("bangumi-mapping", "some_key"),
            ("bangumi-oauth", "client_id"),
        ],
    )
    async def test_update_config_其他bangumi前缀段_不被误伤(self, section, key):
        """只读判定必须精确匹配 [bangumi]，不得波及 bangumi-* 兄弟段。"""
        verdict, payload, _ = await self._probe(section, key)

        assert verdict == "放行", f"{section}.{key} 被误拒: {payload}"
        assert payload["status"] == "success"

    @pytest.mark.asyncio
    async def test_update_config_bangumi段的敏感键_与只读判定无关(self):
        """只读段判定应独立于 sensitive_fields：非敏感键同样被拒。

        [bangumi] 的 sensitive_fields 只有 access_token/refresh_token，
        但 username/private 等非敏感键写入同样会触发账号迁移。
        """
        from fastmcp.exceptions import ToolError

        verdict, payload, mock_cm = await self._probe("bangumi", "username", "attacker")

        assert verdict == "拒绝"
        assert isinstance(payload, ToolError)
        mock_cm.set_config.assert_not_called()
