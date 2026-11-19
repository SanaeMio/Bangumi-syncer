"""MCP RSA 密钥路径默认值与父目录自愈的回归测试。

守卫 PR #11 评审确认的问题，防止回退：

1. 未设置环境变量时，RSA 密钥默认落项目数据目录 ``data/``（Docker 下
   即 ``/app/data``），而**不是**系统临时目录——否则容器重建或清理
   临时目录会重新生成密钥对，已签发的 Access Token 全部验签失败，
   客户端被迫重新授权。
2. ``MCP_RSA_PRIVATE_KEY`` / ``MCP_RSA_PUBLIC_KEY`` 仍可覆盖默认路径。
3. ``RSAKeyManager`` 写文件前自动创建父目录，任意自定义嵌套路径均可写入。
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from app.mcp import keys as keys_module
from app.mcp.keys import RSAKeyManager
from tests.mcp_helpers import assert_private_key_owner_only


class TestDefaultKeyPaths:
    """默认密钥路径随数据目录持久化，而非落在系统临时目录。"""

    def test_密钥路径_未设置环境变量_默认落data目录(self, monkeypatch):
        """未设置环境变量时，默认私钥/公钥路径应为 data/ 下且不含临时目录。"""
        monkeypatch.delenv("MCP_RSA_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("MCP_RSA_PUBLIC_KEY", raising=False)

        from app.mcp.server import _resolve_key_paths

        private_path, public_path = _resolve_key_paths()

        assert private_path == os.path.join("data", "mcp_private.pem")
        assert public_path == os.path.join("data", "mcp_public.pem")
        # 回归守卫：默认路径不得再落在系统临时目录。
        assert tempfile.gettempdir() not in private_path
        assert tempfile.gettempdir() not in public_path

    def test_密钥路径_环境变量已设置_覆盖默认路径(self, monkeypatch, tmp_path):
        """环境变量覆盖能力必须保留：设置后返回环境变量指定的路径。"""
        custom_private = str(tmp_path / "custom_private.pem")
        custom_public = str(tmp_path / "custom_public.pem")
        monkeypatch.setenv("MCP_RSA_PRIVATE_KEY", custom_private)
        monkeypatch.setenv("MCP_RSA_PUBLIC_KEY", custom_public)

        from app.mcp.server import _resolve_key_paths

        private_path, public_path = _resolve_key_paths()

        assert private_path == custom_private
        assert public_path == custom_public


class TestKeyParentDirAutoCreate:
    """密钥文件父目录不存在时自动创建。"""

    def test_密钥生成_父目录不存在_自动创建且私钥0600(self, tmp_path):
        """嵌套父目录不存在时，load_or_generate 应创建目录、生成密钥并置 0600。"""
        nested_private = tmp_path / "nested" / "keys" / "mcp_private.pem"
        nested_public = tmp_path / "nested" / "keys" / "mcp_public.pem"
        assert not nested_private.parent.exists()

        manager = RSAKeyManager(
            private_key_path=str(nested_private),
            public_key_path=str(nested_public),
        )
        manager.load_or_generate()

        assert nested_private.exists()
        assert nested_public.exists()
        assert_private_key_owner_only(nested_private)


class TestPrivateKeyPermissionHardening:
    """私钥权限收紧的平台差异。

    Windows 的 ``os.chmod`` 只映射只读位、``st_mode`` 不反映 NTFS ACL，
    无法回读校验 0o600（见 ``keys._POSIX_MODE_SUPPORTED``）；POSIX 侧必须
    保持「回读到非 0600 就告警并强制修正」的原有行为。
    """

    @pytest.mark.skipif(
        os.name != "posix", reason="Windows 无法用 st_mode 表达 0o600 权限位"
    )
    def test_权限位不符时_告警并强制修正为0600(self, tmp_path, monkeypatch):
        """POSIX：已有私钥权限宽松时，加载阶段应修正为 0600 并留下告警。"""
        private_path = tmp_path / "mcp_private.pem"
        public_path = tmp_path / "mcp_public.pem"
        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.load_or_generate()
        private_path.chmod(0o644)  # 模拟权限被放宽

        # 项目 logger 是自制 Logger（非 logging.Logger），caplog 抓不到，直接盯调用。
        warning = MagicMock()
        monkeypatch.setattr(keys_module.logger, "warning", warning)
        manager._ensure_private_key_permissions()

        assert (private_path.stat().st_mode & 0o777) == 0o600
        assert warning.call_args is not None
        assert warning.call_args[0][-1] == 0o644  # 告警里带上原权限位
