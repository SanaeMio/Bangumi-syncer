"""detect_media_type 真实数据快照测试

数据快照取自 Bangumi API 真实条目（2026-07-20 拉取），硬编码到本测试中，
不依赖网络访问。覆盖 detect_media_type 在各种现实场景下的判定：
- 国漫分季 + 剧场版 + 衍生短番（完美世界、斗罗大陆、凡人修仙传）
- 日漫剧场版 + TV 系列（鬼灭之刃、咒术回战、名侦探柯南、火影忍者）
- OVA / OAD / 特别篇（进击的巨人、斗破苍穹）
- 真人版 / 日剧（孤独的美食家）
- 已知限制：platform=剧场版 但标题无「剧场版」关键词 → detect=episode（xfail）
"""

from __future__ import annotations

import pytest

from app.utils.media_type_detector import detect_media_type

# ============================================================
# 真实数据快照（来自 Bangumi API /v0/subjects/{id}）
# 字段：id / name / name_cn / platform / eps
# ============================================================

# 完美世界 搜索结果前 5 条
_PERFECT_WORLD: list[dict] = [
    {
        "id": 542046,
        "name": "完美世界剧场版 九劫焚天",
        "name_cn": "完美世界剧场版 九劫焚天",
        "platform": "WEB",
        "eps": 1,
    },
    {
        "id": 175141,
        "name": "完美世界双食记",
        "name_cn": "完美世界双食记",
        "platform": "WEB",
        "eps": 6,
    },
    {
        "id": 403251,
        "name": "完美世界 第三季",
        "name_cn": "完美世界 第三季",
        "platform": "WEB",
        "eps": 52,
    },
    {
        "id": 345811,
        "name": "完美世界 第二季",
        "name_cn": "完美世界 第二季",
        "platform": "WEB",
        "eps": 52,
    },
    {
        "id": 449355,
        "name": "完美世界 第四季",
        "name_cn": "完美世界 第四季",
        "platform": "WEB",
        "eps": 52,
    },
]

# 斗罗大陆 搜索结果
_DOULUO: list[dict] = [
    {
        "id": 199425,
        "name": "斗罗大陆",
        "name_cn": "斗罗大陆",
        "platform": "WEB",
        "eps": 26,
    },
    {
        "id": 345803,
        "name": "斗罗大陆Ⅱ绝世唐门",
        "name_cn": "斗罗大陆2绝世唐门",
        "platform": "WEB",
        "eps": 182,
    },
    {
        "id": 294773,
        "name": "斗罗大陆 再聚首",
        "name_cn": "斗罗大陆 第三季 再聚首",
        "platform": "WEB",
        "eps": 52,
    },
    {
        "id": 252950,
        "name": "斗罗大陆 精英赛",
        "name_cn": "斗罗大陆 第二季 精英赛",
        "platform": "WEB",
        "eps": 52,
    },
    {
        "id": 307088,
        "name": "斗罗大陆 海神岛",
        "name_cn": "斗罗大陆 第四季 海神岛",
        "platform": "WEB",
        "eps": 52,
    },
    {
        "id": 356976,
        "name": "斗罗大陆 第五季",
        "name_cn": "斗罗大陆 第五季",
        "platform": "WEB",
        "eps": 52,
    },
    {
        "id": 407787,
        "name": "斗罗大陆 第六季",
        "name_cn": "斗罗大陆 第六季",
        "platform": "WEB",
        "eps": 27,
    },
]

# 斗破苍穹 - 特别篇
_DOUPO: list[dict] = [
    {
        "id": 223143,
        "name": "斗破苍穹 特别篇",
        "name_cn": "斗破苍穹 特别篇",
        "platform": "WEB",
        "eps": 2,
    },
]

# 凡人修仙传 - 特别篇 + 剧场版（无关键词）
_FANREN: list[dict] = [
    {
        "id": 320242,
        "name": "凡人修仙传 特别篇：燕家堡之战",
        "name_cn": "凡人修仙传 特别篇：燕家堡之战",
        "platform": "WEB",
        "eps": 4,
    },
    {
        "id": 553577,
        "name": "凡人修仙传 瀚海迷踪",
        "name_cn": "凡人修仙传 瀚海迷踪",
        "platform": "剧场版",
        "eps": 1,
    },
]

# 鬼灭之刃 - TV 主线 + 剧场版（无关键词）
_KIMETSU: list[dict] = [
    {
        "id": 245665,
        "name": "鬼滅の刃",
        "name_cn": "鬼灭之刃",
        "platform": "TV",
        "eps": 26,
    },
    {
        "id": 294137,
        "name": "鬼滅の刃 兄妹の絆",
        "name_cn": "鬼灭之刃 兄妹的羁绊",
        "platform": "剧场版",
        "eps": 1,
    },
]

