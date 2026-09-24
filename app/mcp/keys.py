"""MCP OAuth 的 RSA 密钥管理（RS256 JWT 签名/验签）。

从 ``app.mcp.provider`` 拆分而来，仅承载密钥生命周期，不含 OAuth 流程逻辑。
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

logger = logging.getLogger(__name__)


class RSAKeyManager:
    """管理用于 JWT 签名（RS256）的 RSA 密钥对。"""

    def __init__(
        self,
        private_key_path: str,
        public_key_path: str,
    ) -> None:
        self.private_key_path = private_key_path
        self.public_key_path = public_key_path
        self._private_key: rsa.RSAPrivateKey | None = None
        self._public_key: rsa.RSAPublicKey | None = None

    @staticmethod
    def _ensure_parent_dir(path: str) -> None:
        """确保目标文件的父目录存在（任意自定义路径均可写入）。

        路径不含目录部分时回退到当前目录 ``.``。
        """
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def generate_keys(self) -> None:
        """生成新的 RSA 密钥对并保存到磁盘。"""
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self._private_key = private_key
        self._public_key = private_key.public_key()

        # 设计取舍：私钥以明文 PKCS#8（NoEncryption）落盘。安全边界依赖：
        #   1) 文件权限 0600——生成时 chmod，加载时 _ensure_private_key_permissions
        #      强制回正；
        #   2) data/ 目录隔离且已 gitignore，不会误入版本库。
        # 容器内进程需无交互启动并读取密钥，故不设口令；如未来确有需要，
        # 可引入 MCP_RSA_KEY_PASSWORD 走 passphrase 加密路径。
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        # 确保目录存在
        self._ensure_parent_dir(self.private_key_path)

        with open(self.private_key_path, "wb") as f:
            f.write(private_pem)
        # 将私钥权限设为仅属主可读写（chmod 600）
        os.chmod(self.private_key_path, 0o600)
        self._write_public_key()

    def load_or_generate(self) -> None:
        """从磁盘加载已有密钥；未找到则生成新密钥对。

        若仅存在私钥，则会从该私钥重新派生公钥（私钥绝不会被替换）。只有私钥
        缺失才会触发生成新密钥对，因此意外删除公钥不会使此前已签发的 JWT
        失效；只要私钥仍在，公钥就能随时按需重建，既有 token 的有效性
        不受影响。
        """
        if os.path.exists(self.private_key_path):
            self._load_private_key()
            self._ensure_private_key_permissions()
            if os.path.exists(self.public_key_path):
                self._load_public_key()
            else:
                logger.warning(
                    "公钥 %s 缺失，正从私钥 %s 重新派生",
                    self.public_key_path,
                    self.private_key_path,
                )
                self._write_public_key()
        else:
            logger.info(
                "私钥 %s 不存在，正在生成新的 RSA 密钥对",
                self.private_key_path,
            )
            self.generate_keys()

    def _load_private_key(self) -> None:
        """从磁盘加载私钥，并校验其确为 RSA 私钥。"""
        with open(self.private_key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError(f"私钥文件不是 RSA 私钥: {self.private_key_path}")
        self._private_key = key

    def _load_public_key(self) -> None:
        """从磁盘加载公钥，并校验其确为 RSA 公钥。"""
        with open(self.public_key_path, "rb") as f:
            key = serialization.load_pem_public_key(f.read())
        if not isinstance(key, rsa.RSAPublicKey):
            raise ValueError(f"公钥文件不是 RSA 公钥: {self.public_key_path}")
        self._public_key = key

    def _ensure_private_key_permissions(self) -> None:
        """对已有私钥强制设置仅属主（0o600）权限。"""
        current_mode = os.stat(self.private_key_path).st_mode & 0o777
        if current_mode != 0o600:
            logger.warning(
                "私钥 %s 权限为 0o%o，正在强制修正为仅属主 0o600",
                self.private_key_path,
                current_mode,
            )
            os.chmod(self.private_key_path, 0o600)

    def _write_public_key(self) -> None:
        """从已加载的私钥派生公钥并持久化。"""
        self._public_key = self.private_key.public_key()
        public_pem = self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._ensure_parent_dir(self.public_key_path)
        with open(self.public_key_path, "wb") as f:
            f.write(public_pem)

    @property
    def private_key(self) -> rsa.RSAPrivateKey:
        if self._private_key is None:
            raise RuntimeError("Keys not loaded. Call load_or_generate() first.")
        return self._private_key

    @property
    def public_key(self) -> rsa.RSAPublicKey:
        if self._public_key is None:
            raise RuntimeError("Keys not loaded. Call load_or_generate() first.")
        return self._public_key

    def get_private_key_pem(self) -> bytes:
        """获取 PEM 格式的私钥。"""
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def get_public_key_pem(self) -> bytes:
        """获取 PEM 格式的公钥。"""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def sign_jwt(self, claims: dict[str, Any]) -> str:
        """用 RS256 对 JWT 签名；若缺失 jti 则补充以保证唯一性。"""
        if "jti" not in claims:
            claims["jti"] = secrets.token_urlsafe(16)
        return jwt.encode(claims, self.get_private_key_pem(), algorithm="RS256")

    def verify_jwt(
        self, token: str, audience: str | None = None
    ) -> dict[str, Any] | None:
        """用公钥验证 JWT，返回 claims；无效则返回 None。

        ``audience=None`` 表示**跳过受众（aud）校验**，仅用于显式不需要校验
        的调用场景；授权服务器的生产路径（``load_access_token``）始终传入
        非空 audience 做严格校验。

        失败时按异常类型记 warning 便于线上定位；**只打印异常类型名**，
        绝不输出 token 原文或可能含敏感内容的异常消息。
        """
        try:
            return jwt.decode(
                token,
                self.get_public_key_pem(),
                algorithms=["RS256"],
                audience=audience,
                options={"verify_aud": audience is not None},
            )
        except jwt.ExpiredSignatureError as exc:
            logger.warning(
                "JWT 已过期，拒绝该 token（异常类型=%s）", type(exc).__name__
            )
            return None
        except jwt.InvalidTokenError as exc:
            logger.warning(
                "JWT 验证失败，拒绝该 token（异常类型=%s）", type(exc).__name__
            )
            return None
