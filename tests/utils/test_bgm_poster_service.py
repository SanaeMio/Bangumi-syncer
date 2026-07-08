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
    mock_bgm.get_subject.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_get_poster_urls_async():
    with patch(
        "app.utils.bgm_poster_service.get_poster_urls_sync",
        return_value={1: "https://example.com/1.jpg"},
    ) as mock_sync:
        result = await get_poster_urls([1])

    assert result == {1: "https://example.com/1.jpg"}
    mock_sync.assert_called_once_with([1], None)
