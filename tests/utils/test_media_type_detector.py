"""媒体类型检测器测试

覆盖两件事（2026-09 重构后的职责划分）：
1. ``detect_media_type`` —— **候选侧**：从标题/URL 判断**条目**是电影/OVA/剧集。
   **不再返回 real_action**（实测「真人快打」等动画被误判，且会收窄搜索范围）。
2. ``normalize_source_media_type`` —— **请求侧**：把**媒体源声明的类型**
   映射为 movie/episode/real_action，源的声明是权威信息，不被标题覆盖。

三次元改由配置 ``sync.enable_real_action`` 控制搜索范围。
"""

from app.utils.media_type_detector import (
    detect_media_type,
    normalize_source_media_type,
)


class TestNormalizeSourceMediaType:
    """请求侧：采信媒体源的权威声明"""

    def test_movie_declarations(self):
        for t in ("Movie", "movie", "film", "Film", "电影"):
            assert normalize_source_media_type(t) == "movie", t

    def test_episode_declarations(self):
        for t in ("Episode", "episode", "Series", "TV", "show", "剧集", "动漫", "番剧"):
            assert normalize_source_media_type(t) == "episode", t

    def test_real_action_declarations(self):
        """源显式声明三次元时保留（如上游已判定 real_action / 日剧）"""
        for t in ("real_action", "drama", "jdrama", "日剧", "真人"):
            assert normalize_source_media_type(t) == "real_action", t

    def test_ova_oad_declarations(self):
        assert normalize_source_media_type("OVA") == "ova"
        assert normalize_source_media_type("OAD") == "oad"

    def test_unknown_returns_none(self):
        """无法识别时返回 None，由调用方兜底（不猜测）"""
        assert normalize_source_media_type("") is None
        assert normalize_source_media_type("   ") is None
        assert normalize_source_media_type("unknown-kind") is None

    def test_source_movie_not_overridden_by_title_keyword(self):
        """核心修复：源的 movie 声明**不再**被标题关键词覆盖

        历史缺陷：``item_type='movie'`` + 标题含「特别篇」会被改判为 ova。
        请求侧现在直接采信源，标题不参与判定。
        """
        # 请求侧不再接受 title —— 该函数只做源声明的规范化
        assert normalize_source_media_type("movie") == "movie"


class TestDetectMediaTypeBasic:
    """候选侧基础检测逻辑"""

    def test_empty_inputs_returns_episode(self):
        """空输入返回 episode"""
        assert detect_media_type() == "episode"
        assert (
            detect_media_type(title="", ori_title="", url="", artist="", item_type="")
            == "episode"
        )

    def test_plain_title_returns_episode(self):
        """普通标题返回 episode"""
        assert detect_media_type(title="某番剧") == "episode"
        assert detect_media_type(title="My Anime") == "episode"

    def test_movie_keyword_in_title(self):
        """标题含剧场版关键词 → movie"""
        assert detect_media_type(title="某番剧 剧场版") == "movie"
        assert detect_media_type(title="劇場版 あるアニメ") == "movie"
        assert detect_media_type(title="某电影") == "movie"
        assert detect_media_type(title="電影標題") == "movie"
        assert detect_media_type(title="My Movie") == "movie"
        assert detect_media_type(title="A Film") == "movie"

    def test_no_real_action_from_title_keywords(self):
        """候选侧**不再**从标题关键词推断 real_action

        实测误判：``真人快打``（Mortal Kombat Legends）是动画，
        ``机动战士高达 第08MS小队 三次元的战斗`` 同理。
        这些现在落到默认 episode，交由 platform/type 字段判定。
        """
        assert detect_media_type(title="真人快打传奇：蝎子的复仇") == "episode"
        assert (
            detect_media_type(title="机动战士高达 第08MS小队 三次元的战斗") == "episode"
        )
        assert detect_media_type(title="某日剧 第一季") == "episode"


class TestDetectMediaTypeOVA:
    """OVA 检测测试"""

    def test_ova_keyword_in_title(self):
        """标题含 OVA → ova"""
        assert detect_media_type(title="某番剧 OVA") == "ova"
        assert detect_media_type(title="OVA あるアニメ") == "ova"

    def test_ova_keyword_in_url(self):
        """URL 含 OVA → ova（fongmi 场景）"""
        assert detect_media_type(title="某番剧", url="/video/ova/episode1.mp4") == "ova"

    def test_ova_keyword_in_ori_title(self):
        """原始标题含 OVA → ova"""
        assert detect_media_type(title="某番剧", ori_title="Anime OVA") == "ova"

    def test_special_episode_treated_as_ova(self):
        """特别篇 → ova"""
        assert detect_media_type(title="某番剧 特别篇") == "ova"
        assert detect_media_type(title="某番剧 特別篇") == "ova"


class TestDetectMediaTypeOAD:
    """OAD 检测测试"""

    def test_oad_keyword_in_title(self):
        """标题含 OAD → oad"""
        assert detect_media_type(title="某番剧 OAD") == "oad"
        assert detect_media_type(title="OAD あるアニメ") == "oad"

    def test_oad_keyword_in_url(self):
        """URL 含 OAD → oad（fongmi 场景）"""
        assert detect_media_type(title="某番剧", url="/video/oad/bonus.mp4") == "oad"


