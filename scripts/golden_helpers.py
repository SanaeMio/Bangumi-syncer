"""黄金用例集（golden cases）公共辅助

目标：给匹配管线的改造提供**可回归的基线**。这是**手动执行的开发工具**，
不是 CI 测试：改匹配逻辑前后各跑一次 ``scripts/golden_check.py`` 比对差异。

分两层：

- **L1 离线黄金集** `scripts/golden_data/bangumi_data_cases.json`
  自带精简过的 bangumi-data 子集（目标条目 + 易混淆干扰项），不依赖
  网络、不依赖 archive 归档库，任何时候都能跑。
  断言粒度：bangumi-data 层的 ``find_bangumi_id`` 结果必须与基线一致。

- **L2 全量管线回归集** `scripts/golden_data/matching_cases.json`
  真实 Archive 数据上的 240 条用例，记录完整管线（custom_mapping →
  archive → bangumi-data → api_search）的逐条基线。archive 数据不在
  仓库中，缺数据时 L2 自动跳过。

设计取舍：
- 基线的期望值 = **生成时的实际行为快照**（characterization），不是
  「应当正确」的断言。这样任何行为变化（变好或变坏）都会报出差异，
  强制人工 review —— 这正是基线该有的语义。
- 每条用例同时保留 ``oracle``（构造用例时的真实答案），用于输出命中率
  报告，方便 A/B 对比时判断改动是变好还是变坏。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from app.utils.bangumi_data import BangumiData

GOLDEN_DIR = Path(__file__).resolve().parent / "golden_data"
L1_CASES_PATH = GOLDEN_DIR / "bangumi_data_cases.json"
L2_CASES_PATH = GOLDEN_DIR / "matching_cases.json"


def slim_item(item: dict[str, Any]) -> dict[str, Any]:
    """精简 bangumi-data 条目，只保留匹配路径真正读取的字段

    全量 8829 条约 7.6MB，直接进仓库不现实。匹配逻辑（matching.py）只
    读 title / titleTranslate / begin / sites，其余字段（comment、
    broadcast、officialSite 等）对结果无影响，剔除后体积可降到 1/5。
    """
    out: dict[str, Any] = {"title": item.get("title", "")}
    translates = (item.get("titleTranslate") or {}).get("zh-Hans") or []
    if translates:
        out["titleTranslate"] = {"zh-Hans": list(translates)}
    for key in ("type", "lang", "begin", "end"):
        if item.get(key):
            out[key] = item[key]
    sites = [s for s in (item.get("sites") or []) if s.get("site") == "bangumi"]
    if sites:
        out["sites"] = sites
    return out


def build_offline_bangumi_data(items: list[dict[str, Any]]) -> BangumiData:
    """用给定条目构造一个完全离线的 BangumiData

    绕开 ``BangumiData.__init__``（它会读配置、检查缓存、联网下载、
    预加载 8829 条并建索引），改为手工填充匹配所需的字段：

    - ``use_cache=False`` 让 ``_ensure_fresh_data()`` 立即返回，
      杜绝任何下载与磁盘读取
    - ``_data_cache`` 非空让 ``_parse_data()`` 直接走内存缓存分支
    """
    bd = BangumiData.__new__(BangumiData)
    bd._data_cache = list(items)
    bd._cache_items = list(items)
    bd._title_index = {}
    bd._cache_tmdb_mapping = {}
    bd._cache_tmdb_begin = {}
    bd._build_lock = threading.Lock()
    bd.verbose_logging = False
    bd.use_cache = False
    bd._force_redownload = False
    bd._cache_hit_count = 0
    bd._cache_miss_count = 0
    bd._cache_timestamp = time.time()
    bd._build_title_index()
    return bd


def run_case(client: BangumiData, case: dict[str, Any]) -> dict[str, Any]:
    """跑一条用例，返回 (命中结果 + top5 候选)

    候选列表一并纳入基线是**刻意**的：只比对最终 subject_id 时，
    打分与排序的改动（阈值微调、权重变化）往往仍然命中同一个条目，
    闸门形同虚设；候选 + 分数能第一时间暴露这类变化。

    ``candidates_out`` 只在真正发生扫描时写入，精确索引命中的用例
    需要补一次 ``find_bangumi_candidates`` 才有可比对的候选。
    """
    inp = case["input"]
    out: list[dict] = []
    result = client.find_bangumi_id(
        title=inp["title"],
        ori_title=inp.get("ori_title"),
        release_date=inp.get("release_date"),
        season=inp.get("season", 1),
        media_type=inp.get("media_type", ""),
        candidates_out=out,
    )
    if not out:
        try:
            out = list(
                client.find_bangumi_candidates(
                    title=inp["title"],
                    ori_title=inp.get("ori_title"),
                    release_date=inp.get("release_date"),
                    limit=5,
                )
                or []
            )
        except Exception:  # 候选回传失败不影响主流程判定
            out = []
    sid, matched, date_hit = result if result else ("", "", False)
    return {
        "subject_id": str(sid),
        "matched_title": matched,
        "date_matched": bool(date_hit),
        "candidates": [
            {
                "id": str(c.get("id", "")),
                "name": c.get("name", ""),
                "score": round(float(c.get("score", 0.0)), 6),
            }
            for c in out[:5]
        ],
    }


def load_golden(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def hit_rate(cases: list[dict[str, Any]], key: str = "baseline") -> float:
    """按 key（baseline / oracle）统计 oracle 命中率"""
    if not cases:
        return 0.0
    ok = sum(
        1
        for c in cases
        if c[key].get("subject_id")
        and c[key]["subject_id"] == c["oracle"].get("subject_id")
    )
    return ok / len(cases)
