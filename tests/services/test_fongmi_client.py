"""fongmi client 集数解析与完成判定"""

import pytest

from app.services.fongmi.client import (
    media_is_complete,
    media_to_record,
    parse_episode_info,
)
from app.services.fongmi.models import FongmiDevice

_LONG_MIN = -9223372036854775808


def _device() -> FongmiDevice:
    return FongmiDevice(
        ip="192.168.1.100",
        port=9978,
        uuid="abcd1234",
        name="客厅电视",
        device_type=1,
    )


# ===== parse_episode_info =====


@pytest.mark.parametrize(
    "url,artist,expect_season,expect_ep",
    [
        # 1. SxxExx 系列
        # 方案 B：S01 视为默认（无季号），返回 season=1
        ("http://x/S01E001.mp4", "", 1, 1),
        ("http://x/S01E003.mp4", "", 1, 3),
        ("http://x/s1e1.mp4", "", 1, 1),
        ("http://x/S01.E05.mkv", "", 1, 5),
        # S>1 才视为多季
        ("http://x/S02E12.mp4", "", 2, 12),
        ("http://x/show S02E03 [1080p].mkv", "", 2, 3),
        ("http://x/S03E05.mkv", "", 3, 5),
        # 2. EP/Exx 系列（含分隔符变体）
        ("http://x/EP05.mp4", "", 1, 5),
        ("http://x/ep12.mp4", "", 1, 12),
        ("http://x/E03.mp4", "", 1, 3),
        ("http://x/E.05.mp4", "", 1, 5),
        ("http://x/E-12.mp4", "", 1, 12),
        ("http://x/EP_03.mp4", "", 1, 3),
        # 3. 中文集号（集/话/話/章/期/回）
        ("http://x/第3集.mp4", "", 1, 3),
        ("http://x/第10话.mp4", "", 1, 10),
        ("http://x/第5話.mp4", "", 1, 5),
        ("http://x/第5章.mp4", "", 1, 5),
        ("http://x/第8回.mp4", "", 1, 8),
        # 「第2期」视为季号标记（season=2），季号被移除后无集号数字，回退为 1
        ("http://x/第2期.mp4", "", 2, 1),
        # 4. #番号格式
        ("http://x/Show #01.mp4", "", 1, 1),
        ("http://x/番剧 #12.mkv", "", 1, 12),
        # 5. 方括号/书名号内纯数字
        ("http://x/[01].mp4", "", 1, 1),
        ("http://x/番剧【12】.mkv", "", 1, 12),
        ("http://x/Show(05).mp4", "", 1, 5),
        # 6. 用户真实样本：凡人修仙传（夸父盘统一标为 S01，应视为 season=1）
        (
            "http://127.0.0.1:6678/proxy/play/夸父盘/凡人修仙传/F.S01E003.mp4",
            "[1013.46MB] 001-4K F.S01E003.mp4",
            1,
            3,
        ),
        # 斗破苍穹年番 207 集（url 含端口 6678 不应干扰）
        (
            "http://127.0.0.1:6678/proxy/play/夸父盘/斗破苍穹年番/207 4K.mp4",
            "[909.50MB] 201-300 207 4K.mp4",
            1,
            207,
        ),
        ("http://x/夸父盘/斗破苍穹年番/207 4K.mp4", "", 1, 207),
        ("", "[909.50MB] 201-300 207 4K.mp4", 1, 207),
        # 7. 纯数字文件名
        ("http://x/01.mp4", "", 1, 1),
        ("http://x/12.mp4", "", 1, 12),
        # 8. 带分辨率标记
        ("http://x/05 1080p.mp4", "", 1, 5),
        ("http://x/12.720p.mkv", "", 1, 12),
        # 9. 方括号内为文件大小，应跳过
        ("", "[1.63GB] 001-4K.mp4", 1, 1),
        ("", "[734MB] 01-100 04 4K.mp4", 1, 4),
        # 10. 带端口号的 url 不应误解析端口为集号
        ("http://127.0.0.1:9978/proxy/斗破苍穹年番/207.mp4", "", 1, 207),
        # 11. 明确季号标记（方案 B：仅这些情况返回 season>1）
        ("http://x/番剧 第二季/EP05.mp4", "", 2, 5),
        ("http://x/番剧 第二期/第3集.mp4", "", 2, 3),
        ("http://x/番剧 第3季/05.mp4", "", 3, 5),
        ("http://x/Show Season 2/EP05.mp4", "", 2, 5),
        ("http://x/Show Season 02/E03.mp4", "", 2, 3),
        # 12. 无信息回退
        ("", "", 1, 1),
    ],
)
def test_parse_episode_info(url, artist, expect_season, expect_ep):
    season, ep = parse_episode_info(url, artist)
    assert season == expect_season
    assert ep == expect_ep


def test_parse_episode_info_url_priority_over_artist():
    """url 中的 S01E001 应优先于 artist 中的数字"""
    season, ep = parse_episode_info("http://x/S02E05.mp4", "[1GB] 999-4K.mp4")
    assert season == 2
    assert ep == 5


# ===== media_is_complete =====


def test_media_is_complete_above_threshold():
    media = {"title": "测试番", "duration": 100000, "position": 96000}
    assert media_is_complete(media, 95) is True


def test_media_is_complete_below_threshold():
    media = {"title": "测试番", "duration": 100000, "position": 50000}
    assert media_is_complete(media, 95) is False


def test_media_is_complete_no_title():
    media = {"duration": 100000, "position": 96000}
    assert media_is_complete(media, 95) is False


def test_media_is_complete_live_stream_long_min():
    """直播流 duration 为 Long.MIN_VALUE，不视为完成"""
    media = {
        "title": "直播",
        "duration": _LONG_MIN,
        "position": 1000,
    }
    assert media_is_complete(media, 95) is False


def test_media_is_complete_negative_duration():
    media = {"title": "x", "duration": -1, "position": 100}
    assert media_is_complete(media, 95) is False


def test_media_is_complete_zero_position():
    media = {"title": "x", "duration": 100000, "position": 0}
    assert media_is_complete(media, 95) is False


# ===== media_to_record =====


def test_media_to_record_basic():
    media = {
        "title": "凡人修仙传",
        "url": "http://x/夸父盘/凡人修仙传/F.S01E001.mp4",
        "artist": "[1.63GB] 001-4K F.S01E001.mp4",
        "duration": 1400000,
        "position": 1380000,
    }
    rec = media_to_record(_device(), media)
    assert rec is not None
    assert rec.title == "凡人修仙传"
    assert rec.season == 1
    assert rec.episode == 1
    assert rec.device_ip == "192.168.1.100"
    assert rec.device_name == "客厅电视"


def test_media_to_record_no_title_returns_none():
    media = {"url": "http://x", "duration": 100, "position": 99}
    assert media_to_record(_device(), media) is None


def test_media_to_record_empty_title_returns_none():
    media = {"title": "  ", "url": "http://x", "duration": 100, "position": 99}
    assert media_to_record(_device(), media) is None
