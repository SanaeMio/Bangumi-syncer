"""
Bangumi API HTTP Mock 测试
使用 unittest.mock 模拟 httpx 调用
"""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.bangumi_api import BangumiApi


def _mock_response(status_code=200, json_data=None):
    """创建模拟的 httpx 响应对象"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.headers = {}
    resp.text = ""
    resp.elapsed.total_seconds.return_value = 0.01
    resp.request = MagicMock()
    return resp


def test_search_success():
    """测试搜索成功"""
    mock_resp = _mock_response(
        200, {"data": [{"id": 1, "name": "Test Anime", "name_cn": "测试动画"}]}
    )
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi()
        result = api.search("test", "2024-01-01", "2024-12-31")

        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["name"] == "Test Anime"


def test_search_empty_result():
    """测试搜索无结果"""
    mock_resp = _mock_response(200, {"data": []})
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi()
        result = api.search("nonexistent", "2024-01-01", "2024-12-31")

        assert result == []


def test_get_subject():
    """测试获取条目详情"""
    mock_resp = _mock_response(
        200,
        {
            "id": 123,
            "name": "Test Anime",
            "name_cn": "测试动画",
            "type": 2,
            "eps": 12,
        },
    )
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi()
        result = api.get_subject(123)

        assert result["id"] == 123
        assert result["name_cn"] == "测试动画"
        assert result["eps"] == 12


def test_get_episodes():
    """测试获取剧集列表"""
    mock_resp = _mock_response(
        200,
        {
            "data": [
                {"id": 1, "ep": 1, "sort": 1, "name": "第1话"},
                {"id": 2, "ep": 2, "sort": 2, "name": "第2话"},
            ],
            "total": 2,
        },
    )
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi()
        result = api.get_episodes(123)

        assert result["total"] == 2
        assert len(result["data"]) == 2
        assert result["data"][0]["ep"] == 1


def test_mark_episode_watched_add_collection():
    """测试标记观看 - 未收藏情况"""
    # get_subject_collection 返回 404（未收藏）
    mock_get_404 = _mock_response(404, {})
    # add_collection_subject 返回 200
    mock_post_200 = _mock_response(200, {"status": "ok"})
    # change_episode_state 返回 200
    mock_put_200 = _mock_response(200, {"status": "ok"})

    def request_side_effect(method, url, **kwargs):
        if method == "POST":
            return mock_post_200
        if method == "PUT":
            return mock_put_200
        return mock_get_404

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.side_effect = request_side_effect

        api = BangumiApi(username="testuser", access_token="test_token")
        result = api.mark_episode_watched("123", "1")

        # 返回 2 表示添加到收藏并标记为看过
        assert result == 2


def test_mark_episode_watched_already_watched():
    """测试标记观看 - 已看过"""
    # get_subject_collection 返回已看过
    mock_get_200 = _mock_response(200, {"type": 2, "subject_id": 123})

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_get_200

        api = BangumiApi(username="testuser", access_token="test_token")
        result = api.mark_episode_watched("123", "1")

        # 返回 0 表示已看过，跳过
        assert result == 0


def test_mark_episode_watched_already_episode_watched():
    """测试标记观看 - 单集已看过"""
    # get_subject_collection 返回在看
    mock_collection = _mock_response(200, {"type": 3, "subject_id": 123})
    # get_ep_collection 返回已看过
    mock_ep = _mock_response(200, {"type": 2, "episode_id": 1})

    def request_side_effect(method, url, **kwargs):
        if "episodes/1" in url:
            return mock_ep
        return mock_collection

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.side_effect = request_side_effect

        api = BangumiApi(username="testuser", access_token="test_token")
        result = api.mark_episode_watched("123", "1")

        # 返回 0 表示已看过，跳过
        assert result == 0


def test_mark_episode_watched_success():
    """测试标记观看 - 成功标记"""
    # get_subject_collection 返回在看
    mock_collection = _mock_response(200, {"type": 3, "subject_id": 123})
    # get_ep_collection 返回未看过
    mock_ep_404 = _mock_response(404, {})
    # change_episode_state 返回 200
    mock_put_200 = _mock_response(200, {"status": "ok"})

    def request_side_effect(method, url, **kwargs):
        if method == "PUT":
            return mock_put_200
        if "episodes/1" in url:
            return mock_ep_404
        return mock_collection

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.side_effect = request_side_effect

        api = BangumiApi(username="testuser", access_token="test_token")
        result = api.mark_episode_watched("123", "1")

        # 返回 1 表示成功标记
        assert result == 1


def test_ensure_subject_watching_not_collected():
    mock_get_404 = _mock_response(404, {})
    mock_post_200 = _mock_response(200, {"status": "ok"})

    def request_side_effect(method, url, **kwargs):
        if method == "POST":
            return mock_post_200
        return mock_get_404

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.side_effect = request_side_effect

        api = BangumiApi(username="testuser", access_token="test_token")
        assert api.ensure_subject_watching("123") == 1


def test_ensure_subject_watching_already_watching():
    mock_get_200 = _mock_response(200, {"type": 3, "subject_id": 123})

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_get_200

        api = BangumiApi(username="testuser", access_token="test_token")
        assert api.ensure_subject_watching("123") == 0


def test_ensure_subject_watching_completed():
    mock_get_200 = _mock_response(200, {"type": 2, "subject_id": 123})

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_get_200

        api = BangumiApi(username="testuser", access_token="test_token")
        assert api.ensure_subject_watching("123") == 0


def test_ensure_subject_watching_plan_to_watch():
    mock_get_200 = _mock_response(200, {"type": 1, "subject_id": 123})
    mock_post_200 = _mock_response(200, {"status": "ok"})

    def request_side_effect(method, url, **kwargs):
        if method == "POST":
            return mock_post_200
        return mock_get_200

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.side_effect = request_side_effect

        api = BangumiApi(username="testuser", access_token="test_token")
        assert api.ensure_subject_watching("123") == 1


def test_get_related_subjects():
    """测试获取关联条目"""
    mock_resp = _mock_response(
        200, [{"id": 456, "relation": "续集", "name": "Test Anime Season 2"}]
    )
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi()
        result = api.get_related_subjects(123)

        assert len(result) == 1
        assert result[0]["relation"] == "续集"


def test_get_subject_collection():
    """测试获取条目收藏状态"""
    mock_resp = _mock_response(200, {"type": 3, "subject_id": 123, "private": False})
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi(username="testuser", access_token="test_token")
        result = api.get_subject_collection("123")

        assert result["type"] == 3


def test_get_ep_collection():
    """测试获取单集收藏状态"""
    mock_resp = _mock_response(200, {"type": 2, "episode_id": 1})
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi(username="testuser", access_token="test_token")
        result = api.get_ep_collection("1")

        assert result["type"] == 2


def test_get_me():
    """测试获取当前用户信息"""
    mock_resp = _mock_response(
        200, {"id": 1, "username": "testuser", "nickname": "Test"}
    )
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi(username="testuser", access_token="test_token")
        result = api.get_me()

        assert result["username"] == "testuser"


def test_api_auth_error():
    """测试 API 认证错误"""
    mock_resp = _mock_response(401, {"error": "Unauthorized"})
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.return_value = mock_resp

        api = BangumiApi(username="testuser", access_token="invalid_token")

        with pytest.raises(ValueError, match="认证失败"):
            api.get_me()


def test_bgm_search_invalid_date_fallback():
    """测试 bgm_search: 遇到无效日期时使用无日期搜索"""
    # 模拟无日期搜索成功
    mock_search_old = _mock_response(
        200, {"list": [{"id": 999, "name": "Test Anime", "name_cn": "测试番剧"}]}
    )
    # 模拟获取条目详情
    mock_subject = _mock_response(
        200, {"id": 999, "name": "Test Anime", "name_cn": "测试番剧"}
    )

    def request_side_effect(method, url, **kwargs):
        if "search/subject" in url:
            return mock_search_old
        return mock_subject

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.side_effect = request_side_effect

        api = BangumiApi()
        # 传入空字符串作为首播日期
        result = api.bgm_search(title="测试番剧", ori_title="", premiere_date="")

        assert result is not None
        assert len(result) == 1
        assert result[0]["id"] == 999


def test_bgm_search_alias_fallback_success():
    """测试 bgm_search: 通过旧版接口与详情接口获取 infobox 别名进行匹配"""
    # 模拟带日期搜索无结果
    mock_post_resp = _mock_response(200, {"data": []})

    # 模拟无日期搜索返回简略信息（无 infobox）
    mock_search_old = _mock_response(
        200,
        {
            "list": [
                {
                    "id": 139022,
                    "name": "Concrete Revolutio",
                    "name_cn": "Concrete Revolutio 超人幻想",
                }
            ]
        },
    )

    # 模拟通过 ID 获取包含 infobox 别名的完整数据
    mock_subject = _mock_response(
        200,
        {
            "id": 139022,
            "name": "Concrete Revolutio",
            "name_cn": "Concrete Revolutio 超人幻想",
            "infobox": [
                {"key": "放送开始", "value": "2015年10月4日"},
                {
                    "key": "别名",
                    "value": [{"v": "具象革命 超人幻想"}, {"v": "コンレボ"}],
                },
            ],
        },
    )

    def request_side_effect(method, url, **kwargs):
        if method == "POST":
            return mock_post_resp
        if "search/subject" in url:
            return mock_search_old
        return mock_subject

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.request.side_effect = request_side_effect

        api = BangumiApi()
        # 模拟传入单集日期与别名
        result = api.bgm_search(
            title="具象革命 超人幻想", ori_title="", premiere_date="2015-12-26"
        )

        # 断言匹配成功并返回正确的条目 ID
        assert result is not None
        assert len(result) == 1
        assert result[0]["id"] == 139022


def test_title_diff_ratio_infobox_extraction():
    """测试 title_diff_ratio: 提取 infobox 别名并计算相似度"""
    api = BangumiApi()

    bgm_data_mock = {
        "name": "Original Name",
        "name_cn": "中文主标题",
        "infobox": [
            {"key": "其他信息", "value": "无关内容"},
            {"key": "别名", "value": [{"v": "别名格式1"}, "纯字符串别名格式2"]},
        ],
    }

    # 命中中文主标题
    assert api.title_diff_ratio("中文主标题", "", bgm_data_mock) == 1.0

    # 命中字典格式别名
    assert api.title_diff_ratio("别名格式1", "", bgm_data_mock) == 1.0

    # 命中字符串格式别名
    assert api.title_diff_ratio("纯字符串别名格式2", "", bgm_data_mock) == 1.0

    # 无关名称，相似度应低于阈值
    assert api.title_diff_ratio("毫无关系的名称", "", bgm_data_mock) < 0.5
