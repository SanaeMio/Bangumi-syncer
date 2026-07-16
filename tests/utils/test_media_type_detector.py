"""媒体类型检测器测试

覆盖各驱动场景下的 OVA/OAD/三次元检测，
测试数据基于实际驱动的字段来源调研。
"""

from app.utils.media_type_detector import detect_media_type


class TestDetectMediaTypeBasic:
    """基础检测逻辑测试"""

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
        assert (
            detect_media_type(title="A Film") == "movie"
        )  # Film 关键词命中，统一返回 movie


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
    """三次元（日剧/真人版）检测测试"""

    def test_jdrama_keyword_in_title(self):
        """标题含日剧关键词 → real_action"""
        assert detect_media_type(title="某日剧 第一季") == "real_action"
        assert detect_media_type(title="某日劇") == "real_action"

    def test_live_action_keyword_in_title(self):
        """标题含真人版 → real_action"""
        assert detect_media_type(title="某番剧 真人版") == "real_action"
        assert detect_media_type(title="真人版 ある物語") == "real_action"

    def test_drama_keyword_in_title(self):
        """标题含 Drama → real_action"""
        assert detect_media_type(title="My Drama Series") == "real_action"
        assert detect_media_type(title="Jdrama 2024") == "real_action"

    def test_real_action_takes_priority_over_movie(self):
        """三次元优先于电影（真人电影也应走三次元搜索）"""
        assert detect_media_type(title="真人版 电影") == "real_action"
        assert detect_media_type(title="日剧 剧场版") == "real_action"

    def test_real_action_in_item_type(self):
        """item_type 含 drama → real_action"""
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

    def test_title_keyword_overrides_item_type(self):
        """标题关键词优先于 item_type 的 movie/episode 分类

        设计原则：媒体服务器（Plex/Emby）只知道 episode/movie，
        不知道 OVA/OAD/三次元，因此标题中的类型关键词优先。
        """
        assert detect_media_type(title="剧场版", item_type="Episode") == "movie"
        assert detect_media_type(title="普通标题", item_type="Movie") == "movie"


class TestDetectMediaTypePriority:
    """检测优先级测试"""

    def test_real_action_over_movie(self):
        """三次元优先于电影"""
        assert detect_media_type(title="日剧 电影版") == "real_action"

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

    def test_feiniu_real_action_from_title(self):
        """飞牛三次元场景：标题含日剧"""
        assert (
            detect_media_type(
                title="某日剧 第一季",
                item_type="Series",
            )
            == "real_action"
        )

    def test_jellyfin_real_action_from_media_type(self):
        """Jellyfin 三次元场景：media_type=real_action（上游预格式化）"""
        # Jellyfin 驱动直接从请求体读 media_type，不经 detect_media_type
        # 但 test_match API 会校验 media_type
        # 此测试验证 detect_media_type 对 item_type=real_action 的处理
        assert detect_media_type(item_type="real_action") == "real_action"
