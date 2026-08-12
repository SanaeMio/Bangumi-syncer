"""Bangumi 封面批量解析服务单元测试。"""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.bgm_poster_service import (
    clear_poster_service_caches,
    get_poster_urls,
    get_poster_urls_sync,
    get_shared_bangumi_api,
)


@pytest.fixture(autouse=True)
def reset_poster_service():
    clear_poster_service_caches()
    yield
    clear_poster_service_caches()


@pytest.fixture(autouse=True)
def no_active_account():
    """默认无激活账号：在看列表预取自动跳过，走逐 ID 兜底路径。"""
    with patch("app.core.accounts.get_active_bangumi_config", return_value=None):
        yield


def test_get_shared_bangumi_api_reuses_instance():
    with patch("app.utils.bgm_poster_service.config_manager.get", return_value=""):
        a = get_shared_bangumi_api()
        b = get_shared_bangumi_api()
    assert a is b


def test_get_poster_urls_sync_dedupes_and_prefers_small():
    mock_bgm = MagicMock()
    mock_bgm.get_subject.side_effect = [
        {
            "id": 1,
            "images": {
                "small": "https://lain.bgm.tv/pic/cover/s/a/b/1.jpg",
                "large": "https://lain.bgm.tv/pic/cover/l/a/b/1.jpg",
            },
        },
        {
            "id": 2,
            "images": {"large": "https://lain.bgm.tv/pic/cover/l/c/d/2.jpg"},
        },
    ]

    with patch(
        "app.utils.bgm_poster_service.get_shared_bangumi_api", return_value=mock_bgm
    ):
        with patch("app.utils.bgm_poster_service.config_manager.get", return_value=""):
            result = get_poster_urls_sync([1, 2, 1])

    assert result == {
        1: "https://lain.bgm.tv/pic/cover/s/a/b/1.jpg",
        2: "https://lain.bgm.tv/pic/cover/l/c/d/2.jpg",
    }
    assert mock_bgm.get_subject.call_count == 2


def test_get_poster_urls_sync_applies_image_proxy():
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {
        "id": 10,
        "images": {"small": "https://lain.bgm.tv/pic/cover/s/x/y/10.jpg"},
    }

    def fake_get(section, key, fallback=""):
        if section == "dev" and key == "bgm_image_proxy":
            return "https://img-proxy.example.com"
        return fallback

    with patch(
        "app.utils.bgm_poster_service.get_shared_bangumi_api", return_value=mock_bgm
    ):
        with patch(
            "app.utils.bgm_poster_service.config_manager.get", side_effect=fake_get
        ):
            result = get_poster_urls_sync([10])

    assert result[10] == "https://img-proxy.example.com/pic/cover/s/x/y/10.jpg"


def test_get_poster_urls_sync_skips_failed_subject():
    mock_bgm = MagicMock()
    mock_bgm.get_subject.side_effect = [
        RuntimeError("network"),
        {"id": 2, "images": {"small": "https://lain.bgm.tv/pic/cover/s/c/d/2.jpg"}},
    ]

    with patch(
        "app.utils.bgm_poster_service.get_shared_bangumi_api", return_value=mock_bgm
    ):
        with patch("app.utils.bgm_poster_service.config_manager.get", return_value=""):
            result = get_poster_urls_sync([1, 2])

    assert result == {2: "https://lain.bgm.tv/pic/cover/s/c/d/2.jpg"}


def test_get_poster_urls_sync_uses_process_cache():
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {
        "id": 5,
        "images": {"small": "https://lain.bgm.tv/pic/cover/s/a/b/5.jpg"},
    }

    with patch(
        "app.utils.bgm_poster_service.get_shared_bangumi_api", return_value=mock_bgm
    ):
        with patch("app.utils.bgm_poster_service.config_manager.get", return_value=""):
            first = get_poster_urls_sync([5])
            second = get_poster_urls_sync([5])

    assert first == second
    mock_bgm.get_subject.assert_called_once()


def test_get_poster_urls_sync_accepts_string_subject_ids():
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {
        "id": 42,
        "images": {"small": "https://lain.bgm.tv/pic/cover/s/a/b/42.jpg"},
    }

    with patch(
        "app.utils.bgm_poster_service.get_shared_bangumi_api", return_value=mock_bgm
    ):
        with patch("app.utils.bgm_poster_service.config_manager.get", return_value=""):
            result = get_poster_urls_sync(["42", 42, "0", "bad"])

    assert result == {42: "https://lain.bgm.tv/pic/cover/s/a/b/42.jpg"}
    mock_bgm.get_subject.assert_called_once_with(42, use_archive=False)


