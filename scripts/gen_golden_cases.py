"""生成匹配管线黄金用例集（golden cases）

用途
----
为后续改造（契约层 P2 / 裁决层 P3）提供**回归闸门**：改造前后各跑一次，
逐条比对，任何行为变化都必须被人工 review。

产出
----
- ``scripts/golden_data/bangumi_data_cases.json``  L1 离线黄金集
  自带精简后的 bangumi-data 子集，不依赖网络与 archive 归档库。
- ``scripts/golden_data/matching_cases.json``      L2 全量管线回归集
  真实 Archive 数据上的用例 + 完整管线逐条基线；archive 数据不在仓库中，
  缺数据时跳过。

运行
----
    uv run python scripts/gen_golden_cases.py --mode l1
    uv run python scripts/gen_golden_cases.py --mode l2
    uv run python scripts/gen_golden_cases.py --mode all

注意
----
- 采样必须**确定性**：用固定种子 + 按 bangumi id 排序的候选池，
  否则每次生成的用例集不同，A/B 数字没有可比性。
- 基线的期望值 = 生成时的实际行为快照，不是「应当正确」的断言。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# golden_helpers 与本脚本同目录（scripts/ 不是包，需显式加入搜索路径）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows 控制台默认 GBK，app 启动横幅含 emoji 会抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 注意：golden_helpers 会间接 import app.utils.bangumi_data（模块级单例，构造时
# 读配置、检查缓存）。L2 模式需要在 import 之前先把 CONFIG_FILE 指向临时配置，
# 因此这里**故意**不在顶层 import helpers，改由各模式函数内部按需导入。

DEFAULT_SEED = 20260908
DEFAULT_N = 33  # 每场景用例数（9 个场景 → 约 300 条）
DEFAULT_DISTRACTORS = 10  # 每个目标条目附带的易混淆干扰条目数

SCENARIOS: dict[str, str] = {
    "S1_原名精确": "媒体库推送 JP 原名 + 首播日期，期望命中自身",
    "S2_中文名精确": "媒体库推送中文名 + 首播日期，期望命中自身",
    "S3_季后缀": "原名 + ' 第二季' 且 season=2，考验季后缀剥离",
    "S4_无日期": "原名但无首播日期（年份消歧失效），期望命中自身",
    "S5_剧场版前缀剥离": "取「劇場版 X」条目，用剥离前缀后的 X 查询",
    "S7_短标题碰撞": "短标题（<=5 字）易被长标题包含，期望命中自身",
    "S8_模糊typo": "原名随机替换 1 字符，考验模糊兜底",
    "S9_全角半角": "原名 ASCII 转全角，考验归一化",
    "S10_同名多版本": "同一原名存在多个版本，用原名+最早年份查询",
    "S11_同名不同年份": "同名多版本中取**非最早**版本 + 其年份，考验日期消歧",
    "S12_日期漂移": "首播日期 +400 天（>180 天门槛），考验扫描兜底",
    "S13_无匹配负例": "随机构造的不存在标题，期望不命中（防阈值放宽导致误匹配）",
}

# =====================================================================
# 工具
# =====================================================================


def _bgm_id(item: dict) -> str:
    for s in item.get("sites") or []:
        if s.get("site") == "bangumi":
            return str(s["id"])
    return ""


def _begin(item: dict) -> str:
    return (item.get("begin") or "")[:10]


def _zh(item: dict) -> str:
    zh = (item.get("titleTranslate") or {}).get("zh-Hans") or []
    return zh[0] if zh else ""


def _media_type(item: dict) -> str:
    mapping = {"movie": "movie", "ova": "ova", "oad": "oad"}
    return mapping.get(item.get("type", ""), "episode")


def _to_fullwidth(s: str) -> str:
    return "".join(chr(ord(ch) + 0xFEE0) if 0x21 <= ord(ch) <= 0x7E else ch for ch in s)


def _typo(s: str, rnd: random.Random) -> str:
    """按标题字符集挑替换字符，保证 typo 与原文同语种"""
    if len(s) < 3:
        return s + "!"
    if re.search(r"[\u3040-\u30ff]", s):
        pool = "アイウエオカキクケコサシスセソ"
    elif re.search(r"[\u4e00-\u9fff]", s):
        pool = "的一是不了人我在有他这为之大来以个中上们"
    else:
        pool = "abcdefghijklmnopqrstuvwxyz"
    i = rnd.randrange(len(s))
    return s[:i] + rnd.choice(pool) + s[i + 1 :]


def _pick(pool: list[Any], want: int, rnd: random.Random) -> list[Any]:
    """确定性采样：候选池顺序固定，仅采样索引由种子决定"""
    if len(pool) <= want:
        return list(pool)
    return [pool[i] for i in sorted(rnd.sample(range(len(pool)), want))]


# =====================================================================
# L1：bangumi-data 离线黄金集
# =====================================================================


def build_l1_cases(items: list[dict], n: int, rnd: random.Random) -> list[dict]:
    """从 bangumi-data 自身构造用例（不依赖 archive）

    用例的「正确答案」来自条目自身：推送该条目的原名/中文名/变体，
    期望命中它自己。这样生成过程完全离线且可复现。
    """
    usable = [it for it in items if _bgm_id(it) and it.get("title") and _begin(it)]
    usable.sort(key=lambda it: int(_bgm_id(it) or 0))

    def mk(scenario: str, item: dict, title: str, alt_ids: list[str] = (), **over):
        payload = {
            "title": title,
            "ori_title": over.pop("ori_title", item.get("title", "")),
            "release_date": over.pop("release_date", _begin(item)),
            "season": over.pop("season", 1),
            "media_type": over.pop("media_type", _media_type(item)),
        }
        payload.update(over)
        return {
            "id": f"L1-{scenario}-{len(cases) + 1:03d}",
            "scenario": scenario,
            "desc": SCENARIOS[scenario],
            "input": payload,
            "oracle": {
                "subject_id": _bgm_id(item),
                "title": _zh(item) or item.get("title", ""),
                "alt_ids": [i for i in alt_ids if i and i != _bgm_id(item)],
            },
        }

    cases: list[dict] = []

    # S1 / S4 / S8：普通原名
    pool = _pick(usable, n * 3, rnd)
    for it in pool[:n]:
        cases.append(mk("S1_原名精确", it, it["title"]))
    for it in pool[n : n * 2]:
        cases.append(mk("S4_无日期", it, it["title"], release_date=""))
    for it in pool[n * 2 : n * 3]:
        cases.append(mk("S8_模糊typo", it, _typo(it["title"], rnd)))

    # S2：中文名
    zh_pool = [it for it in usable if _zh(it)]
    for it in _pick(zh_pool, n, rnd):
        cases.append(mk("S2_中文名精确", it, _zh(it)))

    # S3：季后缀
    for it in _pick(usable, n, rnd):
        cases.append(mk("S3_季后缀", it, f"{it['title']} 第二季", season=2))

    # S5：剧场版前缀剥离（推送剥离后的名字，剧场版本身与同名条目都算对）
    movie_re = re.compile(r"^(劇場版|剧场版|映画)\s*")
    movie_pool = [it for it in usable if movie_re.match(it.get("title", ""))]
    for it in _pick(movie_pool, n, rnd):
        core = movie_re.sub("", it["title"]).strip()
        if len(core) < 2:
            continue
        alt = [_bgm_id(x) for x in usable if x.get("title") == core or _zh(x) == core]
        cases.append(mk("S5_剧场版前缀剥离", it, core, alt_ids=alt))

    # S7：短标题碰撞
    short_pool = [it for it in usable if 2 <= len(it.get("title", "")) <= 5]
    for it in _pick(short_pool, n, rnd):
        cases.append(mk("S7_短标题碰撞", it, it["title"]))

    # S9：全角半角（只对含 ASCII 的标题有意义）
    ascii_pool = [it for it in usable if re.search(r"[0-9A-Za-z]", it["title"])]
    for it in _pick(ascii_pool, n, rnd):
        cases.append(mk("S9_全角半角", it, _to_fullwidth(it["title"])))

    # S10：同名多版本（取最早版本 + 年份，答案唯一性较弱）
    by_title: dict[str, list[dict]] = {}
    for it in usable:
        by_title.setdefault(it["title"], []).append(it)
    dup_pool = sorted(
        (v for v in by_title.values() if len(v) > 1),
        key=lambda v: int(_bgm_id(v[0]) or 0),
    )
    for group in _pick(dup_pool, n, rnd):
        target = sorted(group, key=lambda x: _begin(x))[0]
        year = _begin(target)[:4]
        if not year:
            continue
        cases.append(
            mk(
                "S10_同名多版本",
                target,
                f"{target['title']} {year}",
                alt_ids=[_bgm_id(x) for x in group],
            )
        )

    # S11：同名不同年份（**黄金集中区分度最高的场景**）
    #   故意取「非最早」的那个版本，若匹配忽略日期就会命中错误版本。
    #   答案唯一（alt_ids 留空），否则命中同组任意版本都算对，闸门失效。
    for group in _pick(dup_pool, n, rnd):
        ordered = sorted(group, key=lambda x: _begin(x))
        target = ordered[rnd.randrange(1, len(ordered))]
        year = _begin(target)[:4]
        if not year:
            continue
        cases.append(mk("S11_同名不同年份", target, f"{target['title']} {year}"))

    # S12：日期漂移（首播日期偏移 +400 天）
    #   精确索引的日期门槛是 180 天，偏移 400 天必然回退线性扫描，
    #   用来锁住「索引命中 vs 扫描兜底」这条分界。
    from datetime import datetime, timedelta

    for it in _pick(usable, n, rnd):
        try:
            base = datetime.strptime(_begin(it), "%Y-%m-%d")
        except ValueError:
            continue
        drifted = (base + timedelta(days=400)).strftime("%Y-%m-%d")
        cases.append(mk("S12_日期漂移", it, it["title"], release_date=drifted))

    # S13：负例（数据库中不存在的标题，期望不命中）
    #   防止「为提升召回而放宽阈值」导致凭空匹配；这类回归在正例集上
    #   永远看不出来，必须有负例兜底。
    kana = "アイウエオカキクケコサシスセソタチツテトナニヌネノ"
    existing = {it.get("title", "") for it in items}
    made: set[str] = set()
    while len(made) < n:
        token = "".join(rnd.choice(kana) for _ in range(rnd.randrange(6, 12)))
        if token in existing or token in made:
            continue
        made.add(token)
        cases.append(
            {
                "id": "",
                "scenario": "S13_无匹配负例",
                "desc": SCENARIOS["S13_无匹配负例"],
                "input": {
                    "title": token,
                    "ori_title": token,
                    "release_date": "2019-04-01",
                    "season": 1,
                    "media_type": "episode",
                },
                "oracle": {"subject_id": "", "title": "", "alt_ids": []},
            }
        )

    # 用例 id 重新编号，保证与 scenario 无关的稳定顺序
    for i, c in enumerate(cases, 1):
        c["id"] = f"L1-{c['scenario']}-{i:03d}"
    return cases


def build_fixture_items(
    items: list[dict], cases: list[dict], k: int
) -> tuple[list[dict], int]:
    """目标条目 + 每个目标的 top-K 易混淆条目，构成离线 fixture 子集

    为什么要带干扰项：黄金集的价值一半在于「不该被误吸走」。若子集中只有
    目标条目，任何算法退化都表现为命中正确，闸门形同虚设。
    """
    from golden_helpers import slim_item  # noqa: PLC0415
    from rapidfuzz import fuzz, process

    titles = [it.get("title", "") for it in items]

    chosen: dict[str, dict] = {}
    by_id = {_bgm_id(it): it for it in items if _bgm_id(it)}
    for c in cases:
        sid = c["oracle"]["subject_id"]
        if sid in by_id:
            chosen[sid] = by_id[sid]
        for alt in c["oracle"].get("alt_ids", []):
            if alt in by_id:
                chosen[alt] = by_id[alt]

    # 逐条取 top-K 相似条目作为干扰项（cdist 需要 numpy，这里避免引入依赖）
    for c in cases:
        matches = process.extract(
            c["input"]["title"],
            titles,
            scorer=fuzz.token_sort_ratio,
            limit=k * 3,
        )
        want = k
        for _title, _score, idx in matches:
            it = items[idx]
            sid = _bgm_id(it)
            if not sid or sid in chosen:
                continue
            chosen[sid] = it
            want -= 1
            if want <= 0:
                break

    fixture = [slim_item(it) for it in chosen.values()]
    return fixture, len(fixture)


def gen_l1(cache_path: Path, n: int, k: int, seed: int, out: Path | None = None) -> int:
    from golden_helpers import (  # noqa: PLC0415
        L1_CASES_PATH,
        build_offline_bangumi_data,
        run_case,
    )

    print(f"[L1] 读取 bangumi-data 缓存: {cache_path}")
    with open(cache_path, encoding="utf-8") as f:
        raw = json.load(f)
    items = raw["items"] if isinstance(raw, dict) else raw
    print(f"[L1] 全量条目 {len(items)} 条")

    rnd = random.Random(seed)
    cases = build_l1_cases(items, n, rnd)
    print(f"[L1] 构造用例 {len(cases)} 条")

    fixture, n_fix = build_fixture_items(items, cases, k)
    print(f"[L1] fixture 子集 {n_fix} 条（目标 + 干扰）")

    t0 = time.perf_counter()
    bd = build_offline_bangumi_data(fixture)
    print(f"[L1] 离线实例就绪，建索引耗时 {time.perf_counter() - t0:.2f}s")

    hit = 0
    t0 = time.perf_counter()
    for c in cases:
        c["baseline"] = run_case(bd, c)
        sid = c["baseline"]["subject_id"]
        answers = {c["oracle"]["subject_id"], *c["oracle"].get("alt_ids", [])}
        if sid and sid in answers:
            hit += 1
    elapsed = time.perf_counter() - t0
    positives = [c for c in cases if c["oracle"]["subject_id"]]
    print(f"[L1] 基线跑完 {len(cases)} 条，耗时 {elapsed:.1f}s")
    print(
        f"[L1] oracle 命中率 {hit}/{len(positives)} = "
        f"{hit / len(positives):.1%}（负例 {len(cases) - len(positives)} 条不计入）"
    )

    payload = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": seed,
        "source": {
            "kind": "bangumi-data",
            "total_items": len(items),
            "fixture_items": n_fix,
            "distractors_per_case": k,
        },
        "scenarios": SCENARIOS,
        "items": fixture,
        "cases": cases,
    }
    target = out or L1_CASES_PATH
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    size = target.stat().st_size / 1024
    print(f"[L1] 写入 {target}（{size:.0f} KB）")
    return 0


# =====================================================================
# L2：完整管线回归集（依赖本地 archive 数据）
# =====================================================================


def _prepare_l2_config(
    overrides: dict[str, dict[str, str]] | None = None,
) -> Path:
    """为 L2 准备临时配置，返回临时目录

    必须在 import 任何 app 模块**之前**调用：``app.utils.bangumi_data`` 等
    单例在 import 期就读取 CONFIG_FILE 完成构造，之后再改环境变量无效。

    这一点在测试里尤其关键 —— ``tests/conftest.py`` 会把 CONFIG_FILE 指向
    基于 config.example.ini 的临时配置（archive 被禁用、数据目录不同），
    子进程会继承它。若本函数晚于 import，L2 就会跑在与基线完全不同的
    配置上，产生一批假差异。
    """
    import configparser
    import shutil
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="bs_golden_"))
    cfg = tmpdir / "config.ini"
    shutil.copy2(ROOT / "config.ini", cfg)
    cp = configparser.ConfigParser()
    cp.read(cfg, encoding="utf-8")
    cp.set("bangumi-data", "cache_ttl_days", "36500")
    cp.set("bangumi-archive", "enabled", "false")
    cp.set("dev", "log_file", str(tmpdir / "golden.log"))
    cp.set("dev", "log_level", "WARNING")
    cp.set("dev", "debug", "false")
    # A/B 用：注入额外配置项（如 [matching] arbiter_enabled）
    for section, pairs in (overrides or {}).items():
        if not cp.has_section(section):
            cp.add_section(section)
        for key, value in pairs.items():
            cp.set(section, key, value)
    with open(cfg, "w", encoding="utf-8") as f:
        cp.write(f)
    os.environ["CONFIG_FILE"] = str(cfg)
    return tmpdir


def gen_l2(n: int, seed: int, out: Path | None = None) -> int:
    """用真实 Archive 数据跑完整匹配管线，记录逐条基线

    archive 归档库约 300MB 且不在仓库中，因此本函数只在本地有数据时可用；
    生成的 JSON 提交后，测试在没有数据的环境自动 skip。

    为避免污染用户配置与 log.txt，这里复制一份临时配置并把日志重定向到
    临时目录（由 ``_prepare_l2_config`` 在 import 前完成）。L2 测试通过
    **子进程复用本函数**来比对，保证生成与校验跑在完全相同的环境下。
    """
    from golden_helpers import L2_CASES_PATH  # noqa: PLC0415

    from app.models.sync import CustomItem
    from app.services.sync_service import SyncService
    from app.services.sync_service.match_trace import MatchTrace
    from app.utils.bangumi_api import BangumiApi
    from app.utils.bangumi_api._archive_shortcut import (
        ArchiveShortcut,
        archive_shortcut,
    )
    from app.utils.bangumi_archive._archive import bangumi_archive
    from app.utils.bangumi_archive._title_index import archive_title_index

    def _blocked(*_a, **_k):
        raise RuntimeError("NETWORK_BLOCKED")

    class _ProbeService(SyncService):
        _api = None

        def _get_bangumi_api_for_user(self, user_name: str):
            if _ProbeService._api is None:
                api = BangumiApi(
                    username="golden",
                    access_token="golden-token",
                    private=False,
                    http_proxy="",
                    ssl_verify=True,
                    bgm_api_proxy="",
                    bgm_next_proxy="",
                    ech_mode="off",
                )
                api._request_with_retry = _blocked  # type: ignore[assignment]

                def _empty_search(*args, **kwargs):
                    return []

                api.search = _empty_search  # type: ignore[assignment]
                _ProbeService._api = api
            return _ProbeService._api

    db_path = bangumi_archive.get_active_db_path()
    if not db_path or not Path(db_path).exists():
        print("[L2] 未找到 archive 归档库，跳过")
        return 1
    print(f"[L2] archive 库: {db_path}")

    if not archive_title_index._ensure_built():
        print("[L2] 归档索引构建失败")
        return 1

    def _always_on(self) -> None:
        self._enabled = True

    ArchiveShortcut.reload_config = _always_on  # type: ignore[assignment]
    archive_shortcut._enabled = True

    service = _ProbeService()
    rnd = random.Random(seed)
    cases = _build_archive_cases(Path(db_path), n, rnd, CustomItem)
    print(f"[L2] 构造用例 {len(cases)} 条")

    for c in cases:
        trace = MatchTrace()
        sid, _season_matched, failure = service._find_subject_id(c["item"], trace)
        c.pop("item")
        c["baseline"] = {
            "subject_id": str(sid) if sid else "",
            "match_method": trace.final_match_method or "",
            "match_detail": (trace.final_match_method_detail or "")[:200],
            "score": trace.final_score,
            "ambiguous": trace.is_ambiguous,
            "failure": (failure or "")[:200],
        }

    hit = sum(
        1 for c in cases if c["baseline"]["subject_id"] == c["oracle"]["subject_id"]
    )
    print(f"[L2] oracle 命中率 {hit}/{len(cases)} = {hit / len(cases):.1%}")

    payload = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": seed,
        "n": n,  # 每场景用例数，测试重跑时需用同一取值
        "source": {"kind": "bangumi-archive", "db": str(db_path)},
        "cases": cases,
    }
    target = out or L2_CASES_PATH
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[L2] 写入 {target}")
    return 0


def _build_archive_cases(
    db_path: Path, n: int, rnd: random.Random, CustomItem: Any
) -> list[dict]:
    """从 Archive subject 表反向构造媒体库形态的查询（确定性采样）"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    def qs(sql: str, params: tuple, want: int) -> list[sqlite3.Row]:
        # SQLite 的 RANDOM() 不可播种，改用 COUNT + 种子化 offset 保证可复现
        base = sql.replace("ORDER BY RANDOM() LIMIT ?", "")
        cnt = conn.execute(f"SELECT COUNT(*) FROM ({base})", params).fetchone()[0]
        if cnt == 0:
            return []
        off = 0 if cnt <= want else rnd.randrange(cnt - want + 1)
        return conn.execute(
            f"{base} ORDER BY 1 LIMIT ? OFFSET ?", (*params, want, off)
        ).fetchall()

    out: list[dict] = []

    def mk(scenario: str, row: sqlite3.Row, title: str, **over) -> dict:
        item = CustomItem(
            media_type=over.pop("media_type", "episode"),
            title=title,
            ori_title=over.pop("ori_title", None),
            season=over.pop("season", 1),
            episode=over.pop("episode", 1),
            release_date=over.pop("release_date", row["date"] or ""),
            user_name="golden_user",
            source="golden",
        )
        return {
            "id": f"L2-{scenario}-{len(out) + 1:03d}",
            "scenario": scenario,
            "input": {
                "title": item.title,
                "ori_title": item.ori_title,
                "release_date": item.release_date,
                "season": item.season,
                "media_type": item.media_type,
            },
            "oracle": {
                "subject_id": str(row["id"]),
                "title": row["name_cn"] or row["name"],
            },
            "item": item,
        }

    base_sql = (
        "SELECT id,name,name_cn,date FROM subject "
        "WHERE type=2 AND name<>'' AND date<>'' AND date IS NOT NULL "
        "ORDER BY RANDOM() LIMIT ?"
    )
    rows = qs(base_sql, (), n * 4)
    for i, r in enumerate(rows):
        if i < n:
            out.append(mk("S1_原名精确", r, r["name"], ori_title=r["name"]))
        elif i < n * 2:
            out.append(mk("S4_无日期", r, r["name"], release_date=""))
        elif i < n * 3:
            out.append(mk("S8_模糊typo", r, _typo(r["name"], rnd)))
        else:
            out.append(mk("S9_全角半角", r, _to_fullwidth(r["name"])))

    for r in qs(
        "SELECT id,name,name_cn,date FROM subject WHERE type=2 "
        "AND name_cn<>'' AND date<>'' AND date IS NOT NULL "
        "ORDER BY RANDOM() LIMIT ?",
        (),
        n,
    ):
        out.append(mk("S2_中文名精确", r, r["name_cn"], ori_title=r["name"]))

    for r in qs(base_sql, (), n):
        out.append(
            mk("S3_季后缀", r, f"{r['name']} 第二季", season=2, ori_title=r["name"])
        )

    for r in qs(
        "SELECT id,name,name_cn,date FROM subject WHERE type=6 "
        "AND name<>'' AND date<>'' ORDER BY RANDOM() LIMIT ?",
        (),
        n,
    ):
        out.append(
            mk(
                "S6_三次元",
                r,
                r["name"],
                media_type="real_action",
                ori_title=r["name"],
            )
        )

    for r in qs(
        "SELECT id,name,name_cn,date FROM subject WHERE type=2 "
        "AND length(name) BETWEEN 2 AND 5 AND date<>'' "
        "ORDER BY RANDOM() LIMIT ?",
        (),
        n,
    ):
        out.append(mk("S7_短标题碰撞", r, r["name"], ori_title=r["name"]))

    conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["l1", "l2", "all"], default="l1")
    ap.add_argument("--n", type=int, default=DEFAULT_N, help="每场景用例数")
    ap.add_argument("--distractors", type=int, default=DEFAULT_DISTRACTORS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--cache", default="./bangumi_data_cache.json")
    ap.add_argument(
        "--out", default=None, help="输出路径（默认写回 scripts/golden_data）"
    )
    ap.add_argument(
        "--arbiter",
        choices=["on", "off"],
        default=None,
        help="强制开关匹配裁决层（默认跟随配置文件，一般用于 A/B 对比）",
    )
    args = ap.parse_args()

    out = Path(args.out) if args.out else None
    rc = 0
    # L2 的临时配置必须在任何 app 模块 import 之前就绪（见 _prepare_l2_config 说明）
    if args.mode in ("l2", "all"):
        overrides = None
        if args.arbiter:
            overrides = {
                "matching": {
                    "arbiter_enabled": "true" if args.arbiter == "on" else "false"
                }
            }
        _prepare_l2_config(overrides)
    if args.mode in ("l1", "all"):
        rc |= gen_l1(Path(args.cache), args.n, args.distractors, args.seed, out)
    if args.mode in ("l2", "all"):
        rc |= gen_l2(args.n, args.seed, out)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
