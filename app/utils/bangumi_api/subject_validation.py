"""subject_id 有效性校验（共享实现）

原实现是 ``SyncService._validate_subject_id`` 的私有方法，只有「确认待确认
候选」一条路径会调用。结果是：映射管理页手动添加/编辑映射时**不做任何校验**，
用户可以写入书籍/音乐/游戏条目的 ID，直到真正同步时才失败 —— 而那时错误
信息离「写错了 ID」已经很远。

本模块把校验逻辑抽出为共享函数，供两条写入路径共用，避免再出现
「一处校验、另一处不校验」的分叉。
"""

from __future__ import annotations

import json
from typing import Any

from ...core.logging import logger

#: Bangumi 条目类型：2=动画，6=三次元。同步只支持这两类。
SUBJECT_TYPE_ANIME = 2
SUBJECT_TYPE_REAL = 6


def _get_archive_shortcut():
    """延迟获取 archive 短路器

    刻意**不**在模块顶层 ``from ._archive_shortcut import archive_shortcut``：
    那样会把对象引用绑定到本模块，``patch`` 原模块属性将不再生效
    （测试里 ``patch("app.utils.bangumi_api._archive_shortcut.archive_shortcut")``
    会静默失效，导致用例互相污染）。每次经属性访问即可保持可打桩。
    """
    from . import _archive_shortcut

    return _archive_shortcut.archive_shortcut


def _get_accounts():
    """延迟导入账号模块

    账号层会 import 配置层，配置层在导入时即构建 ConfigManager 并打印启动
    信息；放在模块顶层会让本模块的导入带上这种副作用。测试中可用
    ``patch("...subject_validation._get_accounts")`` 替换。
    """
    from ...core import accounts

    return accounts


def _get_config_manager():
    """延迟导入配置管理器（原因同 ``_get_accounts``）"""
    from ...core.config import config_manager

    return config_manager


def _get_api_class():
    """延迟导入 BangumiApi

    它定义在包 ``__init__`` 中，而本模块是该包的子模块；顶层直接
    ``from . import BangumiApi`` 会形成循环导入。测试中可用
    ``patch("...subject_validation._get_api_class")`` 替换。
    """
    from . import BangumiApi

    return BangumiApi


def validate_subject_id(subject_id: Any) -> tuple[bool, str]:
    """校验 subject_id 是否有效：存在且类型为动画/三次元

    优先走 archive 短路（本地查询，<1ms），未命中降级到 Bangumi API。
    返回 ``(True, "")`` 或 ``(False, reason)``。

    校验失败场景：
    - subject_id 非数字
    - 条目不存在（API 404 或返回空）
    - 类型非动画/三次元（如书籍/音乐/游戏）

    **降级放行**（返回 True）的情形：无可用 Bangumi 账号、archive 与 API 均
    不可用。理由是「校验本身不应成为写入的阻塞项」—— 网络故障时把用户的
    编辑挡回去，比允许一个可能无效的 ID 更糟；无效 ID 会在同步时暴露。
    """
    try:
        sid = int(subject_id)
    except (TypeError, ValueError):
        return False, "ID 必须为纯数字"

    if sid <= 0:
        return False, "ID 必须为正整数"

    # 优先走 archive 短路（无网络开销）
    shortcut = _get_archive_shortcut()
    if shortcut.enabled:
        result = shortcut.try_get_subject(sid)
        if result.hit and isinstance(result.data, dict):
            stype = result.data.get("type")
            if stype in (SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL):
                return True, ""
            return False, f"条目类型为 {stype}，仅支持动画/三次元"
        # archive 未命中或不完整：降级到 API

    # 降级到 API：DB 为唯一真相源，取首选账号；无首选则取首个可用账号
    accounts = _get_accounts()

    cfg = accounts.get_primary_bangumi_config() or None
    if cfg is None:
        configs = accounts.list_bangumi_configs()
        if not configs:
            # 无可用配置时降级放行（不阻塞用户，映射写入后再由同步流程校验）
            logger.warning(f"subject_id={sid} 校验跳过：无可用 Bangumi 配置，降级放行")
            return True, ""
        cfg = next(iter(configs.values()))

    dev_snapshot = _get_config_manager().get_dev_http_snapshot()
    api = _get_api_class()(
        username=cfg["username"],
        access_token=cfg["access_token"],
        private=cfg.get("private", False),
        http_proxy=dev_snapshot["script_proxy"],
        ssl_verify=dev_snapshot["ssl_verify"],
        bgm_api_proxy=dev_snapshot["bgm_api_proxy"],
        bgm_next_proxy=dev_snapshot["bgm_next_proxy"],
        ech_mode=dev_snapshot["ech_mode"],
    )
    try:
        data = api.get_subject(sid)
    except Exception as e:
        logger.warning(f"subject_id={sid} API 校验异常：{e}，降级放行")
        return True, ""
    finally:
        try:
            api.close()
        except Exception:  # noqa: BLE001 — 关闭失败不影响校验结论
            pass

    if not isinstance(data, dict) or not data:
        return False, "条目不存在或 API 返回空"

    stype = data.get("type")
    if stype not in (SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL):
        return False, f"条目类型为 {stype}，仅支持动画/三次元"

    return True, ""