def test_get_poster_urls_bypasses_archive_shortcut():
    """封面需要 API 的 images 字段，get_subject 必须传 use_archive=False。"""
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {
        "id": 9,
        "images": {"small": "https://lain.bgm.tv/pic/cover/s/a/b/9.jpg"},
    }

    with patch(
        "app.utils.bgm_poster_service.get_shared_bangumi_api", return_value=mock_bgm
    ):
        with patch("app.utils.bgm_poster_service.config_manager.get", return_value=""):
            result = get_poster_urls_sync([9])

    assert result == {9: "https://lain.bgm.tv/pic/cover/s/a/b/9.jpg"}
    mock_bgm.get_subject.assert_called_once_with(9, use_archive=False)


@pytest.mark.asyncio
async def test_get_poster_urls_async():
    with patch(
        "app.utils.bgm_poster_service.get_poster_urls_sync",
        return_value={1: "https://example.com/1.jpg"},
    ) as mock_sync:
        result = await get_poster_urls([1])

    assert result == {1: "https://example.com/1.jpg"}
    mock_sync.assert_called_once_with([1], None)


def test_get_poster_urls_sync_parallel_partial_failure():
    mock_bgm = MagicMock()
    mock_bgm.get_subject.side_effect = [
        RuntimeError("network"),
        {"id": 2, "images": {"small": "https://lain.bgm.tv/pic/cover/s/c/d/2.jpg"}},
        {"id": 3, "images": {"small": "https://lain.bgm.tv/pic/cover/s/c/d/3.jpg"}},
    ]

    with patch(
        "app.utils.bgm_poster_service.get_shared_bangumi_api", return_value=mock_bgm
    ):
        with patch("app.utils.bgm_poster_service.config_manager.get", return_value=""):
            result = get_poster_urls_sync([1, 2, 3])

    assert result == {
        2: "https://lain.bgm.tv/pic/cover/s/c/d/2.jpg",
        3: "https://lain.bgm.tv/pic/cover/s/c/d/3.jpg",
    }
    assert mock_bgm.get_subject.call_count == 3


# ── 在看列表批量预取 ────────────────────────────────────────────────────────


def _active_account_cfg():
    return {
        "username": "musnow",
        "access_token": "tok",
        "private": False,
    }


def _watching_item(sid, url):
    return {"subject": {"id": sid, "images": {"small": url}}}


def test_watching_prefetch_hits_skips_individual_fetch():
    """在看列表预取命中时不再逐个调 get_subject。"""
    mock_api = MagicMock()
    mock_api.list_user_collections.return_value = [
        _watching_item(1, "https://lain.bgm.tv/pic/cover/s/a/b/1.jpg"),
    ]
    mock_shared = MagicMock()
    mock_shared.is_api_unreachable.return_value = False

    with (
        patch(
            "app.core.accounts.get_active_bangumi_config",
            return_value=_active_account_cfg(),
        ),
        patch("app.utils.bgm_poster_service.BangumiApi", return_value=mock_api),
        patch("app.utils.bgm_poster_service.config_manager.get", return_value=""),
        patch(
            "app.utils.bgm_poster_service.get_shared_bangumi_api",
            return_value=mock_shared,
        ),
    ):
        result = get_poster_urls_sync([1])

    assert result == {1: "https://lain.bgm.tv/pic/cover/s/a/b/1.jpg"}
    mock_api.list_user_collections.assert_called_once_with(
        collection_type=3, limit=50, max_total=500, max_pages=3
    )
    mock_api.close.assert_called_once()
    mock_shared.is_api_unreachable.assert_called_once_with()


def test_watching_prefetch_skipped_when_api_unreachable():
    """共享实例记录 API 不可达（TTL 内）时跳过预取，直接逐 ID 兜底。"""
    mock_api = MagicMock()
    mock_shared = MagicMock()
    mock_shared.is_api_unreachable.return_value = True
    mock_shared.get_subject.return_value = {
        "id": 4,
        "images": {"small": "https://lain.bgm.tv/pic/cover/s/c/d/4.jpg"},
    }

    with (
        patch(
            "app.core.accounts.get_active_bangumi_config",
            return_value=_active_account_cfg(),
        ),
        patch("app.utils.bgm_poster_service.BangumiApi", return_value=mock_api),
        patch("app.utils.bgm_poster_service.config_manager.get", return_value=""),
        patch(
            "app.utils.bgm_poster_service.get_shared_bangumi_api",
            return_value=mock_shared,
        ),
    ):
        result = get_poster_urls_sync([4])

    assert result == {4: "https://lain.bgm.tv/pic/cover/s/c/d/4.jpg"}
    mock_api.list_user_collections.assert_not_called()
    mock_api.close.assert_not_called()
    mock_shared.get_subject.assert_called_once_with(4, use_archive=False)


