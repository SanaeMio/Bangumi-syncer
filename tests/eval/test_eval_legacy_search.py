"""legacy 搜索兜底：同步 search 与异步 search_async 都必须被替换。"""

from unittest.mock import patch

import httpx
import pytest

from eval.lib import _maybe_patch_legacy_search


class _FakeBgm:
    pass


@pytest.mark.asyncio
async def test_legacy_search_patches_async_entry():
    bgm = _FakeBgm()

    def _post(*args, **kwargs):
        return httpx.Response(
            502,
            request=httpx.Request("POST", "https://api.bgm.tv/v0/search/subjects"),
        )

    def _get(url, **kwargs):
        return httpx.Response(
            200,
            json={"list": [{"id": 38435, "name": "剧场版 高达"}]},
            request=httpx.Request("GET", url),
        )

    with (
        patch.object(httpx, "post", _post),
        patch.object(httpx, "get", _get),
    ):
        _maybe_patch_legacy_search(bgm)

        result = await bgm.search_async("剧场版 高达", limit=5)

    assert result[0]["id"] == 38435
    assert result[0]["name"] == "剧场版 高达"
