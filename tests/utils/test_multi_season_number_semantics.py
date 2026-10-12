"""回归：_find_multi_season_episode 的季号语义

该函数沿续集链遍历，用 `season_num` 判定「走到第几季了」。需要固定的是：
`season_num` 究竟是**续集链上的位置计数器**，还是条目**自己声明的季号**。

原实现是前者：从 1 开始，每遇到一个新 subject 就 +1，且起始条目
（传入的 subject_id 本身）在循环内从未被取集。这带来两个可观测后果：

1. target_season 与「第几个 subject」绑定，而非与条目声明的季号绑定；
2. 请求的季数超出现有续集链长度时，行为取决于计数器是否恰好越过。

本文件用真实的 BangumiApi 实例（仅替换 I/O 方法）固定这些行为，
避免用 MagicMock 整体替换后把被测逻辑一起 mock 掉。
"""

from unittest.mock import patch

from app.utils.bangumi_api import BangumiApi

SUBJECTS = {
    100: {"id": 100, "type": 2, "platform": "", "name": "S1", "name_cn": "第一季"},
    200: {"id": 200, "type": 2, "platform": "", "name": "S2", "name_cn": "第二季"},
}

#: 每个条目拥有的章节：(sort, ep_id)
EPISODES = {
    100: [(1, 1001), (2, 1002)],
    200: [(1, 2001), (2, 2002)],
}


def _make_api(chain):
    """真实 BangumiApi 实例，仅替换网络相关方法"""
    api = BangumiApi(username="u", access_token="t")
    api._find_next_sequel_id = lambda sid: chain.get(sid)
    api.get_subject = lambda sid: SUBJECTS.get(sid)

    def _get_episodes(sid, _type=0, fetch_all=False):
        rows = [
            {"sort": s, "id": e, "ep": s, "type": 0, "airdate": ""}
            for s, e in EPISODES.get(sid, [])
        ]
        return {"data": rows, "total": len(rows)}

    api.get_episodes = _get_episodes
    return api


class TestSeasonNumberSemantics:
    def test_starting_subject_counts_as_first_season(self):
        """起始条目算第 1 季 → 请求第 2 季应命中续集 200"""
        api = _make_api({100: 200, 200: None})
        with patch.object(api, "_episode_lookup_failed", return_value=(None, None)):
            result = api._find_multi_season_episode(100, 2, 1, 2, "", None)
        assert result == (200, 2001)

    def test_season_beyond_chain_fails_instead_of_misreturning(self):
        """请求第 5 季但链上只有 2 个条目 → 必须失败

        不能因为计数器没走到 5 就把最后一个条目的集数当成命中返回。
        """
        api = _make_api({100: 200, 200: None})
        with patch.object(
            api, "_episode_lookup_failed", return_value=(None, None)
        ) as failed:
            result = api._find_multi_season_episode(100, 5, 1, 2, "", None)
        assert result == (None, None)
        failed.assert_called_once()

    def test_season_number_not_reset_by_missing_season_keyword(self):
        """续集条目名不含季号时仍应递增（按位置计季）

        条目名常常不含「第 N 季」，仅靠 _extract_season_number 无法计数，
        因此实现用位置计数兜底 —— 这里固定该兜底行为不被改变。
        """
        api = _make_api({100: 200, 200: None})
        # 两个条目名都不含季号
        api.get_subject = lambda sid: {
            "id": sid,
            "type": 2,
            "platform": "",
            "name": f"Show {sid}",
            "name_cn": "",
        }
        with patch.object(api, "_episode_lookup_failed", return_value=(None, None)):
            result = api._find_multi_season_episode(100, 2, 1, 2, "", None)
        assert result == (200, 2001)

    def test_position_semantics_is_intentional(self):
        """**设计说明（非缺陷）**：季号按「续集链位置」而非条目声明的季号计

        这是有意为之，而不是 bug：
        - 当 Bangumi 把一部番拆成多个独立 subject（第二季/第三季各一条目）
          而媒体库按 1、2、3… 顺序编号时，用户请求的「第 2 季」指的正是
          **链上第二个条目**，与该条目是否自称「第三季」无关；
        - 若改为比较条目声明的季号，上例会因「跳过第 2 季」而失败，
          反而破坏最常见的跨季编号场景。

        代价：条目自称的季号与链位置不一致时（如链上第二个条目其实自称
        「第三季」），按位置命中。这是有意的取舍，不是遗漏。

        用户若需要精确控制，应使用自定义映射的 segments / 季度感知格式
        显式指定条目 —— 那条路径会绕过本函数（is_season_matched_id=True）。
        """
        api = _make_api({100: 200, 200: None})
        # 第二个条目自称「第三季」，但仍被当作链上第 2 个（= 请求的 season 2）
        api.get_subject = lambda sid: {
            "id": sid,
            "type": 2,
            "platform": "",
            "name": "Show",
            "name_cn": "第三季" if sid == 200 else "第一季",
        }
        with patch.object(api, "_episode_lookup_failed", return_value=(None, None)):
            result = api._find_multi_season_episode(100, 2, 1, 2, "", None)
        assert result == (200, 2001)