class TestDetectMediaTypeRealAction:
    """三次元检测：候选侧**不再**从标题推断，只认 item_type 显式声明"""

    def test_title_keywords_no_longer_produce_real_action(self):
        """标题含日剧/真人/Drama **不再**判 real_action

        这是本次重构的核心变更：实测误判（真人快打 = 动画）且
        real_action 会收窄搜索范围到 type=6，导致漏标。
        这些标题现在落到 episode 或 movie（若含剧场版/电影关键词），
        三次元由候选侧 type 字段判定。
        """
        # 纯三次元关键词 → 落回默认 episode
        for title in (
            "某日剧 第一季",
            "某日劇",
            "某番剧 真人版",
            "真人版 ある物語",
            "My Drama Series",
            "Jdrama 2024",
        ):
            assert detect_media_type(title=title) == "episode", title

        # 同时含电影关键词时判 movie（不再是 real_action）—— 这是行为变更点
        assert detect_media_type(title="真人版 电影") == "movie"
        assert detect_media_type(title="日剧 剧场版") == "movie"

    def test_real_action_in_item_type(self):
        """item_type 显式声明三次元 → real_action（源权威，保留）"""
        assert detect_media_type(item_type="drama") == "real_action"
        assert detect_media_type(item_type="Jdrama") == "real_action"
        assert detect_media_type(item_type="日剧") == "real_action"


class TestDetectMediaTypeItemType:
    """item_type 字段检测测试（飞牛场景）"""

    def test_item_type_movie(self):
        """item_type=Movie → movie"""
        assert detect_media_type(item_type="Movie") == "movie"
        assert detect_media_type(item_type="film") == "movie"
        assert detect_media_type(item_type="电影") == "movie"

    def test_item_type_episode(self):
        """item_type=Episode → episode"""
        assert detect_media_type(item_type="Episode") == "episode"
        assert detect_media_type(item_type="Series") == "episode"
        assert detect_media_type(item_type="TV") == "episode"
        assert detect_media_type(item_type="动漫") == "episode"
        assert detect_media_type(item_type="番剧") == "episode"

    def test_item_type_ova(self):
        """item_type=OVA → ova"""
        assert detect_media_type(item_type="OVA") == "ova"

    def test_item_type_oad(self):
        """item_type=OAD → oad"""
        assert detect_media_type(item_type="OAD") == "oad"

    def test_title_keyword_refines_item_type(self):
        """候选侧：标题关键词可**细化** item_type

        与请求侧不同 —— 候选侧没有可靠的类型字段（bangumi-data 无 platform），
        故保留「标题更具体时优先」的语义。
        """
        assert detect_media_type(title="剧场版", item_type="Episode") == "movie"
        assert detect_media_type(title="普通标题", item_type="Movie") == "movie"


class TestDetectMediaTypePriority:
    """检测优先级测试"""

    def test_ova_over_movie(self):
        """OVA 优先于电影（OVA 是更具体的动画子类型）

        "剧场版 OVA" 这种组合罕见，实际中 OVA 标记意味着走集数同步，
        比走 movie 单集标记在看更安全。
        """
        assert detect_media_type(title="剧场版 OVA") == "ova"

    def test_ova_over_oad(self):
        """OVA 优先于 OAD（同时存在时）"""
        assert detect_media_type(title="某番剧 OVA OAD") == "ova"


class TestDriverSpecificScenarios:
    """基于实际驱动数据的场景测试"""

    def test_plex_episode_normal(self):
        """Plex 剧集场景：grandparentTitle 为标题"""
        assert (
            detect_media_type(title="鬼滅の刃", ori_title="Demon Slayer") == "episode"
        )

    def test_plex_movie_normal(self):
        """Plex 电影场景：title 含剧场版"""
        assert (
            detect_media_type(
                title="鬼滅の刃 無限列車編", ori_title="Demon Slayer Movie"
            )
            == "movie"
        )

    def test_emby_episode_with_original_title(self):
        """Emby 剧集场景：SeriesName + OriginalTitle"""
        assert (
            detect_media_type(
                title="進撃の巨人",
                ori_title="第3話 サブタイトル",
                item_type="Episode",
            )
            == "episode"
        )

    def test_fongmi_movie_from_url(self):
        """Fongmi 电影场景：URL 含剧场版关键词"""
        assert (
            detect_media_type(
                title="あるアニメ",
                url="/storage/剧场版/movie.mp4",
                artist="某个艺术家",
            )
            == "movie"
        )

    def test_fongmi_ova_from_url(self):
        """Fongmi OVA 场景：URL 含 OVA"""
        assert (
            detect_media_type(
                title="あるアニメ",
                url="/storage/OVA/special.mp4",
            )
            == "ova"
        )

    def test_feiniu_movie_from_item_type(self):
        """飞牛电影场景：item_type=Movie"""
        assert (
            detect_media_type(
                title="某剧场版",
                item_type="Movie",
            )
            == "movie"
        )

    def test_feiniu_real_action_requires_item_type(self):
        """飞牛三次元：**仅当 item_type 显式声明**才判 real_action

        标题含「日剧」不再触发（实测误判面）。飞牛的 item_type 由源提供，
        故正常路径下该判定仍然生效（见 TestNormalizeSourceMediaType）。
        """
        # 标题含日剧但 item_type 只是 Series → 不再判 real_action
        assert detect_media_type(title="某日剧 第一季", item_type="Series") == "episode"
        # item_type 显式声明三次元 → 保留
        assert detect_media_type(item_type="日剧") == "real_action"

    def test_jellyfin_real_action_from_media_type(self):
        """Jellyfin 三次元场景：media_type=real_action（上游预格式化）"""
        # Jellyfin 驱动直接从请求体读 media_type，不经 detect_media_type
        # 但 test_match API 会校验 media_type
        # 此测试验证 detect_media_type 对 item_type=real_action 的处理
        assert detect_media_type(item_type="real_action") == "real_action"