# 进击的巨人 - OAD
_SHINGEKI: list[dict] = [
    {
        "id": 55770,
        "name": "進撃の巨人",
        "name_cn": "进击的巨人",
        "platform": "TV",
        "eps": 25,
    },
    {
        "id": 110049,
        "name": "進撃の巨人 悔いなき選択 OAD",
        "name_cn": "进击的巨人 无悔的选择 OAD",
        "platform": "OVA",
        "eps": 2,
    },
]

# 咒术回战 - 剧场版（含关键词） + 总集篇（无关键词）
_JUJUTSU: list[dict] = [
    {
        "id": 294993,
        "name": "呪術廻戦",
        "name_cn": "咒术回战",
        "platform": "TV",
        "eps": 24,
    },
    {
        "id": 582014,
        "name": "劇場版 呪術廻戦『渋谷事変 特別編集版』×『死滅回游 先行上映』",
        "name_cn": "剧场版 咒术回战《涩谷事变 特别剪辑版》×《死灭回游 先行上映》",
        "platform": "剧场版",
        "eps": 1,
    },
    {
        "id": 509599,
        "name": "呪術廻戦 懐玉・玉折 総集編",
        "name_cn": "咒术回战 怀玉·玉折 总集篇",
        "platform": "剧场版",
        "eps": 1,
    },
]

# 名侦探柯南 - 剧场版（无关键词）
_CONAN: list[dict] = [
    {
        "id": 899,
        "name": "名探偵コナン",
        "name_cn": "名侦探柯南",
        "platform": "TV",
        "eps": 0,
    },
    {
        "id": 2970,
        "name": "名探偵コナン 世紀末の魔術師",
        "name_cn": "名侦探柯南 世纪末的魔术师",
        "platform": "剧场版",
        "eps": 1,
    },
]

# 火影忍者 - 剧场版（含关键词）
_NARUTO: list[dict] = [
    {
        "id": 3425,
        "name": "NARUTO -ナルト-",
        "name_cn": "火影忍者",
        "platform": "TV",
        "eps": 220,
    },
    {
        "id": 22107,
        "name": "劇場版 NARUTO -ナルト- 大活劇! 雪姫忍法帖だってばよ!!",
        "name_cn": "火影忍者剧场版 大活剧！雪姬忍法帖！！",
        "platform": "剧场版",
        "eps": 1,
    },
]

# 海贼王 - 剧场版（无关键词）
_ONEPIECE: list[dict] = [
    {
        "id": 162049,
        "name": "ONE PIECE めざせ!海賊野球王",
        "name_cn": "航海王：目标！海贼棒球王",
        "platform": "剧场版",
        "eps": 1,
    },
]

# 孤独的美食家 - 主线 + 特别篇
_KODOKU: list[dict] = [
    {
        "id": 30619,
        "name": "孤独のグルメ",
        "name_cn": "孤独的美食家",
        "platform": "日剧",
        "eps": 12,
    },
    {
        "id": 467925,
        "name": "孤独のグルメ2023大晦日スペシャル 井之頭五郎、南へ逃避行「探さないでください。」",
        "name_cn": "孤独的美食家2023除夕特别篇 井之头五郎，往南逃跑“不要找我了。”",
        "platform": "日剧",
        "eps": 1,
    },
]


def _detect(info: dict) -> str:
    """对单条候选运行 detect_media_type（仅传 name/name_cn，模拟搜索结果场景）"""
    return detect_media_type(
        title=info.get("name_cn", ""),
        ori_title=info.get("name", ""),
    )


def _find_by_id(candidates: list[dict], sid: int) -> dict:
    for c in candidates:
        if c.get("id") == sid:
            return c
    return {}


# ============================================================
# 国漫：剧场版关键词命中 → movie
# ============================================================


