"""Bangumi 封面图 URL 改写单元测试。"""

from app.utils.bgm_image_url import (
    build_poster_cache_namespace,
    extract_poster_url,
    rewrite_bgm_image_url,
)


def test_extract_poster_url_prefers_large():
    subject = {
        "images": {
            "large": "https://lain.bgm.tv/pic/cover/l/a/b/c.jpg",
            "medium": "https://lain.bgm.tv/pic/cover/m/a/b/c.jpg",
        }
    }
    assert extract_poster_url(subject) == "https://lain.bgm.tv/pic/cover/l/a/b/c.jpg"


def test_extract_poster_url_falls_back_to_medium():
    subject = {"images": {"medium": "https://lain.bgm.tv/pic/cover/m/a/b/c.jpg"}}
    assert extract_poster_url(subject) == "https://lain.bgm.tv/pic/cover/m/a/b/c.jpg"


def test_extract_poster_url_missing():
    assert extract_poster_url({}) is None
    assert extract_poster_url({"images": {}}) is None


def test_rewrite_bgm_image_url_with_proxy():
    original = "https://lain.bgm.tv/pic/cover/l/xx/yy/zz.jpg"
    assert (
        rewrite_bgm_image_url(original, "https://lain.example.com")
        == "https://lain.example.com/pic/cover/l/xx/yy/zz.jpg"
    )


def test_rewrite_bgm_image_url_proxy_trailing_slash():
    original = "https://lain.bgm.tv/pic/cover/l/xx/yy/zz.jpg"
    assert (
        rewrite_bgm_image_url(original, "https://lain.example.com/")
        == "https://lain.example.com/pic/cover/l/xx/yy/zz.jpg"
    )


def test_rewrite_bgm_image_url_empty_proxy_unchanged():
    original = "https://lain.bgm.tv/pic/cover/l/xx/yy/zz.jpg"
    assert rewrite_bgm_image_url(original, "") == original
    assert rewrite_bgm_image_url(original, "   ") == original


def test_rewrite_bgm_image_url_non_lain_unchanged():
    url = "https://cdn.example.com/image.jpg"
    assert rewrite_bgm_image_url(url, "https://lain.example.com") == url


def test_build_poster_cache_namespace_stable():
    ns = build_poster_cache_namespace("https://api.example.com", "https://img.example.com")
    assert len(ns) == 12
    assert ns == build_poster_cache_namespace("https://api.example.com", "https://img.example.com")


def test_build_poster_cache_namespace_changes_with_proxy():
    a = build_poster_cache_namespace("https://api.a", "")
    b = build_poster_cache_namespace("https://api.b", "")
    c = build_poster_cache_namespace("https://api.a", "https://img.a")
    assert a != b
    assert a != c
