"""Archive 真实数据拟真测试

从 data/archive/bangumi_archive_b.db 抽样真实 subject，构造多场景查询变体，
统计完整匹配率和匹配耗时。验证 FTS5 trigram 方案在 65 万+ 真实数据上的表现。

运行方式:
    uv run python tests/test_archive_real_benchmark.py
    uv run python tests/test_archive_real_benchmark.py --limit 20000   # 指定抽样上限
    uv run python tests/test_archive_real_benchmark.py --scenarios A,B  # 仅跑指定场景

前置条件:
    - data/archive/bangumi_archive_b.db 存在（已通过 Web 导入）
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
    J 续集链查询   - 对有续集关联的 subject 查询 try_find_sequel_chain
    K 集数查询     - 对有 episode 的 subject 查询 try_get_episodes
    L 单季动画     - type=2 且无续集关联的动画，标题查询应命中自身
    M 季后缀扩展   - 测试 S06E279 / Season 2 / 第2期 等多种季后缀格式剥离
    N 跨季续集链   - 多季动画（续集链长度 >= 2）的 try_find_sequel_chain
    O 长篇动画     - 集数 >= 100 的动画，try_get_episodes 应返回完整集列表
    P 电影本篇     - type=6（三次元/电影）的标题查询，验证电影场景匹配
"""

from __future__ import annotations

import argparse
import random
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
)
from app.utils.bangumi_archive._title_index import archive_title_index  # noqa: E402

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
    "K",
    "L",
    "M",
    "N",
    "O",
    "P",
)

# 模糊匹配阈值（与 try_search 默认一致）
FUZZY_THRESHOLD = 80

# 随机种子（可复现）
RANDOM_SEED = 20260729

