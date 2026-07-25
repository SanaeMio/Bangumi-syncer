"""bangumi_constants 常量测试"""

from app.utils.bangumi_constants import (
    ANIME_PLATFORMS,
    COLLECTION_TYPE_DOING,
    COLLECTION_TYPE_DONE,
    COLLECTION_TYPE_DROPPED,
    COLLECTION_TYPE_ON_HOLD,
    COLLECTION_TYPE_WISH,
    EPISODE_TYPE_NORMAL,
    PLATFORM_ANIME_MOVIE,
    PLATFORM_ANIME_OVA,
    PLATFORM_ANIME_TV,
    PLATFORM_REAL_JP,
    PLATFORM_REAL_MOVIE,
    PLATFORM_REAL_TV,
    REAL_PLATFORMS,
    RELATION_CN_TO_ID,
    RELATION_ID_PARENT_STORY,
    RELATION_ID_PREQUEL,
    RELATION_ID_SEQUEL,
    RELATIONS,
    SUBJECT_TYPE_ANIME,
    SUBJECT_TYPE_REAL,
)


class TestSubjectTypes:
    def test_anime_type(self):
        assert SUBJECT_TYPE_ANIME == 2

    def test_real_type(self):
        assert SUBJECT_TYPE_REAL == 6


class TestCollectionTypes:
    def test_collection_type_values(self):
        assert COLLECTION_TYPE_WISH == 1
        assert COLLECTION_TYPE_DONE == 2
        assert COLLECTION_TYPE_DOING == 3
        assert COLLECTION_TYPE_ON_HOLD == 4
        assert COLLECTION_TYPE_DROPPED == 5


class TestEpisodeTypes:
    def test_normal_episode(self):
        assert EPISODE_TYPE_NORMAL == 0


class TestRelations:
    def test_id_constant_as_dict_key(self):
        """ID 常量可直接作为 RELATIONS 的 key 查询"""
        assert RELATIONS[RELATION_ID_SEQUEL] == "续集"
        assert RELATIONS[RELATION_ID_PREQUEL] == "前传"
        assert RELATIONS[RELATION_ID_PARENT_STORY] == "主线故事"

    def test_reverse_lookup_consistency(self):
        """反向查找表与正向表一致"""
        assert RELATION_CN_TO_ID["续集"] == RELATION_ID_SEQUEL
        assert RELATION_CN_TO_ID["前传"] == RELATION_ID_PREQUEL
        assert RELATION_CN_TO_ID["主线故事"] == RELATION_ID_PARENT_STORY
        assert len(RELATION_CN_TO_ID) == len(RELATIONS)

    def test_all_relations_have_cn_name(self):
        """所有关联条目都有中文名"""
        for rid, cn in RELATIONS.items():
            assert isinstance(rid, int)
            assert isinstance(cn, str)
            assert cn != ""


class TestPlatforms:
    def test_anime_platforms(self):
        assert ANIME_PLATFORMS[PLATFORM_ANIME_TV] == "TV"
        assert ANIME_PLATFORMS[PLATFORM_ANIME_OVA] == "OVA"
        assert ANIME_PLATFORMS[PLATFORM_ANIME_MOVIE] == "剧场版"

    def test_real_platforms(self):
        assert REAL_PLATFORMS[PLATFORM_REAL_JP] == "日剧"
        assert REAL_PLATFORMS[PLATFORM_REAL_TV] == "电视剧"
        assert REAL_PLATFORMS[PLATFORM_REAL_MOVIE] == "电影"

    def test_unknown_platform(self):
        assert 9999 not in ANIME_PLATFORMS
        assert 9999 not in REAL_PLATFORMS
