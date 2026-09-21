"""匹配契约层（P2）

把散落在各处的「约定」变成可执行、可校验的类型。

背景：PR 240/241/242/248 的 5 个缺陷没有一个是打分公式问题，全是语义契约问题——
约定只存在于注释和 docstring 里，没有任何类型强制它。最直接的证据是
``bangumi_archive/_store.py::_adapt_episode_row``：docstring 写「将 Archive
行适配为 BangumiApi 返回结构」，实现是 ``return row``。

本模块只做三件事：
- 定义统一形状（``SubjectRef`` / ``EpisodeRef``）
- 提供三方 adapter，把各源的原始数据归一到统一形状
- 提供候选构造与 margin 计算

**不改变任何匹配控制流**：判定顺序、阈值、终止条件一律不变。
契约层是 P3 裁决层的前提——裁决层消费的就是这里产出的候选。

三方数据形状（详见 C3）：

| 源 | 主键 | 原名 | 中文名 | 日期 |
|---|---|---|---|---|
| Archive | ``id`` | ``name`` | ``name_cn`` | ``date`` |
| Bangumi API | ``id`` | ``name`` | ``name_cn`` | ``date`` |
| bangumi-data | ``sites[site=bangumi].id`` | ``title`` | ``titleTranslate.zh-Hans[]`` | ``begin`` |
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.services.sync_service.match_trace import MatchCandidate

# ===== 候选来源（C4：任何策略命中都必须标注来源） =====
SOURCE_ARCHIVE = "archive"
SOURCE_API_SEARCH = "api_search"
SOURCE_BANGUMI_DATA = "bangumi_data"
SOURCE_CUSTOM_MAPPING = "custom_mapping"

# 集数类型（对齐 Bangumi API 的 episode type 定义）——
# canonical 定义已收拢至 bangumi_constants.EPISODE_TYPE_*，此处保留
# 项目内既有短名作为别名（避免调用点批量改名）
from app.utils.bangumi_constants import (  # noqa: E402
    EPISODE_TYPE_ED as EP_TYPE_ED,  # noqa: F401 — 对外别名再导出
    EPISODE_TYPE_NORMAL as EP_TYPE_NORMAL,
    EPISODE_TYPE_OP as EP_TYPE_OP,  # noqa: F401 — 对外别名再导出
    EPISODE_TYPE_SP as EP_TYPE_SP,  # noqa: F401 — 对外别名再导出
)


def _text(value: Any) -> str:
    """任意值转去空白字符串；None / 非字符串一律视为空

    248-A 的根因是 ``ori_title=" "`` 这类哨兵值：它既不是有效标题，
    又不等于 None，会顶掉 ``if title and … and not ori_title:`` 这条正确分支。
    所有跨源读取文本的地方统一走这里，避免同一文件出现两种标准。
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()


def _first_translate(item: dict[str, Any]) -> str:
    """取 bangumi-data 的首个简体中文译名"""
    tr = item.get("titleTranslate")
    if not isinstance(tr, dict):
        return ""
    zh = tr.get("zh-Hans")
    if isinstance(zh, list):
        for t in zh:
            s = _text(t)
            if s:
                return s
    return _text(zh) if isinstance(zh, str) else ""


def _bangumi_data_id(item: dict[str, Any]) -> str:
    """从 bangumi-data 的 sites 里取 bangumi.tv 的条目 ID"""
    sites = item.get("sites")
    if not isinstance(sites, list):
        return ""
    for site in sites:
        if isinstance(site, dict) and site.get("site") == "bangumi":
            sid = site.get("id")
            if sid:
                return _text(sid)
    return ""