def test_watching_prefetch_short_cache_singleflight():
    """在看预取结果 60s 内复用：并发/重复调用不重复拉分页。"""
    mock_api = MagicMock()
    mock_api.list_user_collections.return_value = [
        _watching_item(11, "https://lain.bgm.tv/pic/cover/s/a/b/11.jpg"),
    ]
    mock_shared = MagicMock()
    mock_shared.is_api_unreachable.return_value = False

    with (
        patch(
            "app.core.accounts.get_active_bangumi_config",
            return_value=_active_account_cfg(),
        ),
        patch("app.utils.bgm_poster_service.BangumiApi", return_value=mock_api),
        patch("app.utils.bgm_poster_service.config_manager.get", return_value=""),
        patch(
            "app.utils.bgm_poster_service.get_shared_bangumi_api",
            return_value=mock_shared,
        ),
    ):
        first = get_poster_urls_sync([11])
        second = get_poster_urls_sync([11, 22])

    assert first == {11: "https://lain.bgm.tv/pic/cover/s/a/b/11.jpg"}
    assert 11 in second
    assert mock_api.list_user_collections.call_count == 1


def test_watching_prefetch_miss_falls_back_to_individual():
    """预取未命中的条目逐个拉取兜底（use_archive=False）。"""
    mock_api = MagicMock()
    mock_api.list_user_collections.return_value = [
        _watching_item(2, "https://lain.bgm.tv/pic/cover/s/a/b/2.jpg"),
    ]
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {
        "id": 1,
        "images": {"small": "https://lain.bgm.tv/pic/cover/s/c/d/1.jpg"},
    }
    mock_bgm.is_api_unreachable.return_value = False

    with (
        patch(
            "app.core.accounts.get_active_bangumi_config",
            return_value=_active_account_cfg(),
        ),
        patch("app.utils.bgm_poster_service.BangumiApi", return_value=mock_api),
        patch("app.utils.bgm_poster_service.config_manager.get", return_value=""),
        patch(
            "app.utils.bgm_poster_service.get_shared_bangumi_api",
            return_value=mock_bgm,
        ),
    ):
        result = get_poster_urls_sync([1, 2])

    assert result == {
        1: "https://lain.bgm.tv/pic/cover/s/c/d/1.jpg",
        2: "https://lain.bgm.tv/pic/cover/s/a/b/2.jpg",
    }
    mock_bgm.get_subject.assert_called_once_with(1, use_archive=False)


def test_watching_prefetch_applies_image_proxy():
    """预取命中的 URL 同样应用 bgm_image_proxy 改写。"""
    mock_api = MagicMock()
    mock_api.list_user_collections.return_value = [
        _watching_item(7, "https://lain.bgm.tv/pic/cover/s/x/y/7.jpg"),
    ]
    mock_shared = MagicMock()
    mock_shared.is_api_unreachable.return_value = False

    def fake_get(section, key, fallback=""):
        if section == "dev" and key == "bgm_image_proxy":
            return "https://img-proxy.example.com"
        return fallback

    with (
        patch(
            "app.core.accounts.get_active_bangumi_config",
            return_value=_active_account_cfg(),
        ),
        patch("app.utils.bgm_poster_service.BangumiApi", return_value=mock_api),
        patch(
            "app.utils.bgm_poster_service.config_manager.get",
            side_effect=fake_get,
        ),
        patch(
            "app.utils.bgm_poster_service.get_shared_bangumi_api",
            return_value=mock_shared,
        ),
    ):
        result = get_poster_urls_sync([7])

    assert result == {7: "https://img-proxy.example.com/pic/cover/s/x/y/7.jpg"}


def test_watching_prefetch_failure_falls_back_silently():
    """预取请求失败时静默回退逐 ID 兜底，不影响封面可用性。"""
    mock_api = MagicMock()
    mock_api.list_user_collections.side_effect = RuntimeError("boom")
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {
        "id": 3,
        "images": {"small": "https://lain.bgm.tv/pic/cover/s/c/d/3.jpg"},
    }
    mock_bgm.is_api_unreachable.return_value = False

    with (
        patch(
            "app.core.accounts.get_active_bangumi_config",
            return_value=_active_account_cfg(),
        ),
        patch("app.utils.bgm_poster_service.BangumiApi", return_value=mock_api),
        patch("app.utils.bgm_poster_service.config_manager.get", return_value=""),
        patch(
            "app.utils.bgm_poster_service.get_shared_bangumi_api",
            return_value=mock_bgm,
        ),
    ):
        result = get_poster_urls_sync([3])

    assert result == {3: "https://lain.bgm.tv/pic/cover/s/c/d/3.jpg"}
    mock_bgm.get_subject.assert_called_once_with(3, use_archive=False)