def collect_subject_ids(
    mappings: dict[str, Any], rules: list[Any] | None = None
) -> list[str]:
    """收集一份映射配置里出现的全部 subject_id（含 segments 与 rules 内的）。

    用于写入前批量校验。返回去重后的字符串 ID 列表（保序）。

    ``rules`` 必须一并传入：正则规则同样由用户填写 subject_id（添加/编辑
    规则表单、导入的 JSON 都会带），且规则命中的优先级同样很高。遗漏它们
    会导致「映射里的 ID 校验了、规则里的没校验」，正是这个共享模块要消除的
    分叉 —— 一本轻小说/音乐条目的 ID 会被原样接受，直到同步时才报错。
    """
    seen: list[str] = []

    def _add(raw: Any) -> None:
        if raw is None:
            return
        sid = str(raw).strip()
        if sid and sid not in seen:
            seen.append(sid)

    for entry in (mappings or {}).values():
        if isinstance(entry, dict):
            _add(entry.get("subject_id"))
            segs = entry.get("segments")
            if isinstance(segs, list):
                for seg in segs:
                    if isinstance(seg, dict):
                        _add(seg.get("subject_id"))
        else:
            _add(entry)

    for rule in rules or []:
        if isinstance(rule, dict):
            _add(rule.get("subject_id"))

    return seen


def collect_changed_subject_ids(
    mappings: dict[str, Any],
    rules: list[Any] | None,
    existing_mappings: dict[str, Any],
    existing_rules: list[Any] | None,
) -> list[str]:
    """收集**相对磁盘配置发生变化**的 subject_id，供写入前校验。

    为什么不是 :func:`collect_subject_ids`（全量）：前端每次保存（新增 / 编辑 /
    改规则 / 导入）都会把整份 mappings + rules 塞进 body，若对全量 ID 校验，
    每次保存都要为配置里每个 ID 打一轮 network —— archive 未命中时每个 ID 都
    新建 API 客户端同步 GET，映射一多就是一次扇出。

    更糟的是「坏 ID 绑架无关编辑」：配置里只要留着一个历史无效 ID（条目被删 /
    合并，或早期写入的书籍条目），之后**改别的地方**也会被 400 挡住，用户只能
    先删掉那条或清空全部配置才能继续，而这两条路本身也要经过校验。

    改为只校验新增或值发生变化的 ID：
    - 未改动的条目 → 跳过（它在磁盘上、之前已校验过或已被用户接受）；
    - 删除的条目 → 不在本次提交里，天然不会被收集；
    - 改坏的 ID → 仍会被拦住（这是校验存在的意义）。

    比对按「按标题取出的 JSON 等价」判断，简单字符串格式 ``{"番名": "123"}``
    与对象格式都在此列；条目标题新增 / 删除 / 内容变化都算变化。
    ``rules`` 无稳定键，按整体 JSON 逐条比对后取差集（新增或改动的规则）。
    """
    changed = collect_subject_ids(_changed_entries(mappings, existing_mappings))
    changed.extend(_collect_ids_from_rules(_changed_rules(rules, existing_rules)))
    # 去重保序：同一个 ID 可能同时出现在映射与规则里
    out: list[str] = []
    for sid in changed:
        if sid and sid not in out:
            out.append(sid)
    return out


def _canonical(value: Any) -> str:
    """把映射条目 / 规则序列化成可比较的规范字符串。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def _changed_entries(
    mappings: dict[str, Any], existing_mappings: dict[str, Any]
) -> dict[str, Any]:
    """本次提交里新增或内容变化的映射条目。"""
    out: dict[str, Any] = {}
    for title, entry in (mappings or {}).items():
        if title not in (existing_mappings or {}):
            out[title] = entry
            continue
        if _canonical(entry) != _canonical(existing_mappings[title]):
            out[title] = entry
    return out


def _changed_rules(
    rules: list[Any] | None, existing_rules: list[Any] | None
) -> list[Any]:
    """本次提交里新增或内容变化的规则。

    规则没有稳定主键（用户可改 pattern / subject_id / season / description），
    故按规范化后的**多重集合**做差：磁盘上已有的形态各消耗一个，剩下的是新增
    或改动过的规则。这样「编辑一条规则」只会校验那条规则的新 ID，而不是全部。
    """
    pool: list[str] = [_canonical(r) for r in (existing_rules or [])]
    out: list[Any] = []
    for rule in rules or []:
        key = _canonical(rule)
        if key in pool:
            pool.remove(key)  # 未改动：消耗掉磁盘上的对应项
            continue
        out.append(rule)
    return out


def _collect_ids_from_rules(rules: list[Any] | None) -> list[str]:
    """从规则列表里收集 subject_id（去重保序）。"""
    out: list[str] = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        sid = str(rule.get("subject_id") or "").strip()
        if sid and sid not in out:
            out.append(sid)
    return out
