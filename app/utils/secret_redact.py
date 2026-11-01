"""密钥脱敏工具（best-effort）。

用于 ``last_error`` 等会经 API 暴露给客户端的文本：落库前统一遮蔽常见密钥
模式，避免异常文本（``str(e)``）携带 token / api_key / password 等泄露。

设计原则：
- **只遮蔽敏感值，保留键名/授权 scheme 前缀**，便于排查时辨识来源
  （如 ``Bearer ***``、``access_token=***``）。
- **best-effort**：识别不到敏感模式时原样返回，绝不误伤普通文本。
- 单次正则扫描完成（替换结果不会被二次扫描），避免多轮替换互相干扰。

覆盖模式（大小写不敏感）：
- ``Bearer <token>`` / ``Basic <token>``（含 ``Authorization: Bearer ...`` 前缀）
- 键值 / URL 查询串：``access_token=...``、``refresh_token=...``、``token=...``、
  ``api_key=`` / ``apikey=``、``secret=...``、``password=...``
- JSON / 引号形式：``"access_token": "..."``、``'password': '...'``
"""

from __future__ import annotations

import re
from typing import Any

# 遮蔽标记（所有模式统一使用，保持输出风格一致）
MASK = "***"

# 键名集合（长键在前，避免 ``token`` 抢先匹配 ``access_token`` 的子串）
_SECRET_KEYS = (
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "authorization",
    "token",
)

# 单个敏感值：可选 ``Bearer``/``Basic`` scheme 前缀 + 非空白值
# （排除引号/&/,/;/} 等分隔符，避免吞掉后续正常内容）
_VALUE = r"""(?:(?:bearer|basic)\s+)?[^\s"'&,;}\]]+"""

# 组合正则，单次扫描三分支：
#   1. ``Authorization[:=] <scheme> <token>``（保留 scheme）
#   2. 裸 ``Bearer <token>``（保留 scheme）
#   3. ``key=value`` / ``key: value`` / JSON 引号形式（保留键名与引号）
_SECRET_RE = re.compile(
    rf"""(?ix)
    (?:
        (?P<auth_key>["']?authorization["']?)
        (?P<auth_sep>\s*[:=]\s*)
        (?P<auth_scheme>bearer|basic)(?P<auth_ws>\s+)
        (?P<auth_value>[^\s"'&,;}}\]]+)
      |
        (?P<scheme>(?<![A-Za-z0-9_])bearer)(?P<scheme_ws>\s+)
        (?P<scheme_value>[^\s"'&,;}}\]]+)
      |
        (?P<key>["']?(?:{"|".join(_SECRET_KEYS)})["']?)
        (?P<sep>\s*[:=]\s*)
        (?P<quote>["']?)
        (?P<value>{_VALUE})
        (?P<closing>["']?)
    )
    """
)


def _mask_value(value: str) -> str:
    """遮蔽单个值，保留 ``Bearer``/``Basic`` scheme 前缀便于辨识。"""
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0].lower() in ("bearer", "basic"):
        return f"{parts[0]} {MASK}"
    return MASK


def _replace(match: re.Match) -> str:
    """按命中的分支生成替换文本（键名/引号/scheme 前缀保留）。"""
    if match.group("auth_key") is not None:
        return (
            f"{match.group('auth_key')}{match.group('auth_sep')}"
            f"{match.group('auth_scheme')} {MASK}"
        )
    if match.group("scheme") is not None:
        return f"{match.group('scheme')} {MASK}"
    return (
        f"{match.group('key')}{match.group('sep')}"
        f"{match.group('quote')}{_mask_value(match.group('value'))}"
        f"{match.group('closing')}"
    )


def redact_secrets(text: Any) -> Any:
    """遮蔽文本中的常见密钥模式（best-effort，不改变正常可读信息）。

    ``None`` / 空串原样返回；未命中任何敏感模式时原样返回。
    非 ``str`` 输入（如 int / dict / Exception）原样返回，绝不抛 ``TypeError``
    （调用方可能透传任意异常对象，脱敏工具不应成为新的故障点）。
    """
    if not isinstance(text, str):
        return text
    if not text:
        return text
    return _SECRET_RE.sub(_replace, text)
