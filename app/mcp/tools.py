"""
MCP 工具函数（FastMCP 4 async tools）

直接调用 BS 业务层（同进程），不再走 HTTP。
提供 3 个工具：get_logs / get_current_config / update_config。
"""

import asyncio
import os
import re
from datetime import datetime
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token

from app.api.logs import _read_log_file
from app.core.config import config_manager
from app.core.config_schema import (
    SECTIONS,
    is_sensitive_field,
    multi_instance_prefixes,
)
from app.core.logging import resolved_dev_log_file_path

# 日志级别白名单（get_logs 参数校验）
VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


def _require_read_scope() -> None:
    """校验 access token 存在且含 read scope，否则抛出 ToolError。"""
    token = get_access_token()
    if token is None:
        raise ToolError("未找到访问令牌，无法读取。请提供含 read 权限的 access token。")
    scopes = getattr(token, "scopes", [])
    if "read" not in scopes:
        raise ToolError(f"权限不足：需要 read scope，当前 scope: {scopes}")


# 敏感字段回显掩码
_MASK = "***"

# MCP 侧专用敏感字段掩码集合：除 config_schema 的 sensitive_fields 外，
# 额外屏蔽 auth 段的高危字段（口令哈希 / 派生 Fernet 密钥的主密钥）。
# 不改动 config_schema.sensitive_fields，避免影响落盘加密语义。
_EXTRA_SENSITIVE_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {("auth", "password"), ("auth", "secret_key")}
)

# ── update_config 禁改键清单 ───────────────────────────────────────────────
#
# 判据不是「键敏不敏感」，而是「**改完之后用户还能不能自己救回来**」：
#
#   ① 改完之后用户**无法自行恢复** → 禁改
#      仅限登录通道本身：auth.enabled（全站免鉴权）/ secret_key（使已存
#      密码哈希全部失效）/ password（不知新密码即失去访问权）。
#   ② 改完之后用户还需做一步操作 → 放行，但返回值附 notice 提示
#      见 _MCP_WRITE_FOLLOWUP。硬性禁止会削弱 MCP 实用性，提示优于拒绝。
#   ③ 其余一律开放（等同 Web 界面）     → 放行
#      包含 llm.api_key、notify-* 凭据、auth.webhook_key 等
#      「重填一个字符串即可恢复」的键，与用户在 Web 界面的能力等价。
#
# 早期实现曾把 ``SectionMeta.fields`` 当白名单（"fields 为空即拒绝"），但
# ``fields`` 的语义是「需要特殊默认值/布尔渲染的项」而非完整键清单
# （见 FieldMeta docstring），用它做白名单会误拒 dev.debug / llm.model
# 等大量合法配置；同时对 fields 为空的段（bangumi / trakt / web）反而
# 完全放行，导致凭据可写。故改为本清单。
_MCP_WRITE_DENIED: frozenset[tuple[str, str]] = frozenset(
    {
        # ① 登录通道本身：改完用户无法自行恢复，只能留在 Web 界面操作
        ("auth", "enabled"),  # 置 false 会让全站免鉴权（见 app/api/deps.py）
        (
            "auth",
            "secret_key",
        ),  # verify_password 以其做 HMAC，改动使已存密码哈希全部失效
        ("auth", "password"),  # 用户不知新密码即永久失去访问权
    }
)

# ② 允许改、但改完用户需执行后续操作的键 → 写入成功后通过返回值 notice 提示。
# 这些键本身可逆（用户回 Web 界面即可修正或完成授权），硬性禁止会削弱 MCP
# 的实用性；提示优于拒绝。提示文案按 (段, 键) 区分，避免共用一句空话。
_MCP_WRITE_FOLLOWUP: dict[tuple[str, str], str] = {
    (
        "auth",
        "username",
    ): "已修改登录用户名，下次登录请使用新用户名；已登录会话不受影响",
    ("auth", "max_login_attempts"): (
        "已修改最大登录尝试次数，调小可能导致连续失败后被锁定，请确认取值合理"
    ),
    ("auth", "lockout_duration"): "已修改锁定时长（分钟），请确认与预期一致",
    ("auth", "webhook_auth_enabled"): (
        "已修改 Webhook 鉴权开关，置为关闭后 /api/webhook 接口将不再校验凭据，"
        "请确认这是预期行为"
    ),
    ("trakt", "client_secret"): (
        "已更新 Trakt API 凭据，该配置在进程启动时读取，"
        "且凭据变更后需在「Trakt 同步」页面重新完成 OAuth 授权才能生效"
    ),
    ("bangumi-oauth", "client_secret"): (
        "已更新 Bangumi OAuth 应用密钥，需重新完成 OAuth 授权流程才能生效"
    ),
    ("bangumi", "access_token"): (
        "已更新 Bangumi access_token，该字段仅用于从旧版配置迁移账号，"
        "不会影响数据库中已入库的账号；如需更换账号请在「Bangumi 账号」卡片操作"
    ),
    ("bangumi", "refresh_token"): (
        "已更新 Bangumi refresh_token，同上仅影响旧版配置迁移来源，"
        "已入库账号的令牌请在「Bangumi 账号」卡片管理"
    ),
}

