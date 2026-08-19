"""
配置管理模块
"""

import os
import platform
import threading
from configparser import ConfigParser
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Optional

from .config_schema import (
    all_env_overrides,
    non_account_bangumi_sections,
)
from .config_secret_crypto import (
    LLM_SECTION,
    decrypt_if_sensitive,
    encrypt_if_sensitive,
)
from .logging import logger
from .startup_info import startup_info

# 非多账号的 bangumi-* section：从 SectionMeta 注册表派生
# （bangumi-data / bangumi-mapping / bangumi-archive / bangumi-replay）
_BANGUMI_NON_ACCOUNT_SECTIONS: tuple[str, ...] = non_account_bangumi_sections()


def parse_media_server_username_value(raw: Optional[str]) -> list[str]:
    """解析 media_server_username 配置值（英文或中文逗号分隔）为去重前的用户名列表。"""
    if raw is None:
        return []
    s = str(raw).strip().replace("，", ",")
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


class ConfigManager:
    """配置管理器"""

    _lock = threading.Lock()

    def __init__(self) -> None:
        self.platform = platform.system()
        self.cwd = Path(__file__).parent.parent.parent

        # 配置文件路径
        self.config_paths = self._get_config_paths()
        self.active_config_path = self._find_active_config()
        # 首次运行：active 是 default 路径但文件不存在时，从 config.example.ini 自动复制
        self._ensure_default_config()

        # 配置缓存
        self._config_cache: Optional[ConfigParser] = None
        self._last_modified = 0

        # 配置变更追踪：版本号自增 + 变更监听回调。
        # 供各模块的进程级缓存（watching/poster/bangumi-data 等）在配置
        # 变更后统一失效，与 ``get_dev_http_snapshot`` + BangumiApi 实例
        # 缓存的思路对齐：读配置的模块负责订阅失效，而不是每次现取。
        self._config_version = 0
        self._change_listeners: list[Callable[[], None]] = []
        self._change_dirty = False

        # 初始化配置
        self._load_config()

        # 检查并执行配置迁移
        if self._needs_migration():
            self._migrate_webhook_config()
            self._migrate_email_config()
            # 重新加载配置
            self._load_config()

        # 立即输出启动信息（在模块导入时）
        startup_info.print_banner()
        startup_info.print_system_info(self.active_config_path)

    def _get_config_paths(self) -> dict[str, Path]:
        """获取可能的配置文件路径"""
        return {
            "env": os.environ.get("CONFIG_FILE"),
            "mounted": Path("/app/config/config.ini"),
            "dev": self.cwd / "config.dev.ini",
            "default": self.cwd / "config.ini",
        }

    def _find_active_config(self) -> Path:
        """查找活动的配置文件"""
        # 1. 环境变量指定的配置文件
        if self.config_paths["env"] and Path(self.config_paths["env"]).exists():
            return Path(self.config_paths["env"])

        # 2. Docker挂载的配置文件
        if self.config_paths["mounted"].exists():
            return self.config_paths["mounted"]

        # 3. 开发配置文件
        if self.config_paths["dev"].exists():
            return self.config_paths["dev"]

        # 4. 默认配置文件
        return self.config_paths["default"]

    def _ensure_default_config(self) -> None:
        """首次运行时从 config.example.ini 复制到 config.ini。

        仅在 active_config_path 指向 default 路径（即没有 env/mounted/dev 配置）
        且 default 文件不存在时触发，避免在测试或自定义配置环境下产生副作用。
        """
        default_path = self.config_paths["default"]
        # active 不是 default 路径时，说明用户通过 env/mounted/dev 指定了配置，无需复制
        if self.active_config_path != default_path:
            return
        # default 已存在，无需复制
        if default_path.exists():
            return
        example_path = self.cwd / "config.example.ini"
        if not example_path.exists():
            return
        try:
            import shutil

            shutil.copy2(str(example_path), str(default_path))
            # 模块导入阶段不便使用 logger，输出到 stdout 作为首次运行提示
            print(
                f"[config] 首次运行：已从 {example_path.name} 复制生成 {default_path.name}，"
                "请按需修改后重启。"
            )
        except Exception as e:
            print(f"[config] 自动复制 config.example.ini 失败: {e}")

    def _load_config(self) -> None:
        """加载配置文件"""
        config = ConfigParser()

        # 读取配置文件
        config.read(self.active_config_path, encoding="utf-8-sig")

        # 将旧版 sync.single_username 迁移到 bangumi.media_server_username
        self._migrate_sync_single_username_to_bangumi(config)

        # 通知配置段重命名迁移：webhook-N → notify-webhook-N, email-N → notify-email-N
        self._migrate_notification_section_names(config)

        # 应用环境变量覆盖
        self._apply_env_overrides(config)

        # 更新缓存
        self._config_cache = config
        self._last_modified = (
            self.active_config_path.stat().st_mtime
            if self.active_config_path.exists()
            else 0
        )

        # 版本自增并标记待触发变更回调（由锁外的 _fire_pending_changes 统一触发）
        # getattr 兜底：部分测试绕过 __init__ 构造实例（patch __init__）
        self._config_version = getattr(self, "_config_version", 0) + 1
        self._change_dirty = True

    def _apply_env_overrides(self, config: ConfigParser) -> None:
        """应用环境变量覆盖（映射来源：SectionMeta.env_overrides）"""
        env_overrides = all_env_overrides()

        for (section, option), env_var in env_overrides.items():
            env_value = os.environ.get(env_var)
            if env_value:
                if not config.has_section(section):
                    config.add_section(section)
                config.set(section, option, env_value)

    def _check_config_updated(self) -> bool:
        """检查配置文件是否已更新"""
        if not self.active_config_path.exists():
            return False

        current_mtime = self.active_config_path.stat().st_mtime
        if current_mtime > self._last_modified:
            return True

        return False

    def _get_config_parser_nolock(self) -> ConfigParser:
        """获取配置对象（内部调用，需已持有锁或单线程上下文）"""
        if self._check_config_updated():
            self._load_config()
        return self._config_cache

    def get_config_parser(self) -> ConfigParser:
        """获取配置对象（线程安全）。

        若检测到配置文件被外部修改，会重载配置并在锁外触发变更回调。
        """
        with self._lock:
            config = self._get_config_parser_nolock()
        self._fire_pending_changes()
        return config

    def reload_config(self) -> None:
        """重新加载配置（线程安全，重载完成后触发变更回调）"""
        with self._lock:
            self._load_config()
        self._fire_pending_changes()

    def reload(self) -> None:
        """重新加载配置（别名）"""
        self.reload_config()

    # ------------------------------------------------------------------
    # 配置变更回调（供模块级缓存订阅失效）
    # ------------------------------------------------------------------

    def get_config_version(self) -> int:
        """当前配置版本号（每次配置重载/保存自增）。

        可用于进程级缓存 key 或只读比较，取到不变量后无需再轮询 mtime。
        """
        with self._lock:
            return getattr(self, "_config_version", 0)

    def register_config_change_listener(self, callback: Callable[[], None]) -> None:
        """注册配置变更回调：配置重载或保存后触发，用于各模块缓存失效。

        回调在锁外执行且捕获异常（单个回调失败不影响其他回调），
        但回调内不要做耗时操作。
        """
        with self._lock:
            if callback not in self._change_listeners:
                self._change_listeners.append(callback)

    def unregister_config_change_listener(self, callback: Callable[[], None]) -> None:
        """注销配置变更回调。"""
        with self._lock:
            if callback in self._change_listeners:
                self._change_listeners.remove(callback)

    def _fire_pending_changes(self) -> None:
        """在锁外触发待执行的变更回调（幂等：无未消费变更时直接返回）。

        必须持有锁外的上下文调用，避免回调内部再次获取配置时死锁
        （threading.Lock 不可重入）。
        """
        if not getattr(self, "_change_dirty", False):
            return
        with self._lock:
            listeners = list(getattr(self, "_change_listeners", ()))
            self._change_dirty = False
        for cb in listeners:
            try:
                cb()
            except Exception as e:
                logger.warning(f"配置变更回调执行失败（不影响主流程）: {e}")

    def _get_master_secret(self, config: ConfigParser) -> str:
        """从配置中提取 master secret（不加锁，需已持有锁或外部调用）"""
        return str(config.get("auth", "secret_key", fallback="") or "")

    def get_section(
        self, section: str, fallback: dict[str, Any] = None
    ) -> dict[str, Any]:
        """获取配置段"""
        config = self.get_config_parser()
        if not config.has_section(section):
            return fallback or {}

        result = {}
        for key, value in config.items(section):
            # 尝试转换为适当的数据类型
            if value.lower() in ("true", "false"):
                result[key] = value.lower() == "true"
            elif value.isdigit():
                result[key] = int(value)
            else:
                result[key] = value

        master = self._get_master_secret(config)
        for k, v in list(result.items()):
            if isinstance(v, str):
                result[k] = decrypt_if_sensitive(section, k, v, master=master)

        return result

    def get_config(self, section: str, key: str, fallback: Any = None) -> Any:
        """获取配置值"""
        config = self.get_config_parser()
        if not config.has_section(section):
            return fallback

        if not config.has_option(section, key):
            return fallback

        value = config.get(section, key)

        # 尝试转换为适当的数据类型
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        elif value.isdigit():
            return int(value)
        else:
            master = self._get_master_secret(config)
            out = decrypt_if_sensitive(section, key, value, master=master)
            return out if isinstance(out, str) else value

    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """获取配置值（别名）"""
        return self.get_config(section, key, fallback)

    def set_config(self, section: str, key: str, value: Any) -> None:
        """设置配置值（线程安全）"""
        with self._lock:
            config = self._get_config_parser_nolock()
            if not config.has_section(section):
                config.add_section(section)

            master = self._get_master_secret(config)
            stored = encrypt_if_sensitive(section, key, str(value), master=master)
            config.set(section, key, stored)
            self._save_config(config)
        self._fire_pending_changes()

    def set(self, section: str, key: str, value: Any) -> None:
        """设置配置值（别名）"""
        self.set_config(section, key, value)

    def _save_config(self, config: ConfigParser) -> None:
        """保存配置文件（原子写入，需在锁内调用）"""
        tmp_path = self.active_config_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                config.write(f)
            os.replace(str(tmp_path), str(self.active_config_path))
        except OSError:
            # 写入失败时清理临时文件
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        # 更新缓存
        self._config_cache = config
        self._last_modified = self.active_config_path.stat().st_mtime

        # 保存即配置变更：版本自增并标记待触发变更回调
        self._config_version = getattr(self, "_config_version", 0) + 1
        self._change_dirty = True

    def get_bangumi_configs(self) -> dict[str, dict[str, Any]]:
        """获取所有 Bangumi 账号配置段（仅迁移用，DB 为唯一真相源）。

        遍历 INI 中 ``bangumi-*`` 段（排除系统功能段），返回有 username+access_token 的段。
        仅供 ``app.core.accounts._collect_ini_accounts`` 启动迁移使用，运行时请用
        ``app.core.accounts.list_bangumi_configs``。
        """
        config = self.get_config_parser()
        bangumi_configs = {}

        # 遍历所有配置段，查找多账号 bangumi-* 配置段（排除非账号的系统功能段）
        for section_name in config.sections():
            if section_name.startswith("bangumi-") and section_name not in (
                _BANGUMI_NON_ACCOUNT_SECTIONS
            ):
                section_config = self.get_section(section_name)
                if section_config.get("username") and section_config.get(
                    "access_token"
                ):
                    bangumi_configs[section_name] = section_config

        return bangumi_configs

    def get_dev_http_snapshot(self) -> dict[str, Any]:
        """读取 [dev] 段影响 HTTP 请求的字段快照

        返回 dict 含 script_proxy/ssl_verify/bgm_api_proxy/bgm_next_proxy 与
        ECH 相关字段（ech_mode/ech_doh_url/ech_doh_use_proxy/ech_hosts/ech_ech_config），
        ECH 默认值与其他层（config_schema / ech.py 常量）保持一致。
        """
        return {
            "script_proxy": self.get("dev", "script_proxy", fallback=""),
            "ssl_verify": self.get("dev", "ssl_verify", fallback=True),
            "bgm_api_proxy": self.get("dev", "bgm_api_proxy", fallback=""),
            "bgm_next_proxy": self.get("dev", "bgm_next_proxy", fallback=""),
            "ech_mode": self.get("dev", "ech_mode", fallback="off"),
            "ech_doh_url": self.get(
                "dev", "ech_doh_url", fallback="https://dns.alidns.com/resolve"
            ),
            "ech_doh_use_proxy": self.get("dev", "ech_doh_use_proxy", fallback=False),
            "ech_hosts": self.get(
                "dev", "ech_hosts", fallback="bgm.tv,chii.in,next.bgm.tv,lain.bgm.tv"
            ),
            "ech_ech_config": self.get("dev", "ech_ech_config", fallback=""),
        }

    def _migrate_sync_single_username_to_bangumi(self, config: ConfigParser) -> None:
        """将 [sync] single_username 迁移到 [bangumi] media_server_username 后删除旧键。"""
        if not config.has_section("sync") or not config.has_option(
            "sync", "single_username"
        ):
            return

        old_val = config.get("sync", "single_username", fallback="").strip()
        if not old_val:
            config.remove_option("sync", "single_username")
            self._save_config(config)
            return

        existing = ""
        if config.has_section("bangumi") and config.has_option(
            "bangumi", "media_server_username"
        ):
            existing = config.get(
                "bangumi", "media_server_username", fallback=""
            ).strip()

        if existing:
            config.remove_option("sync", "single_username")
            self._save_config(config)
            logger.info(
                "配置迁移：已删除废弃项 sync.single_username（bangumi.media_server_username 已有值）"
            )
            return

        if not config.has_section("bangumi"):
            config.add_section("bangumi")
        config.set("bangumi", "media_server_username", old_val)
        config.remove_option("sync", "single_username")
        self._save_config(config)
        logger.info(
            "配置迁移：已将 sync.single_username 写入 bangumi.media_server_username 并删除旧键"
        )

    def _migrate_notification_section_names(self, config: ConfigParser) -> None:
        """通知配置段重命名：webhook-N → notify-webhook-N, email-N → notify-email-N

        幂等：已重命名的配置不会再次迁移。仅迁移 section 名，字段不变。
        """
        renamed = False
        for section in list(config.sections()):
            # webhook-N → notify-webhook-N（跳过已是 notify- 前缀的）
            if section.startswith("webhook-") and not section.startswith(
                "notify-webhook-"
            ):
                suffix = section[len("webhook-") :]
                new_name = f"notify-webhook-{suffix}"
                items = dict(config.items(section))
                config.remove_section(section)
                config.add_section(new_name)
                for k, v in items.items():
                    config.set(new_name, k, v)
                renamed = True
            elif section.startswith("email-") and not section.startswith(
                "notify-email-"
            ):
                suffix = section[len("email-") :]
                new_name = f"notify-email-{suffix}"
                items = dict(config.items(section))
                config.remove_section(section)
                config.add_section(new_name)
                for k, v in items.items():
                    config.set(new_name, k, v)
                renamed = True
        if renamed:
            self._save_config(config)
            logger.info("配置迁移：通知段已重命名为 notify-webhook-N / notify-email-N")

    def get_trakt_config(self) -> dict[str, Any]:
        """获取 Trakt 配置"""
        config = self.get_config_parser()

        # 检查 trakt 节是否存在
        if not config.has_section("trakt"):
            return {}

        trakt_config = self.get_section("trakt")

        # 确保配置有默认值
        default_config = {
            "client_id": "",
            "client_secret": "",
            "redirect_uri": "http://localhost:8000/api/trakt/auth/callback",
            "default_sync_interval": "0 */6 * * *",
            "default_enabled": True,
        }

        # 合并配置，确保所有键都存在
        for key, default_value in default_config.items():
            if key not in trakt_config:
                trakt_config[key] = default_value
            elif key == "default_enabled":
                # 转换布尔值
                value = trakt_config[key]
                if isinstance(value, str):
                    trakt_config[key] = value.lower() in (
                        "true",
                        "1",
                        "yes",
                        "on",
                        "enabled",
                    )
                else:
                    trakt_config[key] = bool(value)

        return trakt_config

    def get_scheduler_config(self) -> dict[str, Any]:
        """获取调度器配置"""
        config = self.get_config_parser()

        # 检查 scheduler 节是否存在
        if not config.has_section("scheduler"):
            # 即使没有 scheduler 段，也返回 timezone 默认值（含 TZ 环境变量覆盖）
            tz = os.environ.get("TZ") or "Asia/Shanghai"
            return {"timezone": tz}

        scheduler_config = self.get_section("scheduler")

        # 确保配置有默认值并转换类型
        default_config = {
            "startup_delay": 30,
            "max_concurrent_syncs": 3,
            "job_timeout": 300,
            "max_retries": 3,
            "retry_delay": 60,
        }

        # 合并配置并转换类型
        result_config = {}
        for key, default_value in default_config.items():
            value = scheduler_config.get(key)
            if value is None or value == "":
                result_config[key] = default_value
            else:
                # 转换为整数
                try:
                    result_config[key] = int(value)
                except (ValueError, TypeError):
                    result_config[key] = default_value

        # 时区配置（字符串类型）：优先 config.ini，其次 TZ 环境变量，兜底 Asia/Shanghai
        tz = scheduler_config.get("timezone") or os.environ.get("TZ") or "Asia/Shanghai"
        result_config["timezone"] = tz.strip() or "Asia/Shanghai"

        return result_config

    def get_scheduler_timezone(self) -> str:
        """获取调度器配置的时区名（默认 Asia/Shanghai）

        供需要与 cron 调度保持同一"今日"边界的模块使用，
        避免服务器系统时区与配置时区不一致导致日期错位。
        """
        return self.get_scheduler_config().get("timezone", "Asia/Shanghai")

    def today_in_scheduler_tz(self) -> _date:
        """返回调度器时区下的今日 date

        服务器系统时区（Docker 默认 UTC）可能与 [scheduler] timezone 不一致，
        date.today() 取系统时区会导致跨时区场景下"今日"错位。
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz_name = self.get_scheduler_timezone()
        try:
            return datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            # 时区名无效等异常降级到系统本地日期
            return _date.today()

    def get_feiniu_config(self) -> dict[str, Any]:
        """飞牛 trimmedia 同步配置（默认关闭）"""
        defaults: dict[str, Any] = {
            "enabled": False,
            "db_path": "",
            "min_percent": 85,
            "user_filter": "all",
            "time_range": "all",
            "sync_interval": "*/15 * * * *",
            "limit": 100,
        }
        raw = self.get_section("feiniu", {})
        out: dict[str, Any] = {**defaults, **raw}
        ev = out.get("enabled", False)
        if isinstance(ev, str):
            out["enabled"] = ev.strip().lower() in ("true", "1", "yes", "on")
        else:
            out["enabled"] = bool(ev)

        try:
            out["min_percent"] = int(out.get("min_percent", 85))
        except (TypeError, ValueError):
            out["min_percent"] = 85
        try:
            out["limit"] = int(out.get("limit", 100))
        except (TypeError, ValueError):
            out["limit"] = 100

        out["db_path"] = str(out.get("db_path") or "").strip()
        out["user_filter"] = str(out.get("user_filter") or "all").strip() or "all"
        out["time_range"] = str(out.get("time_range") or "all").strip() or "all"
        out["sync_interval"] = str(
            out.get("sync_interval") or defaults["sync_interval"]
        ).strip()

        return out

    def get_llm_config(self) -> dict[str, Any]:
        """获取 LLM 全局配置（含默认值）。"""

        defaults: dict[str, Any] = {
            "provider": "openai_compat",
            "api_base": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
            "max_tokens": 2000,
            "temperature": 0.7,
            "timeout": 60,
            "thinking_level": "off",
            "retention_days": 365,
        }
        raw = self.get_section(LLM_SECTION, {})
        merged: dict[str, Any] = {**defaults, **raw}
        # 空字符串会覆盖默认值（{**defaults, **raw} 语义），对关键枚举字段兜底
        if not merged.get("provider"):
            merged["provider"] = "openai_compat"
        if not merged.get("thinking_level"):
            merged["thinking_level"] = "off"
        # 确保类型正确（使用 is not None 以允许 0 等 falsy 值）
        if merged.get("max_tokens") is not None:
            merged["max_tokens"] = int(merged["max_tokens"])
        if merged.get("temperature") is not None:
            merged["temperature"] = float(merged["temperature"])
        if merged.get("timeout") is not None:
            merged["timeout"] = int(merged["timeout"])
        return merged

    def get_fongmi_config(self) -> dict[str, Any]:
        """fongmi 局域网轮询同步配置（默认关闭）

        与 feiniu 驱动不同，fongmi 不依赖本地数据库，而是通过局域网 HTTP
        轮询设备的 /media 端点获取播放状态。
        """
        defaults: dict[str, Any] = {
            "enabled": False,
            "devices": "",
            "subnet": "",
            "auto_scan": True,
            "sync_interval": "*/3 * * * *",
            "min_percent": 80,
        }
        raw = self.get_section("fongmi", {})
        out: dict[str, Any] = {**defaults, **raw}

        ev = out.get("enabled", False)
        if isinstance(ev, str):
            out["enabled"] = ev.strip().lower() in ("true", "1", "yes", "on")
        else:
            out["enabled"] = bool(ev)

        av = out.get("auto_scan", True)
        if isinstance(av, str):
            out["auto_scan"] = av.strip().lower() in ("true", "1", "yes", "on")
        else:
            out["auto_scan"] = bool(av)

        try:
            out["min_percent"] = int(out.get("min_percent", 80))
        except (TypeError, ValueError):
            out["min_percent"] = 80

        out["devices"] = str(out.get("devices") or "").strip()
        out["subnet"] = str(out.get("subnet") or "").strip()
        out["sync_interval"] = str(
            out.get("sync_interval") or defaults["sync_interval"]
        ).strip()

        return out

    def get_episode_sync_limits(self) -> tuple[int, int]:
        """季/集同步上限，用于超长连载番（默认 season≤100、episode≤9999）。"""
        max_season = self.get("sync", "max_sync_season", fallback=100)
        max_episode = self.get("sync", "max_sync_episode", fallback=9999)
        try:
            max_season = int(max_season)
        except (TypeError, ValueError):
            max_season = 100
        try:
            max_episode = int(max_episode)
        except (TypeError, ValueError):
            max_episode = 9999
        return max_season, max_episode

    # ── summary 配置增删改查 ────────────────────────────────────────────

    _SUMMARY_FIELDS = (
        "enabled",
        "name",
        "cron",
        "lookback_days",
        "user_name",
        "system_prompt",
        "max_records",
    )

    def get_summary_configs(self) -> list[dict[str, Any]]:
        """获取所有 summary 配置节，按名称排序。"""
        configs: list[dict[str, Any]] = []
        config = self.get_config_parser()
        for section_name in config.sections():
            if section_name.startswith("summary-"):
                section_config = self.get_section(section_name)
                section_config["name"] = section_name[len("summary-") :]
                configs.append(section_config)
        configs.sort(key=lambda x: x.get("name", ""))
        return configs

    def save_summary_config(
        self, config_data: dict[str, Any], old_name: str = ""
    ) -> None:
        """创建或更新 [summary-{name}] 配置节。

        如果提供了 old_name（重命名场景），则先删除旧配置节。
        """
        with self._lock:
            config = self._get_config_parser_nolock()
            name = config_data.get("name") or old_name
            if not name:
                raise ValueError("save_summary_config: name 不能为空")
            section_name = f"summary-{name}"

            if old_name and old_name != name:
                old_section = f"summary-{old_name}"
                if config.has_section(old_section):
                    config.remove_section(old_section)

            if not config.has_section(section_name):
                config.add_section(section_name)

            for field in self._SUMMARY_FIELDS:
                if field in config_data:
                    config.set(section_name, field, str(config_data[field]))

            self._save_config(config)
        self._fire_pending_changes()

    def delete_summary_config(self, name: str) -> None:
        """删除 [summary-{name}] 配置节。"""
        with self._lock:
            config = self._get_config_parser_nolock()
            section_name = f"summary-{name}"
            if config.has_section(section_name):
                config.remove_section(section_name)
                self._save_config(config)
        self._fire_pending_changes()

    def rename_notification_type(self, old_type: str, new_type: str) -> int:
        """将 webhook/邮件配置中的通知类型从 old_type 替换为 new_type。

        用于追番总结任务改名时，自动更新已订阅该任务通知的 webhook/邮件配置，
        避免用户手动重新勾选。
        """
        updated = 0
        with self._lock:
            config = self._get_config_parser_nolock()
            for section in config.sections():
                if not (
                    section.startswith("notify-webhook-")
                    or section.startswith("notify-email-")
                ):
                    continue
                types_raw = config.get(section, "types", fallback="")
                if not types_raw or types_raw.strip() == "all":
                    continue
                type_list = [t.strip() for t in types_raw.split(",")]
                if old_type not in type_list:
                    continue
                type_list = [new_type if t == old_type else t for t in type_list]
                config.set(section, "types", ", ".join(type_list))
                updated += 1
            if updated:
                self._save_config(config)
        self._fire_pending_changes()
        return updated

    # ────────────────────────────────────────────────────────────────────

    def get_all_config(self) -> dict[str, dict[str, Any]]:
        """获取所有配置（Bangumi 账号由 DB 管理，不在此返回）"""
        config = self.get_config_parser()
        result = {}

        for section_name in config.sections():
            # 跳过 bangumi-* 账号段（已迁移到 DB，由 /api/bangumi/accounts 管理）
            if section_name.startswith("bangumi-") and section_name not in (
                _BANGUMI_NON_ACCOUNT_SECTIONS
            ):
                continue
            # 跳过遗留单用户段 [bangumi]（账号字段已迁移到 DB，
            # 避免 decrypt_api_config_payload 解密后明文返回 access_token）
            if section_name == "bangumi":
                continue
            # 统一键名格式：将连字符转换为下划线
            normalized_key = section_name.replace("-", "_")
            result[normalized_key] = self.get_section(section_name)

        return result

    def save_config(self) -> None:
        """保存配置"""
        config = self.get_config_parser()
        self._save_config(config)
        self._fire_pending_changes()

    def _needs_migration(self) -> bool:
        """检查是否需要执行配置迁移"""
        config = self.get_config_parser()

        # 检查是否存在旧的webhook配置
        has_old_webhook = config.has_option("notification", "webhook_url")

        # 检查是否已经存在新的webhook配置段
        has_new_webhook = any(
            section.startswith("notify-webhook-") for section in config.sections()
        )

        # 检查是否存在旧的邮件配置
        has_old_email = config.has_option("notification", "email_enabled")

        # 检查是否已经存在新的邮件配置段
        has_new_email = any(
            section.startswith("notify-email-") for section in config.sections()
        )

        # 如果存在旧配置且不存在新配置，则需要迁移
        return (has_old_webhook and not has_new_webhook) or (
            has_old_email and not has_new_email
        )

    def _migrate_webhook_config(self) -> None:
        """将旧的webhook配置迁移到新的多webhook结构"""
        config = self.get_config_parser()

        # 读取旧的webhook配置
        webhook_enabled = config.get(
            "notification", "webhook_enabled", fallback="False"
        )
        webhook_url = config.get("notification", "webhook_url", fallback="")
        webhook_method = config.get("notification", "webhook_method", fallback="POST")
        webhook_headers = config.get("notification", "webhook_headers", fallback="")
        webhook_template = config.get("notification", "webhook_template", fallback="")

        # 删除旧的webhook配置字段
        if config.has_option("notification", "webhook_enabled"):
            config.remove_option("notification", "webhook_enabled")
        if config.has_option("notification", "webhook_url"):
            config.remove_option("notification", "webhook_url")
        if config.has_option("notification", "webhook_method"):
            config.remove_option("notification", "webhook_method")
        if config.has_option("notification", "webhook_headers"):
            config.remove_option("notification", "webhook_headers")
        if config.has_option("notification", "webhook_template"):
            config.remove_option("notification", "webhook_template")

        if webhook_url and webhook_headers and webhook_template:
            # 创建新的webhook-1配置段
            if not config.has_section("notify-webhook-1"):
                config.add_section("notify-webhook-1")

            config.set("notify-webhook-1", "id", "1")
            config.set("notify-webhook-1", "enabled", webhook_enabled)
            config.set("notify-webhook-1", "url", webhook_url)
            config.set("notify-webhook-1", "method", webhook_method)
            config.set("notify-webhook-1", "headers", webhook_headers)
            config.set("notify-webhook-1", "template", webhook_template)

            # 迁移策略：只启用错误通知类型，保持原有行为
            config.set("notify-webhook-1", "types", "mark_failed")

            # 保存配置
            self._save_config(config)

            logger.info(
                "配置迁移完成：旧webhook配置已迁移到notify-webhook-1配置段（仅启用mark_failed类型）"
            )
        else:
            # 字段不完整，删除旧配置但不创建新配置
            self._save_config(config)
            logger.info("配置迁移：旧webhook配置字段不完整，已删除旧配置")

    def _migrate_email_config(self) -> None:
        """将旧的邮件配置迁移到新的多邮件结构"""
        config = self.get_config_parser()

        # 读取旧的邮件配置
        email_enabled = config.get("notification", "email_enabled", fallback="False")
        smtp_server = config.get("notification", "smtp_server", fallback="")
        smtp_port = config.get("notification", "smtp_port", fallback="587")
        smtp_username = config.get("notification", "smtp_username", fallback="")
        smtp_password = config.get("notification", "smtp_password", fallback="")
        smtp_use_tls = config.get("notification", "smtp_use_tls", fallback="True")
        email_from = config.get("notification", "email_from", fallback="")
        email_to = config.get("notification", "email_to", fallback="")
        email_subject = config.get("notification", "email_subject", fallback="")
        template = config.get("notification", "template", fallback="")

        # 删除旧的邮件配置字段
        if config.has_option("notification", "email_enabled"):
            config.remove_option("notification", "email_enabled")
        if config.has_option("notification", "smtp_server"):
            config.remove_option("notification", "smtp_server")
        if config.has_option("notification", "smtp_port"):
            config.remove_option("notification", "smtp_port")
        if config.has_option("notification", "smtp_username"):
            config.remove_option("notification", "smtp_username")
        if config.has_option("notification", "smtp_password"):
            config.remove_option("notification", "smtp_password")
        if config.has_option("notification", "smtp_use_tls"):
            config.remove_option("notification", "smtp_use_tls")
        if config.has_option("notification", "email_from"):
            config.remove_option("notification", "email_from")
        if config.has_option("notification", "email_to"):
            config.remove_option("notification", "email_to")
        if config.has_option("notification", "email_subject"):
            config.remove_option("notification", "email_subject")
        if config.has_option("notification", "template"):
            config.remove_option("notification", "template")
        # 清理旧字段名（如果存在）
        if config.has_option("notification", "email_template_file"):
            config.remove_option("notification", "email_template_file")

        # 删除notification配置空间
        if config.has_section("notification"):
            config.remove_section("notification")

        if smtp_server and smtp_username and smtp_password and email_from:
            # 创建新的email-1配置段
            if not config.has_section("notify-email-1"):
                config.add_section("notify-email-1")

            config.set("notify-email-1", "id", "1")
            config.set("notify-email-1", "enabled", email_enabled)
            config.set("notify-email-1", "smtp_server", smtp_server)
            config.set("notify-email-1", "smtp_port", smtp_port)
            config.set("notify-email-1", "smtp_username", smtp_username)
            config.set("notify-email-1", "smtp_password", smtp_password)
            config.set("notify-email-1", "smtp_use_tls", smtp_use_tls)
            config.set("notify-email-1", "email_from", email_from)
            config.set("notify-email-1", "email_to", email_to)
            config.set("notify-email-1", "email_subject", email_subject)
            config.set("notify-email-1", "template", template)

            # 迁移策略：只启用错误通知类型，保持原有行为
            config.set("notify-email-1", "types", "mark_failed")

            # 保存配置
            self._save_config(config)

            logger.info(
                "配置迁移完成：旧邮件配置已迁移到email-1配置段（仅启用mark_failed类型）"
            )
        else:
            # 字段不完整，删除旧配置但不创建新配置
            self._save_config(config)
            logger.info("配置迁移：旧邮件配置字段不完整，已删除旧配置")


# 全局配置实例
config_manager = ConfigManager()

# 可注入钩子：默认返回模块级单例；测试/DI 可通过 set_config_manager 替换。
# 注意：仅显式调用 get_config_manager() 的消费方会感知替换，
# 直接 ``from ..core.config import config_manager`` 的代码仍用默认单例。
_config_manager_override: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """获取全局配置管理器（优先返回被注入的实例）。"""
    global _config_manager_override
    return (
        _config_manager_override
        if _config_manager_override is not None
        else config_manager
    )


def set_config_manager(instance: ConfigManager) -> None:
    """替换配置管理器实例（测试/DI 注入）。"""
    global _config_manager_override
    _config_manager_override = instance


def reset_config_manager() -> None:
    """复位配置管理器，恢复默认模块级单例。"""
    global _config_manager_override
    _config_manager_override = None