class TestGuomanMovieKeywordDetection:
    """国漫场景：标题含「剧场版」关键词 → movie"""

    def test_perfect_world_movie_keyword_detected(self):
        """完美世界剧场版 九劫焚天（标题含「剧场版」）→ movie

        注：国漫剧场版 platform 字段常为「WEB」（网络放送），不是「剧场版」。
        Bangumi 的 platform 字段对国漫不严格，detect_media_type 仅靠标题判定更可靠。
        """
        info = _find_by_id(_PERFECT_WORLD, 542046)
        assert info, "未找到 542046 完美世界剧场版 九劫焚天"
        assert _detect(info) == "movie"

    def test_perfect_world_mainline_seasons_detected_as_episode(self):
        """完美世界 第二/三/四季（主线剧集）→ episode"""
        for sid in (345811, 403251, 449355):
            info = _find_by_id(_PERFECT_WORLD, sid)
            assert info, f"未找到 {sid}"
            assert _detect(info) == "episode", (
                f"{sid} {info.get('name_cn')} 应为 episode，实际 {_detect(info)}"
            )

    def test_perfect_world_derivative_short_series_detected_as_episode(self):
        """完美世界双食记（6 集衍生短番，detect=episode 但 eps 小）

        这是当前 detect_media_type 的已知特性：仅靠标题无法区分主线剧集 vs 衍生短番，
        需要在上层 _pick_mainline_episode_candidate 中按 eps/季番声明择优。
        """
        info = _find_by_id(_PERFECT_WORLD, 175141)
        assert info, "未找到 175141 完美世界双食记"
        assert _detect(info) == "episode"
        assert int(info.get("eps") or 0) == 6  # 衍生短番特征：eps 少


# ============================================================
# OVA / OAD / 特别篇 关键词检测
# ============================================================


class TestOvaOadSpecialDetection:
    """OVA / OAD / 特别篇 关键词检测"""

    def test_oad_keyword_in_title(self):
        """进击的巨人 悔いなき選択 OAD（标题含「OAD」）→ oad"""
        info = _find_by_id(_SHINGEKI, 110049)
        assert info, "未找到 110049 进击的巨人 悔いなき選択 OAD"
        assert _detect(info) == "oad"

    def test_special_keyword_chinese(self):
        """斗破苍穹 特别篇（标题含「特别篇」）→ ova"""
        info = _find_by_id(_DOUPO, 223143)
        assert info, "未找到 223143 斗破苍穹 特别篇"
        assert _detect(info) == "ova"

    def test_special_keyword_in_name_cn(self):
        """凡人修仙传 特别篇：燕家堡之战（name_cn 含「特别篇」）→ ova"""
        info = _find_by_id(_FANREN, 320242)
        assert info, "未找到 320242 凡人修仙传 特别篇：燕家堡之战"
        assert _detect(info) == "ova"

    def test_jp_special_in_name_cn(self):
        """孤独的美食家 2023 除夕特别篇（name_cn 含「特别篇」）→ ova

        日剧场景下的「除夕特别篇」也命中 ova 关键词。
        """
        info = _find_by_id(_KODOKU, 467925)
        assert info, "未找到 467925 孤独的美食家 2023 除夕特别篇"
        assert _detect(info) == "ova"


# ============================================================
# 真人版 / 日剧 关键词检测
# ============================================================


class TestRealActionDetection:
    """三次元 / 日剧 / 真人版 检测"""

    def test_lonely_gourmet_main_series(self):
        """孤独的美食家 主线剧集（日剧 platform）→ episode

        标题不含「日剧/真人版」关键词，detect=episode；
        platform=日剧 由上层 subject_types=[6] 搜索范围控制。
        """
        info = _find_by_id(_KODOKU, 30619)
        assert info, "未找到 30619 孤独的美食家"
        assert _detect(info) == "episode"
        assert info.get("platform") == "日剧"


# ============================================================
# 已知限制：platform=剧场版 但标题无「剧场版」关键词 → detect=episode
# ============================================================