# 日志行时间戳格式: [2026/09/03 12:00:00.123]
_LOG_TIMESTAMP_RE = re.compile(r"^\[(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\]")


def _parse_log_timestamp(line: str) -> datetime | None:
    """解析日志行中的时间戳；无法解析返回 None。"""
    m = _LOG_TIMESTAMP_RE.match(line)
    if not m:
        return None
    ts_str = m.group(1)
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def _parse_iso_datetime(value: str) -> datetime:
    """解析 ISO 格式时间字符串（since/until），无效格式抛出 ValueError。

    先把 ``Z`` 后缀与空格分隔标准化为 ``fromisoformat`` 可识别的形式
    （Python 3.10 的 fromisoformat 不接受 ``Z`` 后缀与空格分隔）。

    返回 naive datetime，与日志行时间戳（服务器本地时间，naive）同一语义，
    避免比较时抛 TypeError。若输入带时区信息，先 ``astimezone()`` 换算到
    服务器本地时区再剥掉 tzinfo——仅 ``replace(tzinfo=None)`` 会让 ``...Z``
    与本地时间直接比较，导致时间窗偏移一个时差。

    另：日志时间戳是服务器本地时间（logging 默认行为），因此不带时区的
    输入按本地时间原样保留，不做任何换算。
    """
    # 标准化：空格 → T，末尾 Z → +00:00（UTC 标记）
    normalized = value.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError(f"无法解析的时间格式: {value}")
    # 带时区 → 先换算到服务器本地，再统一为 naive
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _filter_by_time_range(
    content: str,
    since: datetime | None,
    until: datetime | None,
) -> str:
    """按时间范围过滤日志内容行。

    无时间戳的行（如多行 traceback 的续行）沿用**上一行**的时间戳，避免
    整段异常栈被丢弃；文件首行若就无时间戳，则按 until/since 的宽松侧
    （即"早于窗口但无信息量"时保留首个无时间戳块）处理。
    """
    if since is None and until is None:
        return content

    lines = content.splitlines(keepends=True)
    filtered = []
    last_ts: datetime | None = None
    for line in lines:
        ts = _parse_log_timestamp(line)
        if ts is not None:
            last_ts = ts
        else:
            # 续行：沿用上一行时间戳；若上一行也没有（文件开头），保留该行，
            # 由调用方按需兜底——直接丢弃会丢失 traceback 头部。
            ts = last_ts
        if ts is None:
            # 文件开头的无时间戳行：不参与时间判定，保留
            filtered.append(line)
            continue
        if since is not None and ts < since:
            continue
        if until is not None and ts > until:
            continue
        filtered.append(line)
    return "".join(filtered)


def _valid_section_names() -> set[str]:
    """返回所有合法段名集合（含多实例段前缀）。"""
    names: set[str] = set(SECTIONS.keys())
    names.update(multi_instance_prefixes())
    return names


def _is_valid_section(normalized: str) -> bool:
    """判断归一化后的段名是否合法（支持多实例段前缀匹配）。

    注意：多实例前缀本身（裸父段 ``notify-webhook``）**不**视为合法段。
    运行时以 ``startswith("notify-webhook-")`` 枚举实例
    （见 ``app/services/notification_service.py``），裸父段缺尾部横杠不匹配，
    写入后永远不会被读取——属于配置里永不生效的死段。
    """
    if normalized in _valid_section_names():
        # 命中多实例前缀但无后缀 → 死段，拒绝
        return normalized not in multi_instance_prefixes()
    for prefix in multi_instance_prefixes():
        if normalized.startswith(f"{prefix}-"):
            return True
    return False


