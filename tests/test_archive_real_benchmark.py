"""Archive 真实数据拟真测试

从 data/archive/bangumi_archive_{a,b}.db（A/B 双库，默认 active）抽样真实 subject，
构造多场景查询变体，统计完整匹配率和匹配耗时。验证 FTS5 trigram 方案在 65 万+ 真实数据上的表现。

运行方式:
    uv run python tests/test_archive_real_benchmark.py
    uv run python tests/test_archive_real_benchmark.py --limit 20000   # 指定抽样上限
    uv run python tests/test_archive_real_benchmark.py --scenarios A,B  # 仅跑指定场景
    uv run python tests/test_archive_real_benchmark.py --db a         # 指定 A 库（默认 active）
    uv run python tests/test_archive_real_benchmark.py --db b         # 指定 B 库

前置条件:
    - data/archive/bangumi_archive_{a,b}.db 至少存在一个（已通过 Web 导入）
    - --db 选择的目标库必须存在；默认 active 会读取 bangumi_archive.active 指向的库
    - 若 subject_fts 表不存在，脚本会自动触发构建（首次约 7s）

场景说明:
    A 原名精确     - 用 subject.name 查询，期望命中自身
    B 中文名精确   - 用 subject.name_cn 查询，期望命中自身
    C 大小写差异   - name 转大写后查询（英文/罗马音标题）
    D 首尾空白     - name 前后加空格
    E 标点去除     - name 去除所有标点后查询
    F 全角半角     - name 中字母全角化后查询
    G 季后缀剥离   - name + " 第二季"，期望命中同名条目集合
    H 模糊 typo    - name 中随机替换 1 字符，测试模糊容错
    I 子串包含     - 取 name 前半段查询，测试子串命中
    J 续集图闭包   - 对有续集/前传关联的 subject 调 try_find_series_closure（双向 BFS 闭包，含分支）
    Q 同IP关系图闭包 - 对有「同作品」关系(相同系列/前传/续集/外传/改编/同世界观/劇場版/同系列)
                     关联的 subject 调 try_find_franchise_closure（覆盖更广的同IP闭包，含分支）
    K 集数查询     - 对有 episode 的 subject 查询 try_get_episodes
    L 单季动画     - type=2 且无续集关联的动画，标题查询应命中自身
    M 季后缀扩展   - 测试 S06E279 / Season 2 / 第2期 等多种季后缀格式剥离
    N 跨季续集链   - 多季动画（续集链长度 >= 2）的 try_find_sequel_chain
    O 长篇动画     - 集数 >= 100 的动画，try_get_episodes 应返回完整集列表
    P 电影本篇     - type=6（三次元/电影）的标题查询，验证电影场景匹配
    S 同名多义消歧 - 归一化同名、不同 date 的多版本条目（翻拍/多季），用
                    "原名+年份"查询，验证年份消歧优先返回同年版本（痛点 R1+R2）
    T CJK中文名缺口 - type=2 动画中 name_cn 为空的条目，用 JP 原名查询验证
                    匹配层本身可达；同时量化 CN 名缺失覆盖率（痛点 R3）

反向诊断（--reverse / --reverse-discover）：不抽样随机 subject，而是从真实
subject 分布挖掘"难匹配"人群，验证生产 try_search 在难例上的真实命中率，
定位掉点人群为匹配改进提供目标。人群 R1-R10：
    R1 跨语言可达   - name/name_cn 的 bigram Jaccard<0.1，用 name_cn 查自身
    R2 子串碰撞     - 3~8 字短标题被更长标题包含，用短原名查自身
    R3 罗马字标题   - 归一化后 ASCII 字母占比 >= 0.6，用原名查自身
    R4 特殊字符     - 含括号/书名号，去括号内容后查自身
    R5 年份后缀     - 含 19xx/20xx，去年份后查自身
    R6 别名可达     - 取 infobox 别名之一查自身
    R7 片假名变体   - 含长音/小书假名，去变体后查自身
    R8 分隔符变体   - 含中点/波浪分隔符，去分隔符后查自身
    R9 数字编号     - 含 第N/S#/#N，去编号后查自身
    R10 全角半角    - 含全角 ASCII，转半角后查自身
注：反向诊断应以不带 --bigram 的生产 try_search 结果为准（真实缺口）；
    --bigram 仅作乐观可达性估计，会高估命中率（见 --reverse 运行时的 ⚠️ 警告）。
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# UTF-8 输出（Windows 终端兼容）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from app.utils.bangumi_api._archive_shortcut import ArchiveShortcut  # noqa: E402
from app.utils.bangumi_archive._archive import bangumi_archive  # noqa: E402
from app.utils.bangumi_archive._fts_query import (  # noqa: E402
    _collect_candidates_fts_static as _collect_candidates_fts,
    _extract_alias_text,
    _normalize_key,
    _normalize_key_deep,
    _year_of,
)
from app.utils.bangumi_archive._title_index import archive_title_index  # noqa: E402
from app.utils.bangumi_archive._title_normalize import (  # noqa: E402
    _strip_season_episode_suffix,
    strip_bracket_content,
    strip_kana_variant,
    strip_numeral_variant,
    strip_year_suffix,
)

# ===== 配置 =====

DB_PATH = PROJECT_ROOT / "data" / "archive" / "bangumi_archive_b.db"
DB_PATH_A = PROJECT_ROOT / "data" / "archive" / "bangumi_archive_a.db"

# 抽样配置：按 subject.type 分层抽样
# type: 1=书籍 2=动画 3=音乐 4=游戏 6=三次元
# 注：Archive 导入时仅保留 type∈(2,6)，其他类型不会入库，故抽样仅覆盖这两类
SAMPLE_DIST = {
    2: 3000,  # 动画：同步流程主要类型
    6: 2000,  # 三次元：影视剧
}

# 默认抽样上限（--limit 参数可覆盖）
DEFAULT_LIMIT = 10000

# 每条 subject 生成的查询变体场景
# 注：I 场景（子串包含）已移除，媒体库不会推送半截标题，且子串查询触发
# FTS5 候选爆炸 + infobox 解析，性能不可接受，非真实场景。
SCENARIOS = (
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "J",
    "Q",
    "K",
    "L",
    "M",
    "N",
    "O",
    "P",
    "S",
    "T",
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R6",
    "R7",
    "R8",
    "R9",
    "R10",
)

# 反向场景：从真实数据分布挖掘难例（见 mine_reverse_cases）
REVERSE_SCENARIOS = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10")

# 模糊匹配阈值（与 try_search 默认一致）
FUZZY_THRESHOLD = 80

# 随机种子（可复现）
RANDOM_SEED = 20260729

# 续集关联类型（bangumi_constants.py: RELATION_ID_SEQUEL = 3）
RELATION_ID_SEQUEL = 3
# 前传关联类型（bangumi_constants.py: RELATION_ID_PREQUEL = 2）
RELATION_ID_PREQUEL = 2

# 同 IP / 同系列关系图闭包采用的关系类型集合（与 _store.FRANCHISE_RELATION_TYPES 一致）。
# 依据真实库 a.db subject_relation.relation_type 分布选定：
#   1 相同系列 / 2 前传 / 3 续集 / 4 外传 / 7 改编(同作者宇宙) /
#   8 同世界观 / 9 续集(系列) / 10 劇場版·总集编 / 12 同系列
# 剔除噪声边：5 角色出演 / 6 其他 / 11 其他(恶搞·活动) / 14 其他 / 99 其他·现实活动
FRANCHISE_RELATION_TYPES = (1, 2, 3, 4, 7, 8, 9, 10, 12)


# ===== 数据结构 =====


@dataclass
class SubjectSample:
    """抽样的 subject 记录"""

    id: int
    name: str
    name_cn: str
    type: int
    date: str


class CaseKind(Enum):
    """用例类型：区分标题查询、季查询、集查询"""

    TITLE = "title"  # 标题查询（调用 try_search）
    SEASON = "season"  # 季查询（调用 try_find_sequel_chain）
    EPISODE = "episode"  # 集查询（调用 try_get_episodes）


@dataclass
class TestCase:
    """单个测试用例"""

    scenario: str  # 场景标识 A/B/C...
    scenario_name: str  # 场景中文名
    kind: CaseKind  # 用例类型
    query: str  # 查询标题（TITLE 场景使用）
    subject_id: int  # 查询的 subject_id（SEASON/EPISODE 场景使用）
    expected_ids: set[int]  # 期望命中的 subject_id 集合（TITLE/SEASON 场景）
    expected_min_eps: int  # 期望的最小 episode 数（EPISODE 场景）
    subject_type: int  # 原始 subject 类型
    desc: str = ""  # 用例描述


@dataclass
class ScenarioStat:
    """场景统计"""

    total: int = 0
    hit: int = 0
    latencies: list[float] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return self.hit / self.total if self.total > 0 else 0.0

    @property
    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p99(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def avg(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0.0


# ===== 抽样 =====


def sample_subjects(db_path: Path, limit: int) -> list[SubjectSample]:
    """从 db 分层抽样 subject

    策略：按 SAMPLE_DIST 配置从各 type 抽样，总和不超过 limit。
    仅抽样有 name 且非空的条目。
    """
    if not db_path.exists():
        print(f"错误: 数据库文件不存在: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    samples: list[SubjectSample] = []
    random.seed(RANDOM_SEED)

    for subject_type, count in SAMPLE_DIST.items():
        # 按 limit 比例缩放
        scaled_count = int(count * limit / DEFAULT_LIMIT)
        scaled_count = max(scaled_count, min(limit, 100))  # 下限随 limit 缩

        rows = conn.execute(
            "SELECT id, name, name_cn, type, date FROM subject "
            "WHERE type = ? AND name IS NOT NULL AND name != '' "
            "ORDER BY RANDOM() LIMIT ?",
            (subject_type, scaled_count),
        ).fetchall()

        for row in rows:
            samples.append(
                SubjectSample(
                    id=row[0],
                    name=row[1],
                    name_cn=row[2] or "",
                    type=row[3],
                    date=row[4] or "",
                )
            )

    conn.close()
    random.shuffle(samples)
    return samples[:limit]


def sample_subjects_with_episodes(db_path: Path, limit: int) -> list[tuple[int, int]]:
    """抽样有 episode 的 subject，用于集数查询场景

    Returns:
        [(subject_id, episode_count), ...]
    """
    conn = sqlite3.connect(str(db_path))
    # 动画（type=2）优先，同步流程主要场景
    scaled = max(int(limit * 0.7), min(limit, 100))
    rows = conn.execute(
        "SELECT e.subject_id, COUNT(e.id) as ep_count "
        "FROM episode e JOIN subject s ON e.subject_id = s.id "
        "WHERE s.type = 2 AND e.type = 0 "  # type=0 是正片
        "GROUP BY e.subject_id ORDER BY RANDOM() LIMIT ?",
        (scaled,),
    ).fetchall()
    result = [(r[0], r[1]) for r in rows]
    conn.close()
    return result


def sample_subjects_with_sequels(
    db_path: Path, limit: int
) -> list[tuple[int, list[int], list[int]]]:
    """抽样有续集/前传关联的 subject，用于季查询场景

    从 subject_relation 表中查询 relation_type=3（续集）/2（前传）的关联，
    返回 (起始 subject_id, [续集列表], [前传列表]) 的列表。
    用于双向续集图闭包验证：expected = 续集 ∪ 前传。

    Returns:
        [(subject_id, [sequel_id, ...], [prequel_id, ...]), ...]
    """
    conn = sqlite3.connect(str(db_path))
    scaled = max(int(limit * 0.5), min(limit, 100))
    # 找出有续集/前传的 subject（即作为 relation_type 起点的）
    rows = conn.execute(
        "SELECT subject_id, related_subject_id, relation_type "
        "FROM subject_relation "
        "WHERE relation_type IN (?, ?) "
        "ORDER BY RANDOM() LIMIT ?",
        (RELATION_ID_SEQUEL, RELATION_ID_PREQUEL, scaled * 3),  # 多取一些用于去重
    ).fetchall()

    # 按 subject_id 聚合续集/前传列表
    subject_to_sequels: dict[int, list[int]] = {}
    subject_to_prequels: dict[int, list[int]] = {}
    for sid, rid, rtype in rows:
        if rtype == RELATION_ID_SEQUEL:
            subject_to_sequels.setdefault(sid, []).append(rid)
        elif rtype == RELATION_ID_PREQUEL:
            subject_to_prequels.setdefault(sid, []).append(rid)

    # 仅续集的起点
    seq_starts = set(subject_to_sequels)
    valid_ids = (
        {
            r[0]
            for r in conn.execute(
                f"SELECT id FROM subject WHERE id IN ({','.join('?' * len(seq_starts))})",
                list(seq_starts),
            ).fetchall()
        }
        if seq_starts
        else set()
    )
    # 仅前传的起点（用于验证双向闭包也能收回前传方向）
    pre_only = set(subject_to_prequels) - valid_ids
    if pre_only:
        valid_ids |= {
            r[0]
            for r in conn.execute(
                f"SELECT id FROM subject WHERE id IN ({','.join('?' * len(pre_only))})",
                list(pre_only),
            ).fetchall()
        }

    result = [
        (sid, subject_to_sequels.get(sid, []), subject_to_prequels.get(sid, []))
        for sid in (*subject_to_sequels, *subject_to_prequels)
        if sid in valid_ids
    ][:scaled]
    conn.close()
    return result


def sample_subjects_with_franchise(
    db_path: Path, limit: int
) -> list[tuple[int, list[int]]]:
    """抽样有「同作品」关系关联的 subject，用于场景 Q（同IP关系图闭包）

    从 subject_relation 中查询 relation_type ∈ FRANCHISE_RELATION_TYPES 的关联，
    返回 (起始 subject_id, [直接同IP关联 id 列表]) 的列表。

    期望（generate_franchise_cases）：生产 try_find_franchise_closure 的闭包应至少
    含这些直接关联（与场景 J 用 sequel∪prequel 做期望口径一致：验证闭包覆盖种子
    的直接关系边，多跳分支由 BFS 自然收回）。

    Returns:
        [(subject_id, [franchise_related_id, ...]), ...]
    """
    conn = sqlite3.connect(str(db_path))
    scaled = max(int(limit * 0.5), min(limit, 100))
    rows = conn.execute(
        f"SELECT subject_id, related_subject_id, relation_type "
        f"FROM subject_relation "
        f"WHERE relation_type IN ({','.join('?' * len(FRANCHISE_RELATION_TYPES))}) "
        f"ORDER BY RANDOM() LIMIT ?",
        (*FRANCHISE_RELATION_TYPES, scaled * 3),
    ).fetchall()

    subject_to_related: dict[int, list[int]] = {}
    for sid, rid, _ in rows:
        subject_to_related.setdefault(sid, []).append(rid)

    valid_ids = (
        {
            r[0]
            for r in conn.execute(
                f"SELECT id FROM subject WHERE id IN ({','.join('?' * len(subject_to_related))})",
                list(subject_to_related),
            ).fetchall()
        }
        if subject_to_related
        else set()
    )
    result = [
        (sid, subject_to_related.get(sid, []))
        for sid in subject_to_related
        if sid in valid_ids
    ][:scaled]
    conn.close()
    return result


def sample_single_season_anime(db_path: Path, limit: int) -> list[SubjectSample]:
    """抽样单季动画（type=2 且无续集关联），用于 L 场景

    策略：从 type=2 的动画中排除所有在 subject_relation 中作为
    relation_type=3 起点的 subject，剩余的视为单季动画。

    Returns:
        单季动画 SubjectSample 列表
    """
    conn = sqlite3.connect(str(db_path))
    scaled = max(int(limit * 0.5), min(limit, 100))
    rows = conn.execute(
        "SELECT s.id, s.name, s.name_cn, s.type, s.date "
        "FROM subject s "
        "WHERE s.type = 2 AND s.name IS NOT NULL AND s.name != '' "
        "AND s.id NOT IN ("
        "    SELECT DISTINCT subject_id FROM subject_relation WHERE relation_type = ?"
        ") "
        "ORDER BY RANDOM() LIMIT ?",
        (RELATION_ID_SEQUEL, scaled),
    ).fetchall()
    result = [
        SubjectSample(
            id=r[0], name=r[1], name_cn=r[2] or "", type=r[3], date=r[4] or ""
        )
        for r in rows
    ]
    conn.close()
    return result


def sample_long_anime_with_episodes(
    db_path: Path, limit: int, min_eps: int = 100
) -> list[tuple[int, int]]:
    """抽样长篇动画（集数 >= min_eps），用于 O 场景

    Returns:
        [(subject_id, episode_count), ...]
    """
    conn = sqlite3.connect(str(db_path))
    scaled = max(int(limit * 0.3), min(limit, 50))
    rows = conn.execute(
        "SELECT e.subject_id, COUNT(e.id) as ep_count "
        "FROM episode e JOIN subject s ON e.subject_id = s.id "
        "WHERE s.type = 2 AND e.type = 0 "
        "GROUP BY e.subject_id "
        "HAVING ep_count >= ? "
        "ORDER BY RANDOM() LIMIT ?",
        (min_eps, scaled),
    ).fetchall()
    result = [(r[0], r[1]) for r in rows]
    conn.close()
    return result


def sample_movies(db_path: Path, limit: int) -> list[SubjectSample]:
    """抽样电影类型 subject（type=6 三次元），用于 P 场景

    Returns:
        电影类型 SubjectSample 列表
    """
    conn = sqlite3.connect(str(db_path))
    scaled = max(int(limit * 0.3), min(limit, 50))
    rows = conn.execute(
        "SELECT id, name, name_cn, type, date FROM subject "
        "WHERE type = 6 AND name IS NOT NULL AND name != '' "
        "ORDER BY RANDOM() LIMIT ?",
        (scaled,),
    ).fetchall()
    result = [
        SubjectSample(
            id=r[0], name=r[1], name_cn=r[2] or "", type=r[3], date=r[4] or ""
        )
        for r in rows
    ]
    conn.close()
    return result


# ===== 用例生成 =====


def _remove_punct(text: str) -> str:
    """去除标点（与 _normalize_key 一致：保留字母数字）"""
    return "".join(c for c in text if c.isalnum())


def _to_fullwidth_ascii(text: str) -> str:
    """ASCII 字母数字转全角"""
    result = []
    for c in text:
        if "a" <= c <= "z":
            result.append(chr(ord(c) - ord("a") + ord("ａ")))
        elif "A" <= c <= "Z":
            result.append(chr(ord(c) - ord("A") + ord("Ａ")))
        elif "0" <= c <= "9":
            result.append(chr(ord(c) - ord("0") + ord("０")))
        else:
            result.append(c)
    return "".join(result)


def _introduce_typo(text: str) -> str:
    """在标题中随机替换 1 个字符（模拟 typo）

    仅对长度 >= 3 的标题操作，避免破坏过短标题。
    """
    if len(text) < 3:
        return text
    chars = list(text)
    idx = random.randint(0, len(chars) - 1)
    original = chars[idx]
    # 替换为相近字符（同类型替换）
    if original.isalpha() and original.isascii():
        # 英文字母替换为相邻字母
        base = ord("a") if original.islower() else ord("A")
        offset = (ord(original) - base + 1) % 26
        chars[idx] = chr(base + offset)
    elif "\u4e00" <= original <= "\u9fff":
        # 中文字符替换为形近字（简单策略：codepoint + 1）
        chars[idx] = chr(ord(original) + 1)
    else:
        # 其他字符不替换，换一个位置
        return text
    return "".join(chars)


def generate_cases(
    samples: list[SubjectSample], scenarios: tuple[str, ...]
) -> list[TestCase]:
    """为每条 subject 生成标题场景 A-I 的查询用例

    每条 subject 生成 scenarios 中指定的场景变体。
    expected_ids 是期望命中的 subject_id 集合（精确匹配场景期望命中自身，
    模糊/变体场景期望命中自身或同名条目集合）。
    """
    cases: list[TestCase] = []
    random.seed(RANDOM_SEED)

    # 预构建 name → id 集合的映射，用于季后缀场景的期望值
    name_to_ids: dict[str, set[int]] = {}
    for s in samples:
        key = s.name.strip()
        if key:
            name_to_ids.setdefault(key, set()).add(s.id)

    for s in samples:
        # 场景 A：原名精确
        if "A" in scenarios and s.name:
            cases.append(
                TestCase(
                    scenario="A",
                    scenario_name="原名精确",
                    kind=CaseKind.TITLE,
                    query=s.name,
                    subject_id=s.id,
                    expected_ids={s.id},
                    expected_min_eps=0,
                    subject_type=s.type,
                    desc=f"id={s.id} name={s.name!r}",
                )
            )

        # 场景 B：中文名精确
        if "B" in scenarios and s.name_cn:
            cases.append(
                TestCase(
                    scenario="B",
                    scenario_name="中文名精确",
                    kind=CaseKind.TITLE,
                    query=s.name_cn,
                    subject_id=s.id,
                    expected_ids={s.id},
                    expected_min_eps=0,
                    subject_type=s.type,
                    desc=f"id={s.id} name_cn={s.name_cn!r}",
                )
            )

        # 场景 C：大小写差异（仅对含 ASCII 字母的标题）
        if "C" in scenarios and s.name:
            upper = s.name.upper()
            if upper != s.name and any(c.isalpha() and c.isascii() for c in s.name):
                cases.append(
                    TestCase(
                        scenario="C",
                        scenario_name="大小写差异",
                        kind=CaseKind.TITLE,
                        query=upper,
                        subject_id=s.id,
                        expected_ids={s.id},
                        expected_min_eps=0,
                        subject_type=s.type,
                        desc=f"id={s.id} upper={upper!r}",
                    )
                )

        # 场景 D：首尾空白
        if "D" in scenarios and s.name:
            padded = f"  {s.name}  "
            cases.append(
                TestCase(
                    scenario="D",
                    scenario_name="首尾空白",
                    kind=CaseKind.TITLE,
                    query=padded,
                    subject_id=s.id,
                    expected_ids={s.id},
                    expected_min_eps=0,
                    subject_type=s.type,
                    desc=f"id={s.id}",
                )
            )

        # 场景 E：标点去除
        if "E" in scenarios and s.name:
            stripped = _remove_punct(s.name)
            if stripped and stripped != s.name:
                cases.append(
                    TestCase(
                        scenario="E",
                        scenario_name="标点去除",
                        kind=CaseKind.TITLE,
                        query=stripped,
                        subject_id=s.id,
                        expected_ids={s.id},
                        expected_min_eps=0,
                        subject_type=s.type,
                        desc=f"id={s.id} stripped={stripped!r}",
                    )
                )

        # 场景 F：全角半角（ASCII 字母数字转全角）
        if "F" in scenarios and s.name:
            fullwidth = _to_fullwidth_ascii(s.name)
            if fullwidth != s.name and any(c.isascii() and c.isalnum() for c in s.name):
                cases.append(
                    TestCase(
                        scenario="F",
                        scenario_name="全角半角",
                        kind=CaseKind.TITLE,
                        query=fullwidth,
                        subject_id=s.id,
                        expected_ids={s.id},
                        expected_min_eps=0,
                        subject_type=s.type,
                        desc=f"id={s.id}",
                    )
                )

        # 场景 G：季后缀剥离
        # 查询 "name 第二季"，期望命中所有同名条目（archive 中存的是第一季本体）
        if "G" in scenarios and s.name:
            _gname = s.name.strip()
            # 过滤退化样本：名已带季/part 后缀（双后缀伪例）或过短，
            # 与 M 场景同一思路，避免度量被不可能真实出现的输入稀释。
            if len(_gname) < 2 or _strip_season_episode_suffix(_gname) != _gname:
                continue
            query = f"{_gname} 第二季"
            expected = name_to_ids.get(_gname, {s.id})
            cases.append(
                TestCase(
                    scenario="G",
                    scenario_name="季后缀剥离",
                    kind=CaseKind.TITLE,
                    query=query,
                    subject_id=s.id,
                    expected_ids=expected,
                    expected_min_eps=0,
                    subject_type=s.type,
                    desc=f"id={s.id} query={query!r}",
                )
            )

        # 场景 H：模糊 typo
        if "H" in scenarios and s.name:
            typo = _introduce_typo(s.name)
            if typo != s.name:
                cases.append(
                    TestCase(
                        scenario="H",
                        scenario_name="模糊typo",
                        kind=CaseKind.TITLE,
                        query=typo,
                        subject_id=s.id,
                        expected_ids={s.id},
                        expected_min_eps=0,
                        subject_type=s.type,
                        desc=f"id={s.id} typo={typo!r}",
                    )
                )

        # 场景 I：子串包含（取前半段）
        if "I" in scenarios and s.name and len(s.name) >= 4:
            half = s.name[: len(s.name) // 2]
            if half and half != s.name:
                cases.append(
                    TestCase(
                        scenario="I",
                        scenario_name="子串包含",
                        kind=CaseKind.TITLE,
                        query=half,
                        subject_id=s.id,
                        expected_ids={s.id},
                        expected_min_eps=0,
                        subject_type=s.type,
                        desc=f"id={s.id} substring={half!r}",
                    )
                )

    return cases


def generate_season_cases(
    sequel_samples: list[tuple[int, list[int], list[int]]],
    scenarios: tuple[str, ...],
) -> list[TestCase]:
    """生成季查询场景 J：调用 try_find_series_closure 验证双向续集图闭包

    期望：对有关联的 subject 查询，闭包应包含 subject_relation 中记录的
    全部续集与前传 id（含分支型 IP 的兄弟续集/前传，单链 LIMIT 1 会丢失）。
    """
    if "J" not in scenarios:
        return []
    cases: list[TestCase] = []
    for subject_id, sequel_ids, prequel_ids in sequel_samples:
        expected = set(sequel_ids) | set(prequel_ids)
        if not expected:
            continue
            cases.append(
                TestCase(
                    scenario="J",
                    scenario_name="续集图闭包",
                    kind=CaseKind.SEASON,
                    query="",
                    subject_id=subject_id,
                    expected_ids=expected,
                    expected_min_eps=0,
                    subject_type=2,
                    desc=f"id={subject_id} sequels={sequel_ids} prequels={prequel_ids}",
                )
            )
    return cases


def generate_franchise_cases(
    franchise_samples: list[tuple[int, list[int]]],
    scenarios: tuple[str, ...],
) -> list[TestCase]:
    """生成场景 Q：调用 try_find_franchise_closure 验证同IP关系图闭包

    期望：对有关联的 subject 查询，闭包应至少包含 subject_relation 中记录的
    全部直接「同作品」关联 id（含分支型 IP 的兄弟续集/外传/相同系列等，
    单链 LIMIT 1 会丢失）。与场景 J 口径一致（验证闭包覆盖种子直接关系边）。
    """
    if "Q" not in scenarios:
        return []
    cases: list[TestCase] = []
    for subject_id, related_ids in franchise_samples:
        expected = set(related_ids)
        if not expected:
            continue
        cases.append(
            TestCase(
                scenario="Q",
                scenario_name="同IP关系图闭包",
                kind=CaseKind.SEASON,
                query="",
                subject_id=subject_id,
                expected_ids=expected,
                expected_min_eps=0,
                subject_type=2,
                desc=f"id={subject_id} franchise_related={related_ids}",
            )
        )
    return cases


def generate_same_name_cases(
    db_path: Path,
    scenarios: tuple[str, ...],
    limit: int = 1000,
) -> list[TestCase]:
    """生成场景 S（同名多义消歧 / 年份消歧）：验证「原名+年份」查询优先同年版本。

    反映痛点 R1+R2：归一化同名、不同 date 的多版本条目（翻拍/多季，如銀魂
    2006/2011/2017、涼宮ハルヒの憂鬱 2006/2009、Kanon 2002/2006）。匹配层
    find_subject_ids_by_title 在查询含年份时，先按裸标题匹配，再在多个同名
    候选中优先返回 date 年份相符者。每个版本生成一条用例：query=原名+年份，
    期望命中自身（自身应排在同名候选首位）。
    """
    if "S" not in scenarios:
        return []
    conn = sqlite3.connect(str(db_path))
    # type=2 动画，取 id/name/date
    rows = conn.execute(
        "SELECT id, name, date FROM subject WHERE type=2 "
        "AND name IS NOT NULL AND TRIM(name)<>''"
    ).fetchall()
    conn.close()

    from collections import defaultdict

    groups: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for sid, name, date in rows:
        nk = _normalize_key(name)
        if not nk:
            continue
        y = _year_of(date) if date else None
        groups[nk].append((sid, y, name))

    cases: list[TestCase] = []
    random.seed(RANDOM_SEED)
    # 仅保留「≥2 个不同非空年份」的同名组（真正多版本歧义）
    multi = [g for g in groups.values() if len({y for _, y, _ in g if y}) >= 2]
    random.shuffle(multi)
    for g in multi:
        if len(cases) >= limit:
            break
        for sid, y, name in g:
            if y is None:
                continue
            cases.append(
                TestCase(
                    scenario="S",
                    scenario_name="同名多义消歧",
                    kind=CaseKind.TITLE,
                    query=f"{name} {y}",
                    subject_id=sid,
                    expected_ids={sid},
                    expected_min_eps=0,
                    subject_type=2,
                    desc=f"id={sid} name={name!r} year={y}",
                )
            )
            if len(cases) >= limit:
                break
    return cases


def generate_cjk_gap_cases(
    db_path: Path,
    scenarios: tuple[str, ...],
    limit: int = 1000,
) -> list[TestCase]:
    """生成场景 T（CJK 中文名缺口）：量化 type=2 动画 name_cn 缺失覆盖率。

    反映痛点 R3：约 1/4 的 anime 无 name_cn，CN 查询天然失效。本场景用 JP 原名
    查询这些「缺中文名」条目，验证匹配层本身（JP 路径）可达——证明缺口是数据侧
    （infobox 未给中文名），而非匹配逻辑缺陷。同时打印覆盖率，供改进排期参考。

    用例：缺 name_cn 的 type=2 条目，query=JP 原名，期望命中自身。
    """
    if "T" not in scenarios:
        return []
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT id, name, name_cn FROM subject WHERE type=2").fetchall()
    conn.close()

    total = len(rows)
    missing = [r for r in rows if not (r[2] and r[2].strip())]
    if total:
        print(
            f"\n[T 覆盖率] type=2 动画共 {total} 条，"
            f"name_cn 为空 {len(missing)} 条 "
            f"({100 * len(missing) / total:.1f}%) —— CN 查询缺口"
        )

    cases: list[TestCase] = []
    random.seed(RANDOM_SEED)
    sample = random.sample(missing, min(limit, len(missing)))
    for sid, name, _ in sample:
        if not name or not name.strip():
            continue
        cases.append(
            TestCase(
                scenario="T",
                scenario_name="CJK中文名缺口",
                kind=CaseKind.TITLE,
                query=name,
                subject_id=sid,
                expected_ids={sid},
                expected_min_eps=0,
                subject_type=2,
                desc=f"id={sid} name={name!r} (无 name_cn)",
            )
        )
    return cases


def generate_episode_cases(
    episode_samples: list[tuple[int, int]],
    scenarios: tuple[str, ...],
) -> list[TestCase]:
    """生成集查询场景 K：调用 try_get_episodes 验证 episode 列表

    期望：对有 episode 的 subject 查询，应返回非空 episode 列表，
    且列表长度 >= 抽样时统计的 episode 数。
    """
    if "K" not in scenarios:
        return []
    cases: list[TestCase] = []
    for subject_id, ep_count in episode_samples:
        cases.append(
            TestCase(
                scenario="K",
                scenario_name="集数查询",
                kind=CaseKind.EPISODE,
                query="",
                subject_id=subject_id,
                expected_ids=set(),
                expected_min_eps=ep_count,
                subject_type=2,
                desc=f"id={subject_id} ep_count={ep_count}",
            )
        )
    return cases


def generate_single_season_cases(
    samples: list[SubjectSample], scenarios: tuple[str, ...]
) -> list[TestCase]:
    """生成场景 L：单季动画标题查询

    期望：单季动画（无续集关联）的标题查询应命中自身。
    与 A 场景区别：L 场景的样本经过"无续集关联"过滤，
    验证单季动画在 archive 中的可达性。
    """
    if "L" not in scenarios:
        return []
    cases: list[TestCase] = []
    for s in samples:
        cases.append(
            TestCase(
                scenario="L",
                scenario_name="单季动画",
                kind=CaseKind.TITLE,
                query=s.name,
                subject_id=s.id,
                expected_ids={s.id},
                expected_min_eps=0,
                subject_type=s.type,
                desc=f"id={s.id} name={s.name!r}",
            )
        )
    return cases


# 季后缀格式变体（覆盖 _SEASON_EPISODE_PATTERNS 的各模式）
_SEASON_SUFFIX_VARIANTS: tuple[str, ...] = (
    " 第二季",
    " 第2季",
    " 第二期",
    " 第2期",
    " Season 2",
    " season2",
    " 2nd Season",
    " S02",
    " S02E01",
    " II",
    " 第2部",
    " 上半",
    " 下半",
)


def generate_season_suffix_cases(
    samples: list[SubjectSample],
    scenarios: tuple[str, ...],
    max_subjects: int | None = None,
) -> list[TestCase]:
    """生成场景 M：季后缀剥离扩展

    对每个样本尝试多种季后缀格式，验证 _strip_season_episode_suffix
    能正确剥离各格式后命中核心标题。

    max_subjects：subject 抽样封顶（与全局 --limit 独立）。M 的本意是验证 13 种
    后缀格式都能被剥离，subject 身份无关紧要，故对 subject 数封顶即可大幅收敛
    用例量（用例数 = min(len(samples), max_subjects) × 13），后缀格式覆盖不变。
    None 或 <=0 表示不封顶（沿用传入的 samples 全量）。
    """
    if "M" not in scenarios:
        return []
    if max_subjects and max_subjects > 0:
        samples = samples[:max_subjects]
    cases: list[TestCase] = []
    for s in samples:
        # 过滤退化样本，保证 M 真实度量「季后缀剥离」而非下列伪例：
        #  - 样本名本身已带季/part 后缀（如「X 第三季」）：再拼后缀变成双后缀，
        #    真实媒体库不会发这种查询，且剥离后匹配目标模糊；
        #  - 样本名过短（<2 字符，如单汉字「愛」）：剥离后缀后剩超短串，
        #    无法可靠命中，度量的其实是短标题而非季后缀剥离。
        # 与 R 场景「唯一裸核过滤」同一思路：剔除不可能真实出现的输入。
        name = (s.name or "").strip()
        if len(name) < 2 or _strip_season_episode_suffix(name) != name:
            continue
        for suffix in _SEASON_SUFFIX_VARIANTS:
            query = f"{s.name}{suffix}"
            cases.append(
                TestCase(
                    scenario="M",
                    scenario_name="季后缀扩展",
                    kind=CaseKind.TITLE,
                    query=query,
                    subject_id=s.id,
                    expected_ids={s.id},
                    expected_min_eps=0,
                    subject_type=s.type,
                    desc=f"id={s.id} suffix={suffix!r}",
                )
            )
    return cases


def generate_cross_season_cases(
    sequel_samples: list[tuple[int, list[int], list[int]]],
    scenarios: tuple[str, ...],
    db_path: Path,
) -> list[TestCase]:
    """生成场景 N：跨季续集链

    直接定位续集链长度 >= 2 的多季动画（GROUP BY HAVING COUNT >= 2），
    验证 try_find_sequel_chain 能返回完整长链。

    注：随机抽样 subject_relation 几乎采不到「一起点多条续集」的多季动画
    （每条续集关系独立成行，随机窗口极少同时命中同一主体的 2 行），故此处
    改为直接按聚合条件定位，保证 N 场景样本有效。
    """
    if "N" not in scenarios:
        return []
    cases: list[TestCase] = []
    conn = sqlite3.connect(str(db_path))
    try:
        # 多季动画起点：续集关系数 >= 2
        cap = max(len(sequel_samples) or 100, 100)
        multi_starts = [
            r[0]
            for r in conn.execute(
                "SELECT subject_id FROM subject_relation "
                "WHERE relation_type = ? "
                "GROUP BY subject_id HAVING COUNT(*) >= 2 "
                "ORDER BY RANDOM() LIMIT ?",
                (RELATION_ID_SEQUEL, cap),
            ).fetchall()
        ]
        if not multi_starts:
            return cases
        placeholders = ",".join("?" * len(multi_starts))
        rows = conn.execute(
            f"SELECT subject_id, related_subject_id FROM subject_relation "
            f"WHERE relation_type = ? AND subject_id IN ({placeholders})",
            (RELATION_ID_SEQUEL, *multi_starts),
        ).fetchall()
        subj_to_seq: dict[int, list[int]] = {}
        for sid, rid in rows:
            subj_to_seq.setdefault(sid, []).append(rid)
        for subject_id in multi_starts:
            sequel_ids = subj_to_seq.get(subject_id, [])
            if len(sequel_ids) < 2:
                continue
            # 验证续集都在 subject 表中
            valid = conn.execute(
                f"SELECT id FROM subject WHERE id IN ({','.join('?' * len(sequel_ids))})",
                sequel_ids,
            ).fetchall()
            if len(valid) < 2:
                continue
            cases.append(
                TestCase(
                    scenario="N",
                    scenario_name="跨季续集链",
                    kind=CaseKind.SEASON,
                    query="",
                    subject_id=subject_id,
                    expected_ids=set(sequel_ids),
                    expected_min_eps=0,
                    subject_type=2,
                    desc=f"id={subject_id} chain_len={len(sequel_ids)}",
                )
            )
    finally:
        conn.close()
    return cases


def generate_long_anime_cases(
    long_samples: list[tuple[int, int]],
    scenarios: tuple[str, ...],
) -> list[TestCase]:
    """生成场景 O：长篇动画集数查询

    期望：长篇动画（集数 >= 100）的 try_get_episodes 应返回完整集列表，
    且数量 >= 抽样统计值。
    """
    if "O" not in scenarios:
        return []
    cases: list[TestCase] = []
    for subject_id, ep_count in long_samples:
        cases.append(
            TestCase(
                scenario="O",
                scenario_name="长篇动画",
                kind=CaseKind.EPISODE,
                query="",
                subject_id=subject_id,
                expected_ids=set(),
                expected_min_eps=ep_count,
                subject_type=2,
                desc=f"id={subject_id} ep_count={ep_count}",
            )
        )
    return cases


def generate_movie_cases(
    samples: list[SubjectSample], scenarios: tuple[str, ...]
) -> list[TestCase]:
    """生成场景 P：电影本篇标题查询

    期望：电影类型（type=6）的标题查询应命中自身。
    验证电影场景在 archive 中的匹配能力。
    """
    if "P" not in scenarios:
        return []
    cases: list[TestCase] = []
    for s in samples:
        cases.append(
            TestCase(
                scenario="P",
                scenario_name="电影本篇",
                kind=CaseKind.TITLE,
                query=s.name,
                subject_id=s.id,
                expected_ids={s.id},
                expected_min_eps=0,
                subject_type=s.type,
                desc=f"id={s.id} name={s.name!r}",
            )
        )
    return cases


# ===== 反向场景挖掘（用真实数据分布构造难例） =====

_BRACKET_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]|【[^】]*】|「[^」]*」|『[^』]*』")

# ===== 新增反向难例人群（R5-R10）的归一化/扰动辅助 =====
# R7 片假名变体：长音记号「ー」与小书假名（媒体常将其归一化掉）
_KATAKANA_LONG = "ー"
_SMALL_KANA = frozenset("ァィゥェォッャュョヮヵヶ")

# R8 分隔符变体：中点 / 波浪 / 破折号（媒体常省略或替换）
_SEP_CHARS = frozenset("・･·•〜~–—")

# R5 年份后缀：19xx / 20xx（含括号包裹）
_YEAR_RE = re.compile(r"[(（]?(?:19|20)\d{2}[)）]?")

# R9 数字编号：第N / S# / #N 等
_NUMERAL_RE = re.compile(r"第\s*[0-9一二三四五六七八九十百千万]+|S\d{1,2}|#\d+")


def _ascii_ratio(text: str) -> float:
    """归一化串中 ASCII 字母占比（R3 罗马字判定）"""
    if not text:
        return 0.0
    alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    return alpha / len(text)


def _has_kana_variant(text: str) -> bool:
    """含片假名长音或小书假名（R7）"""
    return _KATAKANA_LONG in text or any(c in _SMALL_KANA for c in text)


def _has_separator(text: str) -> bool:
    """含中点/波浪/破折类分隔符（R8）"""
    return any(c in _SEP_CHARS for c in text)


def _has_fullwidth_ascii(text: str) -> bool:
    """含全角 ASCII 字母/数字（R10）"""
    return any(0xFF01 <= ord(c) <= 0xFF5E for c in text)


def _strip_kana_variant(text: str) -> str:
    """去掉片假名长音与小书假名（模拟媒体归一化）"""
    return "".join(
        c for c in text if c != _KATAKANA_LONG and c not in _SMALL_KANA
    ).strip()


def _strip_separator(text: str) -> str:
    """去掉中点/波浪/破折类分隔符"""
    return "".join(c for c in text if c not in _SEP_CHARS).strip()


def _to_halfwidth_ascii(text: str) -> str:
    """全角 ASCII 变体 → 半角（R10；与场景 F 的半角→全角互补）"""
    out = []
    for c in text:
        o = ord(c)
        if 0xFF01 <= o <= 0xFF5E:  # ！-～ 全角 ASCII
            out.append(chr(o - 0xFEE0))
        elif c == "　":  # 全角空格
            out.append(" ")
        else:
            out.append(c)
    return "".join(out)


def _bigram_jaccard(a: str, b: str) -> float:
    """两个归一化串的字符 bigram Jaccard 相似度"""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    sa = {a[i : i + 2] for i in range(len(a) - 1)}
    sb = {b[i : i + 2] for i in range(len(b) - 1)}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _deep_stripped_query(name: str, scenario: str) -> str | None:
    """用与生产深度归一化一致的剥离函数生成「裸核」查询，并做可达性过滤。

    与生产 _normalize_key_deep 使用同一套 strip_* 函数，保证 bare 查询能被
    深度归一化命中（deep_key(query) == deep_key(title)）。同时要求裸核在
    原标题归一化串中连续出现（FTS 预筛可达）——中间装饰（如「X」夹在词间）
    会使裸核非连续，子串/trigram 永远无法命中，此类「不可能用例」直接排除，
    避免虚低匹配率。返回 None 表示应跳过该 subject。
    """
    if scenario == "R4":
        q = strip_bracket_content(name)
    elif scenario == "R5":
        q = strip_year_suffix(name)
    elif scenario == "R7":
        q = strip_kana_variant(name)
    elif scenario == "R9":
        q = strip_numeral_variant(name)
    else:
        return None
    q = q.strip()
    if not q or q == name:
        return None
    src_norm = _normalize_key(name)
    q_norm = _normalize_key(q)
    if not q_norm or q_norm not in src_norm:
        return None
    return q


def _build_deep_index(recs: list) -> dict[str, set[int]]:
    """构建「深度候选键 → subject 集合」索引，用于反向用例的确定性过滤。

    键集合覆盖生产匹配器实际会命中的全部归一化形态：
    name/name_cn 的轻量 + 深度归一。与生产 find_subject_ids_by_title 的召回
    口径一致（见 _fts_query._build_key_index）。

    注：刻意不纳入 infobox 别名键——别名键对反向用例「唯一裸核」判定的贡献
    极小且代价高（需逐条 parse_infobox）；R4/R5/R7/R9 的唯一裸核判定仅靠
    name/name_cn 已足够，避免为单次单场景迭代付出全库别名解析的 ~17s 开销。
    """
    index: dict[str, set[int]] = {}
    for r in recs:
        sid, name, name_cn, _stype, _date, _nk, _ck, _ak, aliases_raw = r
        keys: set[str] = set()
        if name:
            keys.add(_normalize_key(name))
            d = _normalize_key_deep(name)
            if d:
                keys.add(d)
        if name_cn:
            keys.add(_normalize_key(name_cn))
            d = _normalize_key_deep(name_cn)
            if d:
                keys.add(d)
        for k in keys:
            index.setdefault(k, set()).add(sid)
    return index


def _is_unique_core(q: str, source_sid: int, deep_index: dict[str, set[int]]) -> bool:
    """查询裸核 q 是否唯一确定 source_sid（无其它 subject 共享裸核）。

    反向用例若裸核被多个 subject 共享（如 'The Office' 命中第1~8季），
    从裸查询无法判定期望的特定变体——属基准方向固有歧义（真实媒体库发
    完整标题会精确命中），应从可判定用例中剔除，使 99% 指标反映真实
    可达性而非人为歧义。
    """
    cand: set[int] = set()
    for k in (_normalize_key(q), _normalize_key_deep(q)):
        if k:
            cand |= deep_index.get(k, set())
    return cand == {source_sid}


def _is_substring_collision(
    nk: str, sid: int, inv3: dict[str, list[tuple[int, str]]]
) -> bool:
    """nk 是否被另一条更长标题包含（共享 3gram 倒排粗筛 + 子串精判）"""
    for i in range(len(nk) - 2):
        for oid, onk in inv3.get(nk[i : i + 3], []):
            if oid != sid and len(onk) > len(nk) and nk in onk:
                return True
    return False


def _build_recs(
    db_path: Path, selected: tuple[str, ...] = REVERSE_SCENARIOS
) -> tuple[
    list[tuple[int, str, str, int, str, str, str, frozenset, tuple]],
    dict[str, list[tuple[int, str]]],
]:
    """读取全库 subject，构建反向挖掘用的规整记录与 3-gram 倒排

    recs 每项: (sid, name, name_cn, stype, date, nk, ck, ak, aliases_raw)
      nk/ck  = name/name_cn 归一化串
      ak     = 归一化别名集合（用于别名可达性 R6 的精确判定）
      aliases_raw = infobox 原始别名元组（用于构造 R6 查询）

    按需构建索引，把单场景迭代的固定开销从 ~20s 降到 ~1.5s：
    - inv3（3gram 倒排）仅在选中 R2 时构建（仅 R2 子串碰撞判定使用）；
    - infobox 别名解析（_extract_alias_text → parse_infobox）仅在选中 R6 时进行
      （仅 R6 别名可达用到 aliases_raw）；其余 8 个场景不需要别名，跳过逐条
      parse_infobox 可省约 17s（全库 5.6 万条规模实测）。
    """
    need_inv3 = "R2" in selected
    need_aliases = "R6" in selected
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT id, name, name_cn, type, date, infobox FROM subject"
    ).fetchall()
    conn.close()

    recs = []
    inv3: dict[str, list[tuple[int, str]]] = {}
    for sid, name, name_cn, stype, date, infobox in rows:
        nk = _normalize_key(name or "")
        ck = _normalize_key(name_cn or "")
        # 仅 R6 需要原始别名；其余场景（R4/R5/R7/R9 唯一裸核过滤）用
        # name/name_cn 的归一化键即足够，无需逐条 parse_infobox。
        if need_aliases and infobox:
            aliases_raw = tuple(a for a in _extract_alias_text(infobox).split() if a)
        else:
            aliases_raw = ()
        ak = frozenset(_normalize_key(a) for a in aliases_raw)
        recs.append(
            (sid, name or "", name_cn or "", stype, date or "", nk, ck, ak, aliases_raw)
        )
        if need_inv3 and len(nk) >= 3:
            for i in range(len(nk) - 2):
                inv3.setdefault(nk[i : i + 3], []).append((sid, nk))

    return recs, inv3


def _print_reverse_discovery(
    recs: list, inv3: dict[str, list[tuple[int, str]]]
) -> dict[str, int]:
    """全库扫描：统计各反向难例人群的真实规模与"需策略"扰动占比（不跑 try_search，秒级）

    直接回答"从真实数据挖掘真实的匹配难题"：用真实 subject 分布量化每个
    难例人群的体量，找出真正值得为匹配改进投入的目标。

    Returns:
        各人群（R1-R10）的规模字典，供调用方打印。
    """
    pools: dict[str, int] = {
        k: 0 for k in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10")
    }
    needs: dict[str, int] = dict(pools)  # 需策略（扰动后≠原串，或结构性难例）

    for r in recs:
        sid, name, name_cn, stype, date, nk, ck, ak, aliases_raw = r
        # R1 跨语言：name/name_cn 几乎不重叠
        if ck and _bigram_jaccard(nk, ck) < 0.1:
            pools["R1"] += 1
            needs["R1"] += 1
        # R2 子串碰撞：短标题被更长标题包含
        if 3 <= len(nk) <= 8:
            if _is_substring_collision(nk, sid, inv3):
                pools["R2"] += 1
                needs["R2"] += 1
        # R3 罗马字标题
        if len(nk) >= 3 and _ascii_ratio(nk) >= 0.6:
            pools["R3"] += 1
            needs["R3"] += 1
        # R4 特殊字符（去括号后≠原串才算扰动）
        if any(ch in name for ch in "()[]【】「」『』"):
            pools["R4"] += 1
            stripped = _BRACKET_RE.sub("", name).strip()
            if stripped and stripped != name:
                needs["R4"] += 1
        # R5 年份后缀
        if _YEAR_RE.search(name):
            pools["R5"] += 1
            stripped = _YEAR_RE.sub("", name).strip()
            if stripped and stripped != name:
                needs["R5"] += 1
        # R6 别名可达
        if ak:
            pools["R6"] += 1
            needs["R6"] += 1
        # R7 片假名变体
        if _has_kana_variant(name):
            pools["R7"] += 1
            stripped = _strip_kana_variant(name)
            if stripped and stripped != name:
                needs["R7"] += 1
        # R8 分隔符变体
        if _has_separator(name):
            pools["R8"] += 1
            stripped = _strip_separator(name)
            if stripped and stripped != name:
                needs["R8"] += 1
        # R9 数字编号
        if _NUMERAL_RE.search(name):
            pools["R9"] += 1
            stripped = _NUMERAL_RE.sub("", name).strip()
            if stripped and stripped != name:
                needs["R9"] += 1
        # R10 全角半角
        if _has_fullwidth_ascii(name):
            pools["R10"] += 1
            hw = _to_halfwidth_ascii(name)
            if hw != name:
                needs["R10"] += 1

    descs = {
        "R1": "跨语言(name/name_cn Jaccard<0.1)",
        "R2": "短标题被更长标题包含(子串碰撞)",
        "R3": "罗马字/英文标题(ASCII>=0.6)",
        "R4": "含括号/书名号标题",
        "R5": "含年份/编号(19xx/20xx)",
        "R6": "含 infobox 别名",
        "R7": "含片假名长音/小书假名",
        "R8": "含中点/波浪分隔符",
        "R9": "含数字编号(第N/S#/#N)",
        "R10": "含全角ASCII字母",
    }
    print("\n" + "=" * 72)
    print(f"反向难例人群 · 全库真实规模（subject 总数 {len(recs)}，秒级扫描）")
    print("=" * 72)
    print(f"  {'人群':<6}{'规模':>10}{'需策略':>10}  说明")
    print("-" * 72)
    for k in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"):
        p, n = pools[k], needs[k]
        print(f"  {k:<6}{p:>10}{n:>10}  {descs[k]}")

    return pools


def mine_reverse_cases(
    db_path: Path,
    limit: int,
    selected: tuple[str, ...],
    discover: bool = False,
) -> list[TestCase]:
    """反向场景：从真实数据分布挖掘难例，构造自然查询用例。

    不抽样随机 subject，而是按分布特征筛选真实"难匹配"人群，验证生产
    try_search 在难例上的真实命中率（定位掉点人群，为匹配改进提供目标）：

    R1 跨语言可达性 - name/name_cn 的 bigram Jaccard<0.1 的标题，用 name_cn 查询，
                      期望命中自身（检验中文名索引路径）。
    R2 子串碰撞     - 3~8 字短标题被另一条更长标题包含，用短原名查询，期望命中自身
                      （检验排序消歧：精确短标题不输给包含它的长标题）。
    R3 罗马字标题   - 归一化后 ASCII 字母占比 >= 0.6 的罗马字/英文标题，用原名查询，
                      期望命中自身（高频拉丁 bigram 区分度弱，检验召回）。
    R4 特殊字符     - 含括号/方括号/书名号的标题，去除括号内容后查询，期望命中自身
                      （检验"X (2023)"类标题的可达性）。
    R5 年份后缀     - 含 19xx/20xx 的标题，去年份后查询，期望命中自身
                      （检验年份/编号后缀剥离，区别于季后缀 G/M）。
    R6 别名可达     - 取 infobox 别名之一查询，期望命中自身
                      （检验别名索引路径可达性，区别于中文名 B）。
    R7 片假名变体   - 含长音/小书假名的标题，去片假名变体后查询，期望命中自身
                      （检验媒体归一化掉长音/小书假名后的可达性）。
    R8 分隔符变体   - 含中点/波浪分隔符的标题，去分隔符后查询，期望命中自身
                      （检验「・」等分隔符的可达性）。
    R9 数字编号     - 含 第N/S#/#N 编号的标题，去编号后查询，期望命中自身
                      （检验数字编号归一）。
    R10 全角半角    - 含全角 ASCII 的标题，转半角后查询，期望命中自身
                      （与场景 F 半角→全角互补，检验宽度归一）。
    """
    recs, inv3 = _build_recs(db_path, selected)
    # 全库规模发现（各 R 人群真实规模与需策略占比）仅在使用 --discover 时打印，
    # 默认单场景迭代不重扫全表（省 ~1.5s）。--reverse-discover 模式仍会无条件打印。
    if discover:
        _print_reverse_discovery(recs, inv3)
    # 深度候选键索引：用于反向用例的「唯一裸核」确定性过滤（剔除多编号变体等
    # 固有歧义用例，使 99% 指标反映真实可达性而非人为歧义）。
    deep_index = _build_deep_index(recs)

    random.seed(RANDOM_SEED)
    per = max(int(limit / 4), 300)
    per_rev = min(per, 400)  # 新增人群单类上限，控制采样规模
    cases: list[TestCase] = []

    # R1：跨语言可达性
    r1_pool = [r for r in recs if r[6] and _bigram_jaccard(r[5], r[6]) < 0.1]
    if "R1" in selected:
        for r in random.sample(r1_pool, min(per, len(r1_pool))):
            sid, name_cn, stype = r[0], r[2], r[3]
            cases.append(
                TestCase(
                    scenario="R1",
                    scenario_name="跨语言可达",
                    kind=CaseKind.TITLE,
                    query=name_cn,
                    subject_id=sid,
                    expected_ids={sid},
                    expected_min_eps=0,
                    subject_type=stype,
                    desc=f"id={sid} name_cn={name_cn!r}",
                )
            )

    # R2：子串碰撞（先抽短标题样本再做碰撞精判，控制挖掘代价）
    if "R2" in selected:
        short_pool = [r for r in recs if 3 <= len(r[5]) <= 8]
        short_sample = random.sample(short_pool, min(4000, len(short_pool)))
        r2_count = 0
        for r in short_sample:
            if r2_count >= per:
                break
            sid, name, nk = r[0], r[1], r[5]
            if _is_substring_collision(nk, sid, inv3):
                cases.append(
                    TestCase(
                        scenario="R2",
                        scenario_name="子串碰撞",
                        kind=CaseKind.TITLE,
                        query=name,
                        subject_id=sid,
                        expected_ids={sid},
                        expected_min_eps=0,
                        subject_type=r[3],
                        desc=f"id={sid} name={name!r}",
                    )
                )
                r2_count += 1

    # R3：罗马字高频 bigram 标题
    r3_pool = [r for r in recs if len(r[5]) >= 3 and _ascii_ratio(r[5]) >= 0.6]
    if "R3" in selected:
        for r in random.sample(r3_pool, min(per, len(r3_pool))):
            sid, name, stype = r[0], r[1], r[3]
            cases.append(
                TestCase(
                    scenario="R3",
                    scenario_name="罗马字标题",
                    kind=CaseKind.TITLE,
                    query=name,
                    subject_id=sid,
                    expected_ids={sid},
                    expected_min_eps=0,
                    subject_type=stype,
                    desc=f"id={sid} name={name!r}",
                )
            )

    # R4：特殊字符（用与生产一致的括号剥离生成裸核，过滤不可达用例）
    r4_pool = []
    for r in recs:
        name = r[1]
        if any(ch in name for ch in "()[]【】「」『』（）"):
            q = _deep_stripped_query(name, "R4")
            if q:
                r4_pool.append((r, q))
    if "R4" in selected:
        for r, q in random.sample(r4_pool, min(per, len(r4_pool))):
            sid, name, stype = r[0], r[1], r[3]
            if not _is_unique_core(q, sid, deep_index):
                continue
            cases.append(
                TestCase(
                    scenario="R4",
                    scenario_name="特殊字符",
                    kind=CaseKind.TITLE,
                    query=q,
                    subject_id=sid,
                    expected_ids={sid},
                    expected_min_eps=0,
                    subject_type=stype,
                    desc=f"id={sid} query={q!r} src={name!r}",
                )
            )

    # R5：年份后缀（用与生产一致的年份剥离生成裸核，过滤不可达用例）
    r5_pool = []
    for r in recs:
        q = _deep_stripped_query(r[1], "R5")
        if q:
            r5_pool.append((r, q))
    if "R5" in selected:
        for r, q in random.sample(r5_pool, min(per_rev, len(r5_pool))):
            sid, name, stype = r[0], r[1], r[3]
            if not _is_unique_core(q, sid, deep_index):
                continue
            cases.append(
                TestCase(
                    scenario="R5",
                    scenario_name="年份后缀",
                    kind=CaseKind.TITLE,
                    query=q,
                    subject_id=sid,
                    expected_ids={sid},
                    expected_min_eps=0,
                    subject_type=stype,
                    desc=f"id={sid} query={q!r} src={name!r}",
                )
            )

    # R6：别名可达（取最长别名查询）
    r6_pool = [r for r in recs if r[8]]
    if "R6" in selected:
        for r in random.sample(r6_pool, min(per_rev, len(r6_pool))):
            sid, name, stype, aliases_raw = r[0], r[1], r[3], r[8]
            alias = max(aliases_raw, key=len)
            cases.append(
                TestCase(
                    scenario="R6",
                    scenario_name="别名可达",
                    kind=CaseKind.TITLE,
                    query=alias,
                    subject_id=sid,
                    expected_ids={sid},
                    expected_min_eps=0,
                    subject_type=stype,
                    desc=f"id={sid} alias={alias!r}",
                )
            )

    # R7：片假名变体（用与生产一致的片假名剥离生成裸核，过滤不可达用例）
    r7_pool = []
    for r in recs:
        q = _deep_stripped_query(r[1], "R7")
        if q:
            r7_pool.append((r, q))
    if "R7" in selected:
        for r, q in random.sample(r7_pool, min(per_rev, len(r7_pool))):
            sid, name, stype = r[0], r[1], r[3]
            if not _is_unique_core(q, sid, deep_index):
                continue
            cases.append(
                TestCase(
                    scenario="R7",
                    scenario_name="片假名变体",
                    kind=CaseKind.TITLE,
                    query=q,
                    subject_id=sid,
                    expected_ids={sid},
                    expected_min_eps=0,
                    subject_type=stype,
                    desc=f"id={sid} query={q!r} src={name!r}",
                )
            )

    # R8：分隔符变体（去中点/波浪后查询）
    r8_pool = [r for r in recs if _has_separator(r[1])]
    if "R8" in selected:
        for r in random.sample(r8_pool, min(per_rev, len(r8_pool))):
            sid, name, stype = r[0], r[1], r[3]
            stripped = _strip_separator(name)
            if stripped and stripped != name:
                cases.append(
                    TestCase(
                        scenario="R8",
                        scenario_name="分隔符变体",
                        kind=CaseKind.TITLE,
                        query=stripped,
                        subject_id=sid,
                        expected_ids={sid},
                        expected_min_eps=0,
                        subject_type=stype,
                        desc=f"id={sid} query={stripped!r} src={name!r}",
                    )
                )

    # R9：数字编号（用与生产一致的数字编号剥离生成裸核，过滤不可达用例）
    r9_pool = []
    for r in recs:
        q = _deep_stripped_query(r[1], "R9")
        if q:
            r9_pool.append((r, q))
    if "R9" in selected:
        for r, q in random.sample(r9_pool, min(per_rev, len(r9_pool))):
            sid, name, stype = r[0], r[1], r[3]
            if not _is_unique_core(q, sid, deep_index):
                continue
            cases.append(
                TestCase(
                    scenario="R9",
                    scenario_name="数字编号",
                    kind=CaseKind.TITLE,
                    query=q,
                    subject_id=sid,
                    expected_ids={sid},
                    expected_min_eps=0,
                    subject_type=stype,
                    desc=f"id={sid} query={q!r} src={name!r}",
                )
            )

    # R10：全角半角（转半角后查询）
    r10_pool = [r for r in recs if _has_fullwidth_ascii(r[1])]
    if "R10" in selected:
        for r in random.sample(r10_pool, min(per_rev, len(r10_pool))):
            sid, name, stype = r[0], r[1], r[3]
            hw = _to_halfwidth_ascii(name)
            if hw != name:
                cases.append(
                    TestCase(
                        scenario="R10",
                        scenario_name="全角半角",
                        kind=CaseKind.TITLE,
                        query=hw,
                        subject_id=sid,
                        expected_ids={sid},
                        expected_min_eps=0,
                        subject_type=stype,
                        desc=f"id={sid} query={hw!r} src={name!r}",
                    )
                )

    return cases


# ===== 测试执行 =====


def setup_archive(db_path: Path) -> tuple[ArchiveShortcut, int]:
    """初始化 Archive 短路并返回可用的 shortcut 实例

    绕过 conftest 的隔离机制：直接设置 enabled 状态，
    并触发 FTS5 表构建（旧库升级场景）。

    Args:
        db_path: 本次基准要抽样的目标库路径（应与 active 库一致，
            否则 try_search 查询的索引与抽样来源不同库，结果无意义）。
            由 main() 在调用前按需切换 active 指针保证一致。

    Returns:
        (shortcut 实例, subject 表行数)
    """
    if not db_path.exists():
        print(f"错误: 目标库不存在: {db_path}")
        sys.exit(1)

    # 校验抽样库与 active 库一致（try_search 走 active 索引）
    active_path = bangumi_archive.get_active_db_path()
    if db_path.resolve() != active_path.resolve():
        print(
            f"错误: 抽样库 {db_path.name} 与 active 库 "
            f"{active_path.name} 不一致，try_search 将无法命中抽样来源。"
            f"请用 --db 选择与 active 相同的库，或确保 active 已切到该库。"
        )
        sys.exit(1)

    print(
        f"使用 active 库: {active_path} ({active_path.stat().st_size / 1024 / 1024:.1f} MB)"
    )

    # 检查 FTS5 表，必要时构建
    conn = sqlite3.connect(str(active_path))
    fts_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subject_fts'"
    ).fetchone()
    subject_count = conn.execute("SELECT COUNT(*) FROM subject").fetchone()[0]
    fts_count = 0
    if fts_exists:
        fts_count = conn.execute("SELECT COUNT(*) FROM subject_fts").fetchone()[0]
    conn.close()

    print(f"subject 表行数: {subject_count}")
    print(f"subject_fts 表存在: {fts_exists is not None}, 行数: {fts_count}")

    # FTS5 行数应与 subject 行数接近（允许部分无标题条目被跳过）
    # 若行数差距过大（如调试残留），删除旧表触发完整重建
    need_rebuild = not fts_exists or fts_count < subject_count * 0.5
    if need_rebuild:
        if fts_exists:
            print(
                f"FTS5 行数 {fts_count} 远少于 subject {subject_count}，删除旧表重建..."
            )
            conn = sqlite3.connect(str(active_path))
            conn.execute("DROP TABLE IF EXISTS subject_fts")
            conn.commit()
            conn.close()
        print("触发 FTS5 自动构建（65 万条约 7s）...")
        build_start = time.perf_counter()
        archive_title_index.invalidate()
        ok = archive_title_index._ensure_built()
        build_dur = time.perf_counter() - build_start
        print(f"FTS5 构建完成: ok={ok}, 耗时 {build_dur:.2f}s")
        if not ok:
            print("错误: FTS5 表构建失败")
            sys.exit(1)
        # 验证构建后行数
        conn = sqlite3.connect(str(active_path))
        fts_count = conn.execute("SELECT COUNT(*) FROM subject_fts").fetchone()[0]
        conn.close()
        print(f"构建后 subject_fts 行数: {fts_count}")
    else:
        # 确保 archive_title_index 连接到正确的库
        archive_title_index.invalidate()
        archive_title_index._ensure_built()

    # 初始化 ArchiveShortcut
    shortcut = ArchiveShortcut()
    shortcut._enabled = True  # 直接启用，绕过 config

    if not shortcut.enabled:
        print("错误: ArchiveShortcut 启用失败")
        sys.exit(1)

    if not archive_title_index.is_ready:
        print("错误: archive_title_index 未就绪")
        sys.exit(1)

    print("Archive 短路已启用，FTS5 索引就绪")
    return shortcut, subject_count


# ===== 批量查询优化 =====


@dataclass
class SubjectCacheEntry:
    """预加载的 subject 缓存条目（归一化后，查询时无需重复解析）"""

    norm_name: str
    norm_name_cn: str
    aliases_set: frozenset[str]


def preload_subject_cache(db_path: Path) -> tuple[dict[int, SubjectCacheEntry], float]:
    """预加载所有 subject 到内存，预归一化 name/name_cn/aliases

    批量查询优化的核心：把每条查询的"回表 SELECT + infobox 解析"
    替换为内存 dict 查找，避免 N 次 SQL 回表 + N 次 parse_infobox。

    Returns:
        (id → SubjectCacheEntry 映射, 预加载耗时秒数)
    """
    start = time.perf_counter()
    conn = sqlite3.connect(str(db_path))
    # 流式读取，避免 fetchall 一次性占用过多内存
    cursor = conn.execute("SELECT id, name, name_cn, infobox FROM subject")
    cache: dict[int, SubjectCacheEntry] = {}
    for sid, name, name_cn, infobox in cursor:
        norm_name = _normalize_key(name) if name else ""
        norm_name_cn = _normalize_key(name_cn) if name_cn else ""
        aliases_text = _extract_alias_text(infobox) if infobox else ""
        aliases_set = frozenset(aliases_text.split()) if aliases_text else frozenset()
        cache[sid] = SubjectCacheEntry(norm_name, norm_name_cn, aliases_set)
    conn.close()
    dur = time.perf_counter() - start
    return cache, dur


def batch_title_match(
    conn: sqlite3.Connection,
    cache: dict[int, SubjectCacheEntry],
    query: str,
    expected_ids: set[int],
) -> tuple[bool, list[int]]:
    """批量优化路径：FTS5 候选 + 内存精确匹配

    与 shortcut.try_search 的精确匹配路径一致：
    1. 归一化 query
    2. FTS5 MATCH 查候选 id（SQL，0.03ms）
    3. 内存 dict 精确匹配（无回表，无 infobox 解析）

    不包含 try_search 的后续策略（媒体前缀、标题分割、模糊兜底），
    未命中时调用方应回退到 shortcut.try_search。

    Returns:
        (是否命中期望 id, 命中的 subject_id 列表)
    """
    key = _normalize_key(query)
    if not key:
        return False, []
    candidate_ids = _collect_candidates_fts(conn, key, fuzzy=False)
    if not candidate_ids:
        return False, []
    hit = False
    hit_ids: list[int] = []
    for sid in candidate_ids:
        entry = cache.get(sid)
        if entry is None:
            continue
        # 精确匹配：归一化后相等，或 key 在 aliases 集合中
        if (
            entry.norm_name == key
            or entry.norm_name_cn == key
            or key in entry.aliases_set
        ):
            hit_ids.append(sid)
            if sid in expected_ids:
                hit = True
    return hit, hit_ids


# ===== bigram 倒排索引（纯内存批量查询） =====
#
# 注：生产路径 ArchiveFTSQuery 已用 FTS5 trigram 取代早期的内存 dict + bigram
# 索引（见 _fts_query.py 类注释）。此处把 bigram 倒排索引作为基准的可选"批量
# 索引"模式重新引入：构建一次、纯内存集合运算召回候选，避免 FTS5 的 SQL MATCH
# 与回表，用于大规模匹配结果的高速计算（非生产路径，命中准则与 FTS 候选路径一致：
# 期望 subject 进入 top-k 候选即算召回成功）。


def build_bigram_index(
    db_path: Path,
) -> tuple[dict[str, set[int]], int, float]:
    """构建二元(bigram)倒排索引：bigram -> {subject_id}

    覆盖 name / name_cn / aliases，与 FTS5 路径使用同一套 `_normalize_key`
    归一化（NFKC + 去标点 + 小写），保证候选可比。

    Returns:
        (postings: bigram->id集合, subject 条目数, 构建耗时秒)
    """
    start = time.perf_counter()
    postings: dict[str, set[int]] = collections.defaultdict(set)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT id, name, name_cn, infobox FROM subject")
        n = 0
        for sid, name, name_cn, infobox in cur:
            n += 1
            texts: list[str] = []
            if name:
                texts.append(_normalize_key(name))
            if name_cn:
                texts.append(_normalize_key(name_cn))
            aliases = _extract_alias_text(infobox) if infobox else ""
            if aliases:
                texts.append(aliases)  # 已是归一化空格拼接
            for t in texts:
                if len(t) < 2:
                    continue
                grams = {t[i : i + 2] for i in range(len(t) - 1)}
                for g in grams:
                    postings[g].add(sid)
    finally:
        conn.close()
    dur = time.perf_counter() - start
    return dict(postings), n, dur


def bigram_query(
    postings: dict[str, set[int]], query: str, topk: int = 10, max_scan_lists: int = 8
) -> list[int]:
    """用 bigram 倒排索引召回候选 subject id（按重叠 bigram 数排序）

    与 FTS5 trigram 的 MATCH 不同：纯内存集合运算，无 SQL。

    性能优化（merge by smallest list）：只对查询中"倒排列表最短"的若干个
    bigram 做集合扫描。真实文档必然同时出现在其所有 bigram 的倒排列表中
    （含稀有项），因此仅扫描最稀有的少数列表即可覆盖真实文档，同时把
    单次查询的扫描量从"所有 bigram 列表长度之和"降为"最短若干列表之和"，
    对含高频 bigram（常见中日文 pair）的查询提速显著，且不损失召回。
    """
    key = _normalize_key(query)
    if len(key) < 2:
        return []
    qgrams = [key[i : i + 2] for i in range(len(key) - 1)]
    # 按倒排列表长度升序，仅扫描最短的 max_scan_lists 个
    qgrams.sort(key=lambda g: len(postings.get(g, ())))
    scan = qgrams[:max_scan_lists]
    counts: collections.Counter = collections.Counter()
    for g in scan:
        sids = postings.get(g)
        if not sids:
            continue
        for sid in sids:
            counts[sid] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:topk]
    return [sid for sid, _ in ranked]


def bigram_title_match(
    postings: dict[str, set[int]],
    query: str,
    expected_ids: set[int],
    topk: int = 10,
) -> tuple[bool, list[int]]:
    """bigram 索引路径：召回 top-k 候选，期望 id 在候选内即判命中

    命中准则与 FTS5 候选路径一致：期望 subject 进入 top-k 候选即算召回成功。
    未命中（候选为空或不含期望 id）时由调用方回退 shortcut.try_search。
    """
    ids = bigram_query(postings, query, topk=topk)
    hit = bool(ids) and any(sid in expected_ids for sid in ids)
    return hit, ids


def run_cases_bigram(
    shortcut: ArchiveShortcut,
    cases: list[TestCase],
    postings: dict[str, set[int]],
) -> tuple[list[ScenarioStat], list[tuple[TestCase, list[dict]]]]:
    """bigram 倒排索引批量执行：TITLE 场景走内存索引，未命中回退 shortcut

    策略：
    - TITLE 场景：先走 bigram 索引召回（纯内存），命中即记；未命中回退
      shortcut.try_search（含媒体前缀/标题分割/模糊兜底）
    - SEASON/EPISODE 场景：直接走 shortcut（与 --batch 一致）

    Returns:
        (各场景统计列表, 失败用例样本列表)
    """
    stats: dict[str, ScenarioStat] = {s: ScenarioStat() for s in SCENARIOS}
    stats["ALL"] = ScenarioStat()
    stats["TITLE"] = ScenarioStat()
    stats["SEASON"] = ScenarioStat()
    stats["EPISODE"] = ScenarioStat()

    failures: list[tuple[TestCase, list]] = []
    max_fail_samples = 30

    bigram_hit_count = 0
    fallback_count = 0

    total = len(cases)
    print(f"\n开始执行 {total} 个用例（bigram 索引模式）...")

    for idx, case in enumerate(cases, 1):
        if idx % 2000 == 0:
            print(f"  进度: {idx}/{total} ({idx * 100 // total}%)")

        hit = False
        hit_data: list = []

        start = time.perf_counter()
        try:
            if case.kind == CaseKind.TITLE:
                bigram_hit, bigram_ids = bigram_title_match(
                    postings, case.query, case.expected_ids
                )
                if bigram_hit:
                    hit = True
                    hit_data = [{"id": sid} for sid in bigram_ids[:5]]
                    bigram_hit_count += 1
                else:
                    fallback_count += 1
                    subject_types = [case.subject_type] if case.subject_type else [2]
                    result = shortcut.try_search(
                        title=case.query,
                        start_date="",
                        end_date="",
                        limit=5,
                        subject_types=subject_types,
                    )
                    if result.hit and result.data:
                        hit_data = (
                            result.data
                            if isinstance(result.data, list)
                            else [result.data]
                        )
                        for item in hit_data:
                            if (
                                isinstance(item, dict)
                                and item.get("id") in case.expected_ids
                            ):
                                hit = True
                                break
            elif case.kind == CaseKind.SEASON:
                # J 场景验证双向续集图闭包；N 场景沿用单链续集链
                if case.scenario == "J":
                    result = shortcut.try_find_series_closure(
                        case.subject_id, max_hops=64
                    )
                elif case.scenario == "Q":
                    result = shortcut.try_find_franchise_closure(
                        case.subject_id, max_hops=64
                    )
                else:
                    result = shortcut.try_find_sequel_chain(
                        case.subject_id, max_hops=30
                    )
                if result.hit and result.data:
                    chain = (
                        result.data if isinstance(result.data, list) else [result.data]
                    )
                    hit_data = [{"id": sid} for sid in chain]
                    if set(chain) & case.expected_ids:
                        hit = True
            elif case.kind == CaseKind.EPISODE:
                result = shortcut.try_get_episodes(case.subject_id, episode_type=0)
                if result.hit and result.data:
                    eps = (
                        result.data if isinstance(result.data, list) else [result.data]
                    )
                    hit_data = eps
                    if len(eps) >= case.expected_min_eps:
                        hit = True
        except Exception as e:
            hit_data = [{"error": str(e)}]
        latency = (time.perf_counter() - start) * 1000

        stat = stats[case.scenario]
        stat.total += 1
        stat.latencies.append(latency)
        if hit:
            stat.hit += 1

        kind_key = case.kind.value.upper()
        stats[kind_key].total += 1
        stats[kind_key].latencies.append(latency)
        if hit:
            stats[kind_key].hit += 1

        stats["ALL"].total += 1
        stats["ALL"].latencies.append(latency)
        if hit:
            stats["ALL"].hit += 1

        if not hit and len(failures) < max_fail_samples:
            failures.append((case, hit_data))

    stat_order = ("ALL", "TITLE", "SEASON", "EPISODE", *SCENARIOS)
    stat_list = [stats[s] for s in stat_order if s in stats]

    total_title = stats["TITLE"].total
    if total_title > 0:
        print(
            f"  bigram 索引命中: {bigram_hit_count}/{total_title} "
            f"({bigram_hit_count * 100 / total_title:.1f}%)，"
            f"回退 shortcut: {fallback_count}/{total_title}"
        )

    return stat_list, failures


def run_cases_batch(
    shortcut: ArchiveShortcut,
    cases: list[TestCase],
    cache: dict[int, SubjectCacheEntry],
) -> tuple[list[ScenarioStat], list[tuple[TestCase, list[dict]]]]:
    """批量优化执行：TITLE 场景用预加载缓存 + FTS5 候选，避免回表

    策略：
    - TITLE 场景：先走批量精确匹配（内存缓存），未命中回退 shortcut.try_search
    - SEASON/EPISODE 场景：直接走 shortcut（已足够快，P95 < 10ms）

    Returns:
        (各场景统计列表, 失败用例样本列表)
    """
    stats: dict[str, ScenarioStat] = {s: ScenarioStat() for s in SCENARIOS}
    stats["ALL"] = ScenarioStat()
    stats["TITLE"] = ScenarioStat()
    stats["SEASON"] = ScenarioStat()
    stats["EPISODE"] = ScenarioStat()

    failures: list[tuple[TestCase, list]] = []
    max_fail_samples = 30

    # 获取 archive_title_index 的 conn（复用其连接，避免新建）
    conn = archive_title_index._ensure_conn()
    if conn is None:
        print("错误: 无法获取 archive_title_index 连接，回退到逐条模式")
        return run_cases(shortcut, cases)

    # 统计：批量路径命中数（未回退 shortcut 的数量）
    batch_hit_count = 0
    fallback_count = 0

    total = len(cases)
    print(f"\n开始执行 {total} 个用例（批量模式）...")

    for idx, case in enumerate(cases, 1):
        if idx % 2000 == 0:
            print(f"  进度: {idx}/{total} ({idx * 100 // total}%)")

        hit = False
        hit_data: list = []

        start = time.perf_counter()
        try:
            if case.kind == CaseKind.TITLE:
                # 批量优化路径：先尝试内存精确匹配
                batch_hit, batch_ids = batch_title_match(
                    conn, cache, case.query, case.expected_ids
                )
                if batch_hit:
                    hit = True
                    hit_data = [{"id": sid} for sid in batch_ids[:5]]
                    batch_hit_count += 1
                else:
                    # 未命中，回退到 shortcut.try_search（含模糊兜底）
                    fallback_count += 1
                    subject_types = [case.subject_type] if case.subject_type else [2]
                    result = shortcut.try_search(
                        title=case.query,
                        start_date="",
                        end_date="",
                        limit=5,
                        subject_types=subject_types,
                    )
                    if result.hit and result.data:
                        hit_data = (
                            result.data
                            if isinstance(result.data, list)
                            else [result.data]
                        )
                        for item in hit_data:
                            if (
                                isinstance(item, dict)
                                and item.get("id") in case.expected_ids
                            ):
                                hit = True
                                break
            elif case.kind == CaseKind.SEASON:
                # J 场景验证双向续集图闭包；N 场景沿用单链续集链
                if case.scenario == "J":
                    result = shortcut.try_find_series_closure(
                        case.subject_id, max_hops=64
                    )
                elif case.scenario == "Q":
                    result = shortcut.try_find_franchise_closure(
                        case.subject_id, max_hops=64
                    )
                else:
                    result = shortcut.try_find_sequel_chain(
                        case.subject_id, max_hops=30
                    )
                if result.hit and result.data:
                    chain = (
                        result.data if isinstance(result.data, list) else [result.data]
                    )
                    hit_data = [{"id": sid} for sid in chain]
                    if set(chain) & case.expected_ids:
                        hit = True
            elif case.kind == CaseKind.EPISODE:
                result = shortcut.try_get_episodes(case.subject_id, episode_type=0)
                if result.hit and result.data:
                    eps = (
                        result.data if isinstance(result.data, list) else [result.data]
                    )
                    hit_data = eps
                    if len(eps) >= case.expected_min_eps:
                        hit = True
        except Exception as e:
            hit_data = [{"error": str(e)}]
        latency = (time.perf_counter() - start) * 1000

        stat = stats[case.scenario]
        stat.total += 1
        stat.latencies.append(latency)
        if hit:
            stat.hit += 1

        kind_key = case.kind.value.upper()
        stats[kind_key].total += 1
        stats[kind_key].latencies.append(latency)
        if hit:
            stats[kind_key].hit += 1

        stats["ALL"].total += 1
        stats["ALL"].latencies.append(latency)
        if hit:
            stats["ALL"].hit += 1

        if not hit and len(failures) < max_fail_samples:
            failures.append((case, hit_data))

    stat_order = ("ALL", "TITLE", "SEASON", "EPISODE", *SCENARIOS)
    stat_list = [stats[s] for s in stat_order if s in stats]

    total_title = stats["TITLE"].total
    if total_title > 0:
        print(
            f"  批量路径命中: {batch_hit_count}/{total_title} "
            f"({batch_hit_count * 100 / total_title:.1f}%)，"
            f"回退 shortcut: {fallback_count}/{total_title}"
        )

    return stat_list, failures


def run_cases(
    shortcut: ArchiveShortcut,
    cases: list[TestCase],
    dump_failures_path: str | None = None,
) -> tuple[list[ScenarioStat], list[tuple[TestCase, list[dict]]]]:
    """执行所有测试用例

    根据 case.kind 调用不同的 shortcut 方法：
    - TITLE: try_search（标题搜索）
    - SEASON: try_find_sequel_chain（续集链查询）
    - EPISODE: try_get_episodes（集数查询）

    Returns:
        (各场景统计列表, 失败用例样本列表)
    """
    stats: dict[str, ScenarioStat] = {s: ScenarioStat() for s in SCENARIOS}
    # 合并总计：ALL 全部，TITLE/SEASON/EPISODE 按用例类型分组
    stats["ALL"] = ScenarioStat()
    stats["TITLE"] = ScenarioStat()
    stats["SEASON"] = ScenarioStat()
    stats["EPISODE"] = ScenarioStat()

    failures: list[tuple[TestCase, list]] = []
    max_fail_samples = 30  # 最多记录 30 个失败样本

    total = len(cases)
    print(f"\n开始执行 {total} 个用例...")

    for idx, case in enumerate(cases, 1):
        if idx % 2000 == 0:
            print(f"  进度: {idx}/{total} ({idx * 100 // total}%)")

        hit = False
        hit_data: list = []

        start = time.perf_counter()
        try:
            if case.kind == CaseKind.TITLE:
                # 标题查询：调用 try_search
                subject_types = [case.subject_type] if case.subject_type else [2]
                result = shortcut.try_search(
                    title=case.query,
                    start_date="",
                    end_date="",
                    limit=5,
                    subject_types=subject_types,
                )
                if result.hit and result.data:
                    hit_data = (
                        result.data if isinstance(result.data, list) else [result.data]
                    )
                    for item in hit_data:
                        if (
                            isinstance(item, dict)
                            and item.get("id") in case.expected_ids
                        ):
                            hit = True
                            break
            elif case.kind == CaseKind.SEASON:
                # 季查询：J 调 try_find_series_closure（续集/前传闭包），
                #         Q 调 try_find_franchise_closure（同IP闭包），N 调 try_find_sequel_chain
                if case.scenario == "J":
                    result = shortcut.try_find_series_closure(
                        case.subject_id, max_hops=64
                    )
                elif case.scenario == "Q":
                    result = shortcut.try_find_franchise_closure(
                        case.subject_id, max_hops=64
                    )
                else:
                    result = shortcut.try_find_sequel_chain(
                        case.subject_id, max_hops=30
                    )
                if result.hit and result.data:
                    chain = (
                        result.data if isinstance(result.data, list) else [result.data]
                    )
                    hit_data = [{"id": sid} for sid in chain]
                    # 期望：返回的闭包与 subject_relation 记录（续集∪前传）有交集
                    if set(chain) & case.expected_ids:
                        hit = True
            elif case.kind == CaseKind.EPISODE:
                # 集查询：调用 try_get_episodes
                result = shortcut.try_get_episodes(case.subject_id, episode_type=0)
                if result.hit and result.data:
                    eps = (
                        result.data if isinstance(result.data, list) else [result.data]
                    )
                    hit_data = eps
                    # 期望：返回的 episode 数 >= 抽样统计值
                    if len(eps) >= case.expected_min_eps:
                        hit = True
        except Exception as e:
            # 单个用例异常不中断整体测试
            hit_data = [{"error": str(e)}]
        latency = (time.perf_counter() - start) * 1000  # ms

        # 更新统计
        stat = stats[case.scenario]
        stat.total += 1
        stat.latencies.append(latency)
        if hit:
            stat.hit += 1

        # 按用例类型分组统计
        kind_key = case.kind.value.upper()  # TITLE / SEASON / EPISODE
        stats[kind_key].total += 1
        stats[kind_key].latencies.append(latency)
        if hit:
            stats[kind_key].hit += 1

        stats["ALL"].total += 1
        stats["ALL"].latencies.append(latency)
        if hit:
            stats["ALL"].hit += 1

        # 记录失败样本
        if not hit:
            if len(failures) < max_fail_samples:
                failures.append((case, hit_data))
            if dump_failures_path:
                got_ids = [
                    d.get("id") for d in hit_data if isinstance(d, dict) and "id" in d
                ]
                # 诊断增强：直接走生产精确匹配接口（find_subject_ids_by_title，
                # 含深度归一化），看期望 id 是否「本就可达」——若可达但不在 top5
                # 说明是排序截断（过度合并），若不可达说明是装饰剥离覆盖缺口。
                full_ids: list[int] = []
                if case.kind == CaseKind.TITLE:
                    try:
                        full_ids = archive_title_index.find_subject_ids_by_title(
                            case.query
                        )
                    except Exception:
                        full_ids = []
                rec = {
                    "scenario": case.scenario,
                    "kind": case.kind.value,
                    "query": case.query,
                    "expected_ids": sorted(case.expected_ids),
                    "got_ids": got_ids,
                    "full_match_count": len(full_ids),
                    "expected_in_full": bool(case.expected_ids & set(full_ids)),
                    "subject_type": case.subject_type,
                    "desc": case.desc,
                }
                with open(dump_failures_path, "a", encoding="utf-8") as _f:
                    _f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 转为有序列表：ALL 总计 + 按类型分组 + 各场景明细
    stat_order = ("ALL", "TITLE", "SEASON", "EPISODE", *SCENARIOS)
    stat_list = [stats[s] for s in stat_order if s in stats]
    return stat_list, failures


# ===== 报告输出 =====


def format_ms(ms: float) -> str:
    """格式化耗时"""
    if ms < 1:
        return f"{ms * 1000:.0f}μs"
    if ms < 10:
        return f"{ms:.2f}ms"
    return f"{ms:.1f}ms"


def print_report(
    stat_list: list[ScenarioStat],
    failures: list[tuple[TestCase, list[dict]]],
    total_cases: int,
    subject_count: int,
    build_time: float,
) -> None:
    """打印测试报告"""
    print("\n" + "=" * 80)
    print("Archive 真实数据拟真测试报告")
    print("=" * 80)

    print("\n数据规模:")
    print(f"  subject 总数: {subject_count}")
    print(f"  测试用例总数: {total_cases}")
    if build_time > 0:
        print(f"  FTS5 表构建耗时: {build_time:.2f}s")

    print(
        f"\n{'场景':<20} {'用例数':>8} {'命中数':>8} {'命中率':>8} "
        f"{'平均':>8} {'P50':>8} {'P95':>8} {'P99':>8}"
    )
    print("-" * 88)

    stat_names = ("ALL", "TITLE", "SEASON", "EPISODE", *SCENARIOS)
    for stat_name, stat in zip(stat_names, stat_list):
        if stat.total == 0:
            continue
        scenario_label = {
            "ALL": "【联合】总计",
            "TITLE": "【Subject】标题匹配",
            "SEASON": "【Season】季查询",
            "EPISODE": "【Episode】集查询",
            "A": "  A 原名精确",
            "B": "  B 中文名精确",
            "C": "  C 大小写差异",
            "D": "  D 首尾空白",
            "E": "  E 标点去除",
            "F": "  F 全角半角",
            "G": "  G 季后缀剥离",
            "H": "  H 模糊typo",
            "I": "  I 子串包含",
            "J": "  J 续集链查询",
            "K": "  K 集数查询",
            "L": "  L 单季动画",
            "M": "  M 季后缀扩展",
            "N": "  N 跨季续集链",
            "O": "  O 长篇动画",
            "P": "  P 电影本篇",
            "R1": "  R1 跨语言可达",
            "R2": "  R2 子串碰撞",
            "R3": "  R3 罗马字标题",
            "R4": "  R4 特殊字符",
            "R5": "  R5 年份后缀",
            "R6": "  R6 别名可达",
            "R7": "  R7 片假名变体",
            "R8": "  R8 分隔符变体",
            "R9": "  R9 数字编号",
            "R10": "  R10 全角半角",
        }.get(stat_name, stat_name)
        print(
            f"{scenario_label:<20} {stat.total:>8} {stat.hit:>8} "
            f"{stat.hit_rate * 100:>7.2f}% {format_ms(stat.avg):>8} "
            f"{format_ms(stat.p50):>8} {format_ms(stat.p95):>8} "
            f"{format_ms(stat.p99):>8}"
        )

    # 失败样本
    if failures:
        print(f"\n失败用例样本（前 {len(failures)} 个）:")
        for case, hit_data in failures[:10]:
            if case.kind == CaseKind.TITLE:
                hit_ids = [d.get("id") for d in hit_data if isinstance(d, dict)]
                print(f"  [{case.scenario}] query={case.query!r}")
                print(f"    expected={case.expected_ids}, got={hit_ids}, {case.desc}")
            elif case.kind == CaseKind.SEASON:
                hit_ids = [d.get("id") for d in hit_data if isinstance(d, dict)]
                print(f"  [{case.scenario}] subject_id={case.subject_id}")
                print(
                    f"    expected_sequels={case.expected_ids}, got={hit_ids}, {case.desc}"
                )
            elif case.kind == CaseKind.EPISODE:
                ep_count = len(hit_data)
                print(f"  [{case.scenario}] subject_id={case.subject_id}")
                print(
                    f"    expected_min={case.expected_min_eps}, got={ep_count}, {case.desc}"
                )

    # 总结：按类型分组展示
    all_stat = stat_list[0]
    title_stat = stat_list[1] if len(stat_list) > 1 else ScenarioStat()
    season_stat = stat_list[2] if len(stat_list) > 2 else ScenarioStat()
    episode_stat = stat_list[3] if len(stat_list) > 3 else ScenarioStat()

    print("\n总结（按匹配类型分组）:")
    print(
        f"  【联合】总命中率: {all_stat.hit_rate * 100:.2f}% "
        f"({all_stat.hit}/{all_stat.total}), 平均 {format_ms(all_stat.avg)}, "
        f"P95 {format_ms(all_stat.p95)}, P99 {format_ms(all_stat.p99)}"
    )
    if title_stat.total > 0:
        print(
            f"  【Subject】标题匹配: {title_stat.hit_rate * 100:.2f}% "
            f"({title_stat.hit}/{title_stat.total}), 平均 {format_ms(title_stat.avg)}, "
            f"P95 {format_ms(title_stat.p95)}, P99 {format_ms(title_stat.p99)}"
        )
    if season_stat.total > 0:
        print(
            f"  【Season】季查询: {season_stat.hit_rate * 100:.2f}% "
            f"({season_stat.hit}/{season_stat.total}), 平均 {format_ms(season_stat.avg)}, "
            f"P95 {format_ms(season_stat.p95)}, P99 {format_ms(season_stat.p99)}"
        )
    if episode_stat.total > 0:
        print(
            f"  【Episode】集查询: {episode_stat.hit_rate * 100:.2f}% "
            f"({episode_stat.hit}/{episode_stat.total}), 平均 {format_ms(episode_stat.avg)}, "
            f"P95 {format_ms(episode_stat.p95)}, P99 {format_ms(episode_stat.p99)}"
        )

    # 验收标准
    print("\n验收标准参考:")
    print("  - Subject 标题匹配率应 >= 95%（精确场景），含模糊场景应 >= 85%")
    print("  - Season 季查询命中率应 >= 95%")
    print("  - Episode 集查询命中率应 >= 99%")
    print("  - P95 耗时应 < 5ms（FTS5 方案基线）")
    print("  - P99 耗时应 < 50ms（2 字符回退 LIKE 场景）")


# ===== 主入口 =====


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive 真实数据拟真测试")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"抽样上限（默认 {DEFAULT_LIMIT}）",
    )
    parser.add_argument(
        "--m-limit",
        type=int,
        default=400,
        help="M 季后缀扩展场景的 subject 抽样上限（默认 400，与全局 --limit 独立）。"
        "M 用例数 = 此值 × 13 种后缀格式；收敛 M 体量、加速 A-P 全跑，"
        "后缀格式覆盖不变。设 0 表示不封顶（沿用全局 --limit）",
    )
    parser.add_argument(
        "--scenarios",
        type=str,
        default=",".join(SCENARIOS),
        help=f"运行场景，逗号分隔（默认全部: {','.join(SCENARIOS)}）",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="active",
        choices=("active", "a", "b"),
        help="指定基准目标库：active（默认，读 bangumi_archive.active 指向的库）/ a / b",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="启用批量查询优化（预加载 subject 到内存，避免回表）",
    )
    parser.add_argument(
        "--bigram",
        action="store_true",
        help="启用 bigram 倒排索引批量查询（内存构建 bigram 索引，TITLE 查询走纯内存召回）",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="反向诊断模式：从真实数据分布挖掘难例（R1 跨语言/R2 子串碰撞/"
        "R3 罗马字/R4 特殊字符/R5 年份/R6 别名/R7 片假名/R8 分隔符/R9 数字编号/"
        "R10 全角半角），经生产 try_search 测真实难例命中率",
    )
    parser.add_argument(
        "--reverse-discover",
        action="store_true",
        help="仅做反向全库难例规模发现（秒级扫描各 R 人群真实规模与需策略占比），"
        "不构造用例、不跑 try_search，用于先量化匹配难题体量",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="--reverse 模式下同时打印全库各 R 人群规模（默认关，单场景迭代更省时）；"
        "规模发现也可用独立的 --reverse-discover 完成",
    )
    parser.add_argument(
        "--dump-failures",
        type=str,
        default=None,
        metavar="PATH",
        help="将所有失败用例（含 query/期望id/实际命中top/描述）导出为 jsonl，"
        "用于迭代式失败模式归类分析（不限 30 条内存上限）",
    )
    args = parser.parse_args()

    # 解析场景
    selected = tuple(s.strip().upper() for s in args.scenarios.split(",") if s.strip())
    invalid = [s for s in selected if s not in SCENARIOS]
    if invalid:
        print(f"错误: 未知场景 {invalid}，可选: {SCENARIOS}")
        sys.exit(1)

    # 反向模式：场景限定在 REVERSE_SCENARIOS 内
    if args.reverse:
        selected = (
            tuple(s for s in selected if s in REVERSE_SCENARIOS) or REVERSE_SCENARIOS
        )

    # 解析目标库（支持 a/b/active 双库形式）
    if args.db == "active":
        chosen = bangumi_archive._meta.active
    else:
        chosen = args.db
    db_path = DB_PATH_A if chosen == "a" else DB_PATH
    if not db_path.exists():
        available = [
            name for name, p in (("a", DB_PATH_A), ("b", DB_PATH)) if p.exists()
        ]
        print(f"错误: 目标库 {db_path.name} 不存在（--db={args.db}）。")
        print(f"  当前可用的库: {available if available else '无'}")
        sys.exit(1)

    # 对齐 active 指针与抽样库：try_search 走 active 索引，
    # 若 active 与目标库不一致，进程内切换 active（不落盘），保证查询与抽样同库。
    if bangumi_archive._meta.active != chosen:
        print(
            f"提示: active 当前为 '{bangumi_archive._meta.active}'，"
            f"基准目标为 '{chosen}'，进程内切换 active 指针以对齐..."
        )
        bangumi_archive._meta.active = chosen
        archive_title_index.invalidate()

    print(
        f"配置: 抽样上限={args.limit}, 场景={selected}, 目标库={chosen} ({db_path.name})"
    )

    # 1. 抽样 / 反向挖掘
    if args.reverse_discover:
        print("\n[反向全库难例规模发现（仅统计，不跑 try_search）]...")
        recs, inv3 = _build_recs(db_path)
        _print_reverse_discovery(recs, inv3)
        print(
            "\n提示: 以 --reverse 模式重跑可对选中人群采样并跑生产 try_search，"
            "得到真实命中率；以 --reverse --scenarios R5 等可单人群深挖。"
        )
        return
    if args.reverse:
        print("\n[1/5] 反向挖掘真实难例（不抽样随机 subject）...")
        cases = mine_reverse_cases(
            db_path, args.limit, selected, discover=args.discover
        )
        print(f"  挖掘完成: {len(cases)} 个难例用例（场景 {selected}）")
    else:
        print("\n[1/5] 从真实 db 抽样 subject...")
        samples = sample_subjects(db_path, args.limit)
        print(f"  抽样完成: {len(samples)} 条 subject")

        # 类型分布
        type_dist: dict[int, int] = {}
        for s in samples:
            type_dist[s.type] = type_dist.get(s.type, 0) + 1
        print(f"  类型分布: {type_dist}")

        # 1b. 抽样有 episode 和有续集关联的 subject（用于季集场景）
        episode_samples: list[tuple[int, int]] = []
        sequel_samples: list[tuple[int, list[int], list[int]]] = []
        single_season_samples: list[SubjectSample] = []
        long_anime_samples: list[tuple[int, int]] = []
        movie_samples: list[SubjectSample] = []
        franchise_samples: list[tuple[int, list[int]]] = []
        if "J" in selected or "N" in selected:
            print("\n[1b/5] 抽样有续集关联的 subject...")
            sequel_samples = sample_subjects_with_sequels(db_path, args.limit)
            print(f"  抽样完成: {len(sequel_samples)} 条有续集的 subject")
        if "Q" in selected:
            print("\n[1b/5] 抽样有「同作品」关系关联的 subject（场景 Q）...")
            franchise_samples = sample_subjects_with_franchise(db_path, args.limit)
            print(f"  抽样完成: {len(franchise_samples)} 条有同IP关系的 subject")
        if "K" in selected or "O" in selected:
            print("\n[1c/5] 抽样有 episode 的 subject...")
            episode_samples = sample_subjects_with_episodes(db_path, args.limit)
            print(f"  抽样完成: {len(episode_samples)} 条有 episode 的 subject")
        if "L" in selected:
            print("\n[1d/5] 抽样单季动画（无续集关联）...")
            single_season_samples = sample_single_season_anime(db_path, args.limit)
            print(f"  抽样完成: {len(single_season_samples)} 条单季动画")
        if "O" in selected:
            print("\n[1e/5] 抽样长篇动画（集数 >= 100）...")
            long_anime_samples = sample_long_anime_with_episodes(db_path, args.limit)
            print(f"  抽样完成: {len(long_anime_samples)} 条长篇动画")
        if "P" in selected:
            print("\n[1f/5] 抽样电影类型（type=6）...")
            movie_samples = sample_movies(db_path, args.limit)
            print(f"  抽样完成: {len(movie_samples)} 条电影")
        if "S" in selected or "T" in selected:
            print("\n[1g/5] 抽样同名多义(S)/CJK缺口(T)真实人群...")
            # S/T 直接基于全库真实分布生成，无需独立 sampler 中间结构

        # 2. 生成用例
        print("\n[2/5] 生成查询用例变体...")
        cases = generate_cases(samples, selected)
        cases += generate_season_cases(sequel_samples, selected)
        cases += generate_episode_cases(episode_samples, selected)
        cases += generate_single_season_cases(single_season_samples, selected)
        cases += generate_season_suffix_cases(samples, selected, args.m_limit)
        cases += generate_cross_season_cases(sequel_samples, selected, db_path)
        cases += generate_franchise_cases(franchise_samples, selected)
        cases += generate_long_anime_cases(long_anime_samples, selected)
        cases += generate_movie_cases(movie_samples, selected)
        cases += generate_same_name_cases(db_path, selected, args.limit)
        cases += generate_cjk_gap_cases(db_path, selected, args.limit)
        print(f"  生成 {len(cases)} 个用例")

    # 3. 初始化 Archive
    print("\n[3/5] 初始化 Archive 短路...")
    build_start = time.perf_counter()
    shortcut, subject_count = setup_archive(db_path)
    build_time = time.perf_counter() - build_start

    # 批量模式：预加载 subject 到内存
    subject_cache: dict[int, SubjectCacheEntry] = {}
    if args.batch:
        print("\n[3b/5] 预加载 subject 到内存（批量优化）...")
        subject_cache, cache_dur = preload_subject_cache(db_path)
        build_time += cache_dur
        print(
            f"  预加载完成: {len(subject_cache)} 条 subject, "
            f"耗时 {cache_dur:.2f}s, "
            f"内存约 {len(subject_cache) * 0.0001:.0f}MB"
        )

    # bigram 倒排索引模式：构建内存索引（覆盖全库，纯集合运算召回）
    bigram_index: dict[str, set[int]] = {}
    if args.bigram:
        print("\n[3c/5] 构建 bigram 倒排索引（批量索引）...")
        bigram_index, idx_n, idx_dur = build_bigram_index(db_path)
        build_time += idx_dur
        print(
            f"  索引构建完成: {idx_n} 条 subject, "
            f"{len(bigram_index)} 个 bigram, 耗时 {idx_dur:.2f}s"
        )

    # 4. 执行测试
    if args.reverse and args.bigram:
        print(
            "⚠️ 反向诊断启用了 --bigram：bigram 模式测的是「倒排索引能否找回」"
            "(乐观可达性)，会高估真实命中率（如 R4 特殊字符在 bigram 下约 88% "
            "但生产 try_search 仅约 36%）。诊断真实匹配缺口请以不带 --bigram 的"
            "生产 try_search 结果为准；--bigram 仅用于快速乐观可达性估计。"
        )
    if args.bigram:
        mode_label = "bigram索引"
    elif args.batch:
        mode_label = "批量"
    else:
        mode_label = "逐条"
    print(f"\n[4/5] 执行测试（{mode_label}模式）...")
    test_start = time.perf_counter()
    if args.dump_failures:
        # 清空旧 dump 文件，保证本次导出为全新结果
        with open(args.dump_failures, "w", encoding="utf-8") as _f:
            pass
    if args.bigram and bigram_index:
        stat_list, failures = run_cases_bigram(shortcut, cases, bigram_index)
    elif args.batch and subject_cache:
        stat_list, failures = run_cases_batch(shortcut, cases, subject_cache)
    else:
        stat_list, failures = run_cases(shortcut, cases, args.dump_failures)
    test_dur = time.perf_counter() - test_start

    # 5. 报告
    print("\n[5/5] 生成报告...")
    print_report(stat_list, failures, len(cases), subject_count, build_time)

    print(f"\n总执行耗时: {test_dur:.2f}s (含 {len(cases)} 个用例)")
    if cases:
        print(f"平均每用例: {test_dur * 1000 / len(cases):.2f}ms")
    else:
        print("平均每用例: N/A（无用例生成）")


if __name__ == "__main__":
    main()