class TestPlatformMovieWithoutKeyword:
    """已知限制：platform=剧场版 但标题不含「剧场版/劇場版/电影」关键词

    detect_media_type 仅扫描标题/原标题等文本字段，不读取 platform。
    当剧场版条目标题不含「剧场版」字样时（如名侦探柯南系列剧场版），
    当前实现会判为 episode，这是已知限制。

    改进方向：让 detect_media_type 接受 platform 参数，
    当 platform 为「剧场版/电影」且 eps<=2 时判为 movie。
    本测试组用 xfail 标记期望行为，待改进后转为正向断言。
    """

    @pytest.mark.xfail(
        reason="detect_media_type 不读 platform，标题无「剧场版」关键词时漏判",
        strict=True,
    )
    def test_conan_movie_without_keyword(self):
        """名侦探柯南 世纪末的魔术师（platform=剧场版，标题无关键词）应判为 movie"""
        info = _find_by_id(_CONAN, 2970)
        assert info, "未找到 2970 名侦探柯南 世纪末的魔术师"
        assert info.get("platform") == "剧场版"
        assert _detect(info) == "movie"

    @pytest.mark.xfail(
        reason="detect_media_type 不读 platform，标题无「剧场版」关键词时漏判",
        strict=True,
    )
    def test_demon_slayer_movie_brother_sister_bond(self):
        """鬼灭之刃 兄妹的羁绊（platform=剧场版，标题无关键词）应判为 movie"""
        info = _find_by_id(_KIMETSU, 294137)
        assert info, "未找到 294137 鬼灭之刃 兄妹的羁绊"
        assert info.get("platform") == "剧场版"
        assert _detect(info) == "movie"

    @pytest.mark.xfail(
        reason="detect_media_type 不读 platform，标题无「剧场版」关键词时漏判",
        strict=True,
    )
    def test_jujutsu_kaisen_movie_compilation(self):
        """咒术回战 怀玉·玉折 总集篇（platform=剧场版，标题无关键词）应判为 movie"""
        info = _find_by_id(_JUJUTSU, 509599)
        assert info, "未找到 509599 咒术回战 怀玉·玉折 总集篇"
        assert info.get("platform") == "剧场版"
        assert _detect(info) == "movie"

    @pytest.mark.xfail(
        reason="detect_media_type 不读 platform，标题无「剧场版」关键词时漏判",
        strict=True,
    )
    def test_one_piece_movie_baseball(self):
        """航海王：目标！海贼棒球王（platform=剧场版，标题无关键词）应判为 movie"""
        info = _find_by_id(_ONEPIECE, 162049)
        assert info, "未找到 162049 航海王：目标！海贼棒球王"
        assert info.get("platform") == "剧场版"
        assert _detect(info) == "movie"

    @pytest.mark.xfail(
        reason="detect_media_type 不读 platform，标题无「剧场版」关键词时漏判",
        strict=True,
    )
    def test_fanren_movie_hanhai_mizong(self):
        """凡人修仙传 瀚海迷踪（platform=剧场版，标题无关键词）应判为 movie"""
        info = _find_by_id(_FANREN, 553577)
        assert info, "未找到 553577 凡人修仙传 瀚海迷踪"
        assert info.get("platform") == "剧场版"
        assert _detect(info) == "movie"


# ============================================================
# 剧场版关键词正向命中（标题含「剧场版/劇場版」）
# ============================================================


class TestMovieKeywordPositive:
    """标题明确含「剧场版/劇場版」关键词 → movie（正向）"""

    def test_jujutsu_kaisen_movie_with_keyword(self):
        """劇場版 呪術廻戦（标题含「劇場版」）→ movie"""
        info = _find_by_id(_JUJUTSU, 582014)
        assert info, "未找到 582014 劇場版 咒术回战"
        assert _detect(info) == "movie"

    def test_naruto_movie_with_keyword(self):
        """劇場版 NARUTO 大活剧！雪姬忍法帖（标题含「劇場版」）→ movie"""
        info = _find_by_id(_NARUTO, 22107)
        assert info, "未找到 22107 劇場版 NARUTO"
        assert _detect(info) == "movie"


# ============================================================
# 主线剧集 / TV 系列 正向判定
# ============================================================


class TestMainlineEpisodeDetection:
    """主线剧集 / TV 系列 → episode"""

    def test_demon_slayer_main_series(self):
        """鬼灭之刃 TV 主线 → episode"""
        info = _find_by_id(_KIMETSU, 245665)
        assert info, "未找到 245665 鬼灭之刃"
        assert _detect(info) == "episode"
        assert info.get("platform") == "TV"

    def test_douluo_mainline_seasons(self):
        """斗罗大陆各季主线剧集 → episode"""
        for sid in (199425, 345803, 294773, 252950, 307088, 356976, 407787):
            info = _find_by_id(_DOULUO, sid)
            assert info, f"未找到 {sid}"
            assert _detect(info) == "episode", (
                f"{sid} {info.get('name_cn')} 应为 episode，实际 {_detect(info)}"
            )

    def test_shingeki_main_series(self):
        """进击的巨人 TV 主线 → episode"""
        info = _find_by_id(_SHINGEKI, 55770)
        assert info, "未找到 55770 进击的巨人"
        assert _detect(info) == "episode"
        assert info.get("platform") == "TV"

    def test_conan_main_series(self):
        """名侦探柯南 TV 主线 → episode"""
        info = _find_by_id(_CONAN, 899)
        assert info, "未找到 899 名侦探柯南"
        assert _detect(info) == "episode"
        assert info.get("platform") == "TV"

    def test_jujutsu_main_series(self):
        """咒术回战 TV 主线 → episode"""
        info = _find_by_id(_JUJUTSU, 294993)
        assert info, "未找到 294993 咒术回战"
        assert _detect(info) == "episode"
        assert info.get("platform") == "TV"

    def test_naruto_main_series(self):
        """火影忍者 TV 主线 → episode"""
        info = _find_by_id(_NARUTO, 3425)
        assert info, "未找到 3425 火影忍者"
        assert _detect(info) == "episode"
        assert info.get("platform") == "TV"