def _mask_sensitive(config_data: dict[str, Any]) -> dict[str, Any]:
    """将敏感字段值替换为掩码字符串（就地修改并返回）。"""
    for section, fields in config_data.items():
        if not isinstance(fields, dict):
            continue
        for key in list(fields.keys()):
            if (section, key) in _EXTRA_SENSITIVE_FIELDS:
                fields[key] = _MASK
            elif is_sensitive_field(section, key):
                fields[key] = _MASK
    return config_data


async def _dispatch_read_log(
    log_file_path: str,
    level: str | None,
    search: str | None,
    limit: str,
) -> dict[str, Any]:
    """在线程中读取日志文件（可被子类/测试覆盖）。"""
    return await asyncio.to_thread(_read_log_file, log_file_path, level, search, limit)


async def get_logs(
    level: str | None = None,
    search: str | None = None,
    limit: int = 50,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """查询 BS 日志，支持级别/搜索/时间范围过滤。

    Args:
        level: 日志级别过滤（DEBUG/INFO/WARNING/ERROR），可选。
        search: 关键词搜索（大小写不敏感），可选。
        limit: 返回行数上限（1~10000），默认 50。
        since: ISO 格式起始时间，可选。
        until: ISO 格式结束时间，可选。

    Returns:
        {"status": "success", "data": {"content": ..., "stats": {...}}}
    """
    # scope 校验：get_logs 要求 read 权限
    _require_read_scope()

    # 验证 level
    if level is not None:
        level_upper = level.upper()
        if level_upper == "WARN":
            level_upper = "WARNING"
        if level_upper not in VALID_LEVELS:
            raise ToolError(
                f"无效的日志级别: {level}，有效值为: {', '.join(sorted(VALID_LEVELS))}"
            )
        level = level_upper

    # 限制 limit 范围
    limit = max(1, min(limit, 10000))

    # 解析时间范围
    since_dt = None
    until_dt = None

    if since is not None:
        try:
            since_dt = _parse_iso_datetime(since)
        except ValueError:
            raise ToolError(f"无效的 since 时间格式: {since}")

    if until is not None:
        try:
            until_dt = _parse_iso_datetime(until)
        except ValueError:
            raise ToolError(f"无效的 until 时间格式: {until}")

    if since_dt is not None and until_dt is not None and since_dt > until_dt:
        raise ToolError("since 时间必须早于或等于 until 时间")

    try:
        log_path = resolved_dev_log_file_path(config_manager)
        if log_path is None:
            return {
                "status": "success",
                "data": {
                    "content": "",
                    "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
                    "notice": (
                        "文件日志已被禁用（[dev] log_file 为空）。"
                        "请在 Web 配置页「高级配置 → 日志文件」填写路径后重试。"
                    ),
                },
            }

        log_file_path = os.fspath(log_path)

        if not os.path.exists(log_file_path):
            return {
                "status": "success",
                "data": {
                    "content": "",
                    "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
                    "notice": f"日志文件不存在: {log_file_path}",
                },
            }

        # 有时间条件时读全量：先按 limit 取尾部再过滤会吞掉窗口靠前的日志
        # （表现为"这段时间没有日志"的假象）。无时间条件时保持读尾部 N 行。
        has_time_range = since_dt is not None or until_dt is not None
        read_limit = "all" if has_time_range else str(limit)
        result = await _dispatch_read_log(log_file_path, level, search, read_limit)

        # 应用时间范围过滤
        if "content" in result:
            result["content"] = _filter_by_time_range(
                result["content"], since_dt, until_dt
            )
            # 全量读取后按 limit 截断尾部，避免返回整个文件
            if has_time_range:
                kept = result["content"].splitlines(keepends=True)
                if len(kept) > limit:
                    result["content"] = "".join(kept[-limit:])
            result["stats"]["lines"] = result["content"].count("\n")

        return {"status": "success", "data": result}
    except ToolError:
        raise
    except Exception as e:
        # 不泄露内部路径/异常细节
        raise ToolError("获取日志失败，请检查日志文件配置") from e


async def get_current_config() -> dict[str, Any]:
    """读取当前配置，敏感字段已脱敏。

    Returns:
        {"status": "success", "data": {section: {key: value, ...}, ...}}
    """
    # scope 校验：get_current_config 要求 read 权限
    _require_read_scope()

    data = config_manager.get_all_config()
    _mask_sensitive(data)
    return {"status": "success", "data": data}


async def update_config(
    section: str,
    key: str,
    value: Any,
) -> dict[str, Any]:
    """修改配置项，直接生效。

    写入判据是「改完之后用户还能不能自己救回来」，而非逐键白名单：

    - ① 改完用户无法自行恢复的键禁改，仅限登录通道本身
      （auth.enabled / auth.secret_key / auth.password），请到 Web 界面操作；
    - ② 改完用户还需做一步操作的键放行，但返回值带 notice 说明后续操作；
    - ③ 其余一律开放，等同用户在 Web 界面的编辑能力（含 llm.api_key、
      notify-* 凭据、auth.webhook_key 等重填即可恢复的键）。

    本工具是 upsert：段或键不存在时会自动创建。若本次写入的是新键，
    返回值会额外提示「疑似拼写错误，请用 get_current_config 复核」。

    Args:
        section: 配置段名（支持下划线，自动归一化为连字符）。
            多实例段须带实例后缀（如 notify-webhook-1）。
        key: 配置键名。
        value: 配置值。

    Returns:
        {"status": "success", "message": ..., "data": {...}}
        另可能含 "notice"：需用户后续操作，或本次为新增键的复核提醒。

    Raises:
        ToolError: 段名非法、多实例段缺后缀、键在①类禁改清单、值超长或无 write scope。
    """
    # scope 校验：update_config 要求 write 权限
    token = get_access_token()
    if token is None:
        raise ToolError(
            "未找到访问令牌，无法修改配置。请提供含 write 权限的 access token。"
        )
    scopes = getattr(token, "scopes", [])
    if "write" not in scopes:
        raise ToolError(
            f"权限不足：update_config 需要 write scope，当前 scope: {scopes}"
        )

    # 归一化段名：下划线 → 连字符
    normalized = section.replace("_", "-")

    # 多实例裸父段单独提示：它是 schema 登记的前缀，但缺少实例后缀
    if normalized in multi_instance_prefixes():
        raise ToolError(
            f"{normalized} 是多实例段前缀，须指定实例后缀（如 {normalized}-1），"
            f"否则运行时不会读取该段"
        )

    # 校验段名合法性
    if not _is_valid_section(normalized):
        valid_names = _valid_section_names()
        raise ToolError(
            f"未知配置段: {section}。合法段包括: {', '.join(sorted(valid_names))}"
        )

    # 禁改键判定：见 _MCP_WRITE_DENIED 的判据说明
    if (normalized, key) in _MCP_WRITE_DENIED:
        raise ToolError(
            f"配置项 {normalized}.{key} 不允许通过 MCP 修改，请到 Web 界面操作"
        )

    # 校验 value 长度上限（防止 INI 膨胀）：按 str(value) 长度计算
    _MAX_VALUE_LENGTH = 10000
    if len(str(value)) > _MAX_VALUE_LENGTH:
        raise ToolError(f"配置值长度超限: {len(str(value))} > {_MAX_VALUE_LENGTH}")

    # 写入前取快照：判断本次是「改已有键」还是「新增键」
    # （写后再查恒为 True，无法区分）
    is_new_key = not config_manager.get_config_parser().has_option(normalized, key)

    try:
        config_manager.set_config(normalized, key, value)
    except ValueError as e:
        # 底层格式校验失败（非法段名/键名等）应表现为规范的工具错误
        raise ToolError(f"配置写入失败: {e}") from e

    result = {
        "status": "success",
        "message": f"已更新配置: {normalized}.{key}",
        "data": {"section": normalized, "key": key},
    }

    # ② 类键：写入成功，但用户还需执行一步操作，必须主动告知而非让 Agent 自行臆测
    followup = _MCP_WRITE_FOLLOWUP.get((normalized, key))
    if followup:
        result["notice"] = followup

    # 本次为新增键：配置里出现陌生键通常是拼写错误，提醒 Agent 用读工具复核
    if is_new_key:
        result["notice"] = _with_new_key_hint(result.get("notice", ""), normalized, key)

    return result


def _with_new_key_hint(existing_notice: str, section: str, key: str) -> str:
    """在已有提示后追加「疑似新键，请复核」的提醒。"""
    hint = (
        f"注意：{section}.{key} 此前不存在，本次为新增配置项。"
        "若属拼写错误，请用 get_current_config 复核该段的实际键名"
    )
    return f"{existing_notice}\n{hint}" if existing_notice else hint