@dataclass(frozen=True)
class SubjectRef:
    """统一条目引用（C3）

    无论候选来自本地归档、Bangumi API 还是 bangumi-data，
    上游消费方只看得到这个形状，不需要知道源数据的字段名。
    """

    subject_id: str
    name: str = ""  # 原名
    name_cn: str = ""  # 中文名
    air_date: str = ""
    platform: str = ""
    subject_type: int | None = None  # 2=动画 6=真人（bangumi-data 无此信息）
    source: str = ""
    raw: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @property
    def display_name(self) -> str:
        """展示名：优先中文名，缺失时回原名"""
        return self.name_cn or self.name

    # ===== 三方 adapter =====

    @classmethod
    def from_archive(cls, row: dict[str, Any]) -> SubjectRef:
        """本地归档 subject 行 → SubjectRef（形状与 API 一致）"""
        return cls(
            subject_id=_text(row.get("id")),
            name=_text(row.get("name")),
            name_cn=_text(row.get("name_cn")),
            air_date=_text(row.get("date")),
            platform=_text(row.get("platform")),
            subject_type=row.get("type") if isinstance(row.get("type"), int) else None,
            source=SOURCE_ARCHIVE,
            raw=row,
        )

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> SubjectRef:
        """Bangumi API 搜索结果 → SubjectRef"""
        return cls(
            subject_id=_text(row.get("id")),
            name=_text(row.get("name")),
            name_cn=_text(row.get("name_cn")),
            air_date=_text(row.get("date")),
            platform=_text(row.get("platform")),
            subject_type=row.get("type") if isinstance(row.get("type"), int) else None,
            source=SOURCE_API_SEARCH,
            raw=row,
        )

    @classmethod
    def from_bangumi_data(cls, item: dict[str, Any]) -> SubjectRef:
        """bangumi-data 条目 → SubjectRef

        bangumi-data 用 ``title`` / ``titleTranslate.zh-Hans`` / ``begin``，
        与另外两源完全不同。此前在 ``bangumi_data/matching.py`` 里现场做
        ``title``→``name`` 映射，新增任何消费点都要重新踩一遍。
        """
        return cls(
            subject_id=_bangumi_data_id(item),
            name=_text(item.get("title")),
            name_cn=_first_translate(item),
            air_date=_text(item.get("begin")),
            platform="",
            subject_type=None,
            source=SOURCE_BANGUMI_DATA,
            raw=item,
        )

    def to_dict(self) -> dict[str, Any]:
        """回到 Bangumi API 的条目形状（供需要 dict 的下游使用）"""
        out: dict[str, Any] = {
            "id": self.subject_id,
            "name": self.name,
            "name_cn": self.name_cn,
            "date": self.air_date,
            "platform": self.platform,
        }
        if self.subject_type is not None:
            out["type"] = self.subject_type
        return out


@dataclass(frozen=True)
class EpisodeRef:
    """统一集数引用（C2）

    三个字段语义必须显式，不能靠调用方各自理解：
    - ``sort``：全局序号，跨季定位用（Archive 只存这个）
    - ``ep``：本季集号，展示与标记用（API 有，Archive 无，需合成；
      ``None`` 表示无季内集号信息——Archive 多季合并时无法从 sort 推得季边界）
    - ``type``：正片 / SP / OP / ED

    242 的根因就是这三个字段没有契约：``_adapt_episode_row`` 声称做适配
    却原样返回，下游 ``episodes.py`` 里 20+ 处 ``.get("ep")`` 裸访问。
    """

    episode_id: str
    subject_id: str = ""
    sort: float = 0.0
    ep: int | None = None
    type: int = EP_TYPE_NORMAL
    name: str = ""
    name_cn: str = ""
    air_date: str = ""
    raw: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> EpisodeRef:
        """Bangumi API 章节 → EpisodeRef"""
        ep_raw = row.get("ep")
        return cls(
            episode_id=_text(row.get("id")),
            subject_id=_text(row.get("subject_id")),
            sort=_as_float(row.get("sort")),
            ep=_as_int(ep_raw) if ep_raw is not None else None,
            type=_as_int(row.get("type"), default=EP_TYPE_NORMAL),
            name=_text(row.get("name")),
            name_cn=_text(row.get("name_cn")),
            air_date=_text(row.get("airdate")),
            raw=row,
        )

    @classmethod
    def from_archive(cls, row: dict[str, Any]) -> EpisodeRef:
        """Archive 章节行 → EpisodeRef

        Archive 按全局连续 ``sort`` 存储，没有季内集号 ``ep``。``ep`` 缺失时
        **保持缺失**（``None``），不在这里合成——合成需要季内偏移，属上层
        ``_synthesize_ep_field`` 的职责；若盲目用 ``sort`` 兜底，会把「无季内
        话数」误写成数字，破坏下游 ``episodes.py`` 以 ``(ep or 0) > 0`` 判定
        季内话数、以及依据 sort 重置定位季边界的逻辑。
        """
        ep_raw = row.get("ep")
        return cls(
            episode_id=_text(row.get("id")),
            subject_id=_text(row.get("subject_id")),
            sort=_as_float(row.get("sort")),
            ep=_as_int(ep_raw) if ep_raw is not None else None,
            type=_as_int(row.get("type"), default=EP_TYPE_NORMAL),
            name=_text(row.get("name")),
            name_cn=_text(row.get("name_cn")),
            air_date=_text(row.get("airdate")),
            raw=row,
        )

    def to_api_dict(self) -> dict[str, Any]:
        """回到 Bangumi API 的章节形状

        只补缺失字段，不覆盖已有值：下游对原始数据字段的依赖很多，
        归一化的职责是「保证字段存在且类型正确」，而不是改写数据源。
        ``ep`` 为 None 时不写入（保持缺失），避免下游把它当数字用。
        """
        out = dict(self.raw) if isinstance(self.raw, dict) else {}
        out.setdefault("id", self.episode_id)
        out.setdefault("subject_id", self.subject_id)
        out.setdefault("sort", self.sort)
        out.setdefault("type", self.type)
        out.setdefault("name", self.name)
        out.setdefault("name_cn", self.name_cn)
        out.setdefault("airdate", self.air_date)
        if self.ep is not None:
            out.setdefault("ep", self.ep)
        # 已存在但类型不对（None 会让下游比较出错）的字段，用契约值覆盖
        for key, value in (
            ("sort", self.sort),
            ("ep", self.ep),
            ("type", self.type),
        ):
            if key in out and out[key] is None:
                if value is not None:
                    out[key] = value
        return out