# ============================================================
# 上层择优：_pick_mainline_episode_candidate 在真实数据上的行为
# ============================================================


class TestPickMainlineEpisodeCandidateReal:
    """_pick_mainline_episode_candidate 在真实候选列表上的择优行为"""

    @staticmethod
    def _filter_episode_candidates(candidates: list[dict]) -> list[dict]:
        """模拟 _find_subject_id 中的 episode 候选过滤逻辑"""
        return [
            c
            for c in candidates
            if detect_media_type(
                title=c.get("name_cn", ""), ori_title=c.get("name", "")
            )
            == "episode"
        ]

    def test_perfect_world_picks_mainline_over_derivative(self):
        """完美世界：择优应跳过 175141 双食记（6 集）选主线剧集

        场景：请求「完美世界」S01E270，搜索首条是 542046 剧场版（detect=movie），
        episode 候选含 175141 双食记（eps=6）+ 403251 第三季（eps=52）+ 345811 第二季（eps=52）
        + 449355 第四季（eps=52）。
        _pick_mainline_episode_candidate 应选 eps 最大的主线剧集（而非 6 集的双食记）。
        """
        from app.services.sync_service import SyncService

        episode_cands = self._filter_episode_candidates(_PERFECT_WORLD)
        assert len(episode_cands) >= 4, f"episode 候选不足 4 条: {len(episode_cands)}"

        # 确认 175141 双食记在候选中
        ids = [c.get("id") for c in episode_cands]
        assert 175141 in ids, f"175141 双食记不在 episode 候选中: {ids}"

        best = SyncService._pick_mainline_episode_candidate(episode_cands, "完美世界")
        # 择优结果不应是 6 集的双食记
        assert best.get("id") != 175141, (
            f"择优错误：选中了 175141 双食记，应为 eps=52 的主线剧集，"
            f"best={best.get('name_cn')}(id={best.get('id')}, eps={best.get('eps')})"
        )
        # 择优结果的 eps 应 >= 52
        assert int(best.get("eps") or 0) >= 52

    def test_perfect_world_picks_mainline_excludes_movie(self):
        """完美世界：剧场版 542046 应被排除在 episode 候选之外"""
        episode_cands = self._filter_episode_candidates(_PERFECT_WORLD)
        ids = [c.get("id") for c in episode_cands]
        assert 542046 not in ids, "542046 剧场版不应出现在 episode 候选中"

    def test_douluo_picks_exact_title_match(self):
        """斗罗大陆：择优应选精确标题匹配的 199425 第一季本体（而非 eps=182 的第二季）

        _pick_mainline_episode_candidate 优先级：精确匹配 > 季番声明 > eps 最大。
        请求标题「斗罗大陆」精确命中 199425，即使 345803 绝世唐门 eps=182 更大也不应改选。
        """
        from app.services.sync_service import SyncService

        episode_cands = self._filter_episode_candidates(_DOULUO)
        assert len(episode_cands) >= 7
        best = SyncService._pick_mainline_episode_candidate(episode_cands, "斗罗大陆")
        # 199425 是精确标题匹配的第一季本体
        assert best.get("id") == 199425, (
            f"应选精确匹配的 199425，实际选了 {best.get('name_cn')}"
            f"(id={best.get('id')}, eps={best.get('eps')})"
        )

    def test_douluo_picks_season_keyword_when_no_exact_match(self):
        """无精确匹配时：择优应优先选含「第N季」声明的条目

        模拟请求标题「斗罗大陆 X」（无精确匹配），候选含 345803 绝世唐门（无季番声明）
        + 294773 第三季 再聚首（含「第三季」声明）。季番声明优先于 eps 最大，
        应选 294773（即使 345803 eps=182 更大）。
        """
        from app.services.sync_service import SyncService

        # 排除 199425（精确匹配「斗罗大陆」），模拟无精确匹配场景
        cands_without_exact = [c for c in _DOULUO if c.get("id") != 199425]
        episode_cands = self._filter_episode_candidates(cands_without_exact)
        best = SyncService._pick_mainline_episode_candidate(episode_cands, "斗罗大陆 X")
        # 294773 含「第三季」声明，应优先于 eps=182 但无季番声明的 345803
        assert best.get("id") == 294773, (
            f"应选含「第三季」声明的 294773，实际选了 {best.get('name_cn')}"
            f"(id={best.get('id')}, eps={best.get('eps')})"
        )