# 续集关联类型（bangumi_constants.py: RELATION_ID_SEQUEL = 3）
RELATION_ID_SEQUEL = 3


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
) -> list[tuple[int, list[int]]]:
    """抽样有续集关联的 subject，用于季查询场景

    从 subject_relation 表中查询 relation_type=3（续集）的关联，
    返回 (起始 subject_id, [续集 subject_id 列表]) 的列表。

    Returns:
        [(subject_id, [sequel_id, ...]), ...]
    """
    conn = sqlite3.connect(str(db_path))
    scaled = max(int(limit * 0.5), min(limit, 100))
    # 找出有续集的 subject（即作为 relation_type=3 的起点）
    rows = conn.execute(
        "SELECT subject_id, related_subject_id FROM subject_relation "
        "WHERE relation_type = ? "
        "ORDER BY RANDOM() LIMIT ?",
        (RELATION_ID_SEQUEL, scaled * 3),  # 多取一些用于去重
    ).fetchall()

    # 按 subject_id 聚合续集列表
    subject_to_sequels: dict[int, list[int]] = {}
    for sid, rid in rows:
        subject_to_sequels.setdefault(sid, []).append(rid)

    # 过滤掉无效的 subject（可能已不在 subject 表中）
    valid_ids = (
        set(
            conn.execute(
                f"SELECT id FROM subject WHERE id IN ({','.join('?' * len(subject_to_sequels))})",
                list(subject_to_sequels.keys()),
            ).fetchall()
        )
        if subject_to_sequels
        else set()
    )
    valid_ids = {r[0] for r in valid_ids}

    result = [
        (sid, seq_list)
        for sid, seq_list in subject_to_sequels.items()
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
            query = f"{s.name} 第二季"
            expected = name_to_ids.get(s.name.strip(), {s.id})
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
    sequel_samples: list[tuple[int, list[int]]],
    scenarios: tuple[str, ...],
) -> list[TestCase]:
    """生成季查询场景 J：调用 try_find_sequel_chain 验证续集链

    期望：对有续集关联的 subject 查询，应返回非空续集链，
    且链中包含 subject_relation 中记录的续集 id。
    """
    if "J" not in scenarios:
        return []
    cases: list[TestCase] = []
    for subject_id, sequel_ids in sequel_samples:
        cases.append(
            TestCase(
                scenario="J",
                scenario_name="续集链查询",
                kind=CaseKind.SEASON,
                query="",
                subject_id=subject_id,
                expected_ids=set(sequel_ids),
                expected_min_eps=0,
                subject_type=2,
                desc=f"id={subject_id} sequels={sequel_ids}",
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
    samples: list[SubjectSample], scenarios: tuple[str, ...]
) -> list[TestCase]:
    """生成场景 M：季后缀剥离扩展

    对每个样本尝试多种季后缀格式，验证 _strip_season_episode_suffix
    能正确剥离各格式后命中核心标题。
    """
    if "M" not in scenarios:
        return []
    cases: list[TestCase] = []
    for s in samples:
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
    sequel_samples: list[tuple[int, list[int]]],
    scenarios: tuple[str, ...],
    db_path: Path,
) -> list[TestCase]:
    """生成场景 N：跨季续集链

    筛选续集链长度 >= 2 的多季动画，验证 try_find_sequel_chain
    能返回完整长链。
    """
    if "N" not in scenarios:
        return []
    cases: list[TestCase] = []
    conn = sqlite3.connect(str(db_path))
    try:
        for subject_id, sequel_ids in sequel_samples:
            if len(sequel_ids) < 2:
                continue
            # 验证续集都在 subject 表中
            placeholders = ",".join("?" * len(sequel_ids))
            valid = conn.execute(
                f"SELECT id FROM subject WHERE id IN ({placeholders})",
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


# ===== 测试执行 =====


def setup_archive() -> ArchiveShortcut:
    """初始化 Archive 短路并返回可用的 shortcut 实例

    绕过 conftest 的隔离机制：直接设置 enabled 状态，
    并触发 FTS5 表构建（旧库升级场景）。
    """
    # 确认 active db 路径正确
    active_path = bangumi_archive.get_active_db_path()
    if not active_path.exists():
        # 尝试 a 库
        if DB_PATH_A.exists():
            print(f"警告: active 库 {active_path} 不存在，但发现 {DB_PATH_A}")
        print(f"错误: active 库不存在: {active_path}")
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
    return shortcut


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
                result = shortcut.try_find_sequel_chain(case.subject_id, max_hops=30)
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
                # 季查询：调用 try_find_sequel_chain
                result = shortcut.try_find_sequel_chain(case.subject_id, max_hops=30)
                if result.hit and result.data:
                    chain = (
                        result.data if isinstance(result.data, list) else [result.data]
                    )
                    hit_data = [{"id": sid} for sid in chain]
                    # 期望：返回的续集链与 subject_relation 记录有交集
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
        if not hit and len(failures) < max_fail_samples:
            failures.append((case, hit_data))

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
        "--scenarios",
        type=str,
        default=",".join(SCENARIOS),
        help=f"运行场景，逗号分隔（默认全部: {','.join(SCENARIOS)}）",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="启用批量查询优化（预加载 subject 到内存，避免回表）",
    )
    args = parser.parse_args()

    # 解析场景
    selected = tuple(s.strip().upper() for s in args.scenarios.split(",") if s.strip())
    invalid = [s for s in selected if s not in SCENARIOS]
    if invalid:
        print(f"错误: 未知场景 {invalid}，可选: {SCENARIOS}")
        sys.exit(1)

    print(f"配置: 抽样上限={args.limit}, 场景={selected}")

    # 1. 抽样
    print("\n[1/5] 从真实 db 抽样 subject...")
    samples = sample_subjects(DB_PATH, args.limit)
    print(f"  抽样完成: {len(samples)} 条 subject")

    # 类型分布
    type_dist: dict[int, int] = {}
    for s in samples:
        type_dist[s.type] = type_dist.get(s.type, 0) + 1
    print(f"  类型分布: {type_dist}")

    # 1b. 抽样有 episode 和有续集关联的 subject（用于季集场景）
    episode_samples: list[tuple[int, int]] = []
    sequel_samples: list[tuple[int, list[int]]] = []
    single_season_samples: list[SubjectSample] = []
    long_anime_samples: list[tuple[int, int]] = []
    movie_samples: list[SubjectSample] = []
    if "J" in selected or "N" in selected:
        print("\n[1b/5] 抽样有续集关联的 subject...")
        sequel_samples = sample_subjects_with_sequels(DB_PATH, args.limit)
        print(f"  抽样完成: {len(sequel_samples)} 条有续集的 subject")
    if "K" in selected or "O" in selected:
        print("\n[1c/5] 抽样有 episode 的 subject...")
        episode_samples = sample_subjects_with_episodes(DB_PATH, args.limit)
        print(f"  抽样完成: {len(episode_samples)} 条有 episode 的 subject")
    if "L" in selected:
        print("\n[1d/5] 抽样单季动画（无续集关联）...")
        single_season_samples = sample_single_season_anime(DB_PATH, args.limit)
        print(f"  抽样完成: {len(single_season_samples)} 条单季动画")
    if "O" in selected:
        print("\n[1e/5] 抽样长篇动画（集数 >= 100）...")
        long_anime_samples = sample_long_anime_with_episodes(DB_PATH, args.limit)
        print(f"  抽样完成: {len(long_anime_samples)} 条长篇动画")
    if "P" in selected:
        print("\n[1f/5] 抽样电影类型（type=6）...")
        movie_samples = sample_movies(DB_PATH, args.limit)
        print(f"  抽样完成: {len(movie_samples)} 条电影")

    # 2. 生成用例
    print("\n[2/5] 生成查询用例变体...")
    cases = generate_cases(samples, selected)
    cases += generate_season_cases(sequel_samples, selected)
    cases += generate_episode_cases(episode_samples, selected)
    cases += generate_single_season_cases(single_season_samples, selected)
    cases += generate_season_suffix_cases(samples, selected)
    cases += generate_cross_season_cases(sequel_samples, selected, DB_PATH)
    cases += generate_long_anime_cases(long_anime_samples, selected)
    cases += generate_movie_cases(movie_samples, selected)
    print(f"  生成 {len(cases)} 个用例")

    # 3. 初始化 Archive
    print("\n[3/5] 初始化 Archive 短路...")
    build_start = time.perf_counter()
    shortcut = setup_archive()
    build_time = time.perf_counter() - build_start

    # 批量模式：预加载 subject 到内存
    subject_cache: dict[int, SubjectCacheEntry] = {}
    if args.batch:
        print("\n[3b/5] 预加载 subject 到内存（批量优化）...")
        subject_cache, cache_dur = preload_subject_cache(DB_PATH)
        build_time += cache_dur
        print(
            f"  预加载完成: {len(subject_cache)} 条 subject, "
            f"耗时 {cache_dur:.2f}s, "
            f"内存约 {len(subject_cache) * 0.0001:.0f}MB"
        )

    # 4. 执行测试
    mode_label = "批量" if args.batch else "逐条"
    print(f"\n[4/5] 执行测试（{mode_label}模式）...")
    test_start = time.perf_counter()
    if args.batch and subject_cache:
        stat_list, failures = run_cases_batch(shortcut, cases, subject_cache)
    else:
        stat_list, failures = run_cases(shortcut, cases)
    test_dur = time.perf_counter() - test_start

    # 5. 报告
    print("\n[5/5] 生成报告...")
    subject_count = bangumi_archive.get_meta().row_counts.get("subject", 0)
    print_report(stat_list, failures, len(cases), subject_count, build_time)

    print(f"\n总执行耗时: {test_dur:.2f}s (含 {len(cases)} 个用例)")
    print(f"平均每用例: {test_dur * 1000 / len(cases):.2f}ms")


if __name__ == "__main__":
    main()