def _as_float(value: Any, default: float = 0.0) -> float:
    """宽松转 float：API 与 Archive 的 sort 可能是 int / float / 数字字符串"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    """宽松转 int：ep 可能是 int / float / 数字字符串"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


# ===== 候选构造（C4） =====


def make_candidate(
    subject: SubjectRef,
    score: float = 0.0,
    *,
    media_type: str = "",
    aliases: Iterable[str] | None = None,
) -> MatchCandidate:
    """统一形状 → 候选条目"""
    return MatchCandidate(
        subject_id=subject.subject_id,
        name=subject.name,
        name_cn=subject.name_cn,
        score=round(float(score), 4),
        platform=subject.platform,
        air_date=subject.air_date,
        source=subject.source,
        media_type=media_type,
        infobox_aliases=list(aliases) if aliases else [],
    )


def candidates_from_rows(
    rows: Iterable[dict[str, Any]] | None,
    source: str,
    *,
    limit: int = 5,
    scorer: Any = None,
    score_ctx: Any = None,
) -> list[MatchCandidate]:
    """把某源的原始条目列表转成候选列表

    Args:
        rows: 原始条目 dict 列表（Archive / API / bangumi-data 形状）
        source: 候选来源，决定用哪个 adapter
        limit: 最多产出多少条
        scorer: 可选，``(row, subject) -> float`` 打分函数。
            不传时 score 取条目自带的 ``score`` 字段，没有则为 0.0。
        score_ctx: 打分失败时的兜底值来源（未使用，保留扩展点）

    Returns:
        候选列表，按 score 降序。数据源为空时返回空列表（不返回 None）。
    """
    if not rows:
        return []

    adapter = _ADAPTERS.get(source)
    if adapter is None:
        raise ValueError(f"未知的候选来源: {source!r}")

    out: list[MatchCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        subject = adapter(row)
        if not subject.subject_id:
            continue
        if scorer is not None:
            try:
                score = float(scorer(row))
            except Exception:
                score = 0.0
        else:
            score = _as_float(row.get("score"))
        out.append(make_candidate(subject, score))

    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]


_ADAPTERS: dict[str, Any] = {
    SOURCE_ARCHIVE: SubjectRef.from_archive,
    SOURCE_API_SEARCH: SubjectRef.from_api,
    SOURCE_BANGUMI_DATA: SubjectRef.from_bangumi_data,
}


def compute_margin(candidates: list[MatchCandidate] | None) -> float | None:
    """top1 − top2 的分差；无法评估时返回 None

    **三态语义**（与 score=0 严格区分，混为一谈会把「并列第一」误判成
    「没有候选信息」）：

    - ``None``：无候选信息（短路径盲信），不能用于任何置信判定
    - ``0.0``：并列第一，说明条目本身有歧义
    - ``>0``：top1 领先

    返回 None 的三种情况：候选为空、只有一条候选、分数全部缺失（如 archive
    短路当前不打分）。
    """
    if not candidates or len(candidates) < 2:
        return None
    scores = [c.score for c in candidates]
    if not any(scores):
        # 全部为 0 = 没有打分信息，不是「并列第一」
        return None
    top = max(scores)
    rest = [s for s in scores if s != top]
    if not rest:
        return 0.0
    return round(top - max(rest), 4)
