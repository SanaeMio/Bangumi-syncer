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

from app.mcp.keys import RSAKeyManager


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
        assert (nested_private.stat().st_mode & 0o777) == 0o600
