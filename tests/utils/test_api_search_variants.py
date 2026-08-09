"""API 路径变体生成测试

验证 bgm_search 在 archive 未命中、降级到旧版 search_old 接口时，
正确生成并按优先级遍历标题变体（书名号剥离、标题分割主段、媒体前缀变体），
提升 API 路径的匹配率，且不破坏已有变体的优先级顺序。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from app.utils.bangumi_api import BangumiApi


def _mock_resp(json_data: Any) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = json_data
    r.headers = {}
    r.text = ""
    r.elapsed.total_seconds.return_value = 0.01
    return r


def _make_api() -> BangumiApi:
    """构造一个不依赖 httpx 真实 Client 的 BangumiApi 实例。

    通过 patch httpx.Client 避免真实网络初始化。
    """
    with patch("app.utils.bangumi_api.httpx.Client") as mock_cls:
        mock_cls.return_value = MagicMock()
        return BangumiApi()


def _capture_search_old_titles(api: BangumiApi) -> list[str]:
    """记录 bgm_search 兜底阶段 search_old 被调用的 title 顺序。

    Returns:
        装饰后的副作用函数；将调用记录写入捕获列表。
    """
    seen: list[str] = []

    def fake_search_old(title: str, list_only: bool = True, subject_type: int = 2):
        seen.append(title)
        # 全部返回空列表，迫使 bgm_search 遍历所有变体
        return []

    api.search_old = fake_search_old  # type: ignore[method-assign]
    return seen


class TestBgmSearchVariantOrder:
    """变体优先级与去重测试"""

    def test_simple_title_no_extra_variants(self) -> None:
        """简单标题（无后缀、无包裹、无分隔符）只产生原始标题变体。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        # 无 premiere_date 直接进入 search_old 兜底
        api.bgm_search(title="完美世界", ori_title=None, premiere_date="")

        # 应该至少尝试原始标题
        assert "完美世界" in seen
        # 简单标题不会触发书名号剥离/标题分割/媒体前缀
        # 但会触发媒体前缀变体（默认对核心标题拼劇場版等）
        # 媒体前缀变体数量 = len(_MEDIA_PREFIX_VARIANTS) = 4
        from app.utils.bangumi_api._archive_shortcut import _MEDIA_PREFIX_VARIANTS

        for prefix in _MEDIA_PREFIX_VARIANTS:
            assert f"{prefix}完美世界" in seen

    def test_season_suffix_stripped_variant(self) -> None:
        """含季数后缀的标题生成剥离后变体，且原始标题在前。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        api.bgm_search(title="完美世界 S06E279", ori_title=None, premiere_date="")

        # 原始标题在前
        assert seen[0] == "完美世界 S06E279"
        # 剥离后变体存在
        assert "完美世界" in seen
        # 剥离后变体在原始标题之后
        assert seen.index("完美世界") > seen.index("完美世界 S06E279")

    def test_title_wrapper_stripped_variant(self) -> None:
        """书名号包裹的标题生成剥离后变体。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        api.bgm_search(title="「君の名は。」", ori_title=None, premiere_date="")

        # 原始 + 剥离后
        assert "「君の名は。」" in seen
        assert "君の名は。" in seen
        # 剥离后变体在原始之后
        assert seen.index("君の名は。") > seen.index("「君の名は。」")

    def test_title_segment_split_variant(self) -> None:
        """主副标题分隔的标题生成主段变体（主段长度 >= 4）。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        api.bgm_search(
            title="魔法少女小圆：叛逆的物语", ori_title=None, premiere_date=""
        )

        # 主段变体存在
        assert "魔法少女小圆" in seen
        # 主段变体在原始之后
        assert seen.index("魔法少女小圆") > seen.index("魔法少女小圆：叛逆的物语")

    def test_short_main_segment_not_split(self) -> None:
        """主段长度 < 4 时不生成主段变体（避免过短误匹配）。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        # "A:副标题" 主段 "A" 长度 1，不生成主段变体
        api.bgm_search(title="A:副标题", ori_title=None, premiere_date="")

        # 不应该有 "A" 作为独立变体（仅可能因原始 ori_title 为 None 而跳过）
        # search_titles 中不应包含 "A"
        assert "A" not in seen

    def test_media_prefix_variants_appended(self) -> None:
        """核心标题不含媒体前缀时，追加所有前缀变体。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        api.bgm_search(title="クドわふたー", ori_title=None, premiere_date="")

        from app.utils.bangumi_api._archive_shortcut import _MEDIA_PREFIX_VARIANTS

        for prefix in _MEDIA_PREFIX_VARIANTS:
            assert f"{prefix}クドわふたー" in seen

    def test_existing_media_prefix_not_doubled(self) -> None:
        """已含媒体前缀的标题不会重复拼接。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        api.bgm_search(title="劇場版 クドわふたー", ori_title=None, premiere_date="")

        # 不会出现 "劇場版 劇場版 クドわふたー"
        assert "劇場版 劇場版 クドわふたー" not in seen

    def test_dedup_preserves_priority(self) -> None:
        """重复变体只出现一次，保持首次出现的优先级。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        # title 与 ori_title 相同时应去重
        api.bgm_search(title="测试", ori_title="测试", premiere_date="")

        # "测试" 只应出现一次（作为原始变体）
        assert seen.count("测试") == 1

    def test_ori_title_before_title(self) -> None:
        """ori_title 在 title 之前（更精确的原始名优先）。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        api.bgm_search(title="中文译名", ori_title="日本語原名", premiere_date="")

        # ori_title 在前
        assert seen.index("日本語原名") < seen.index("中文译名")

    def test_full_variant_sequence(self) -> None:
        """完整变体序列：原始 → 剥离后 → 书名号剥离 → 主段 → 媒体前缀。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        # 构造一个能触发所有变体类型的标题
        # 「『魔法少女小圆：叛逆的物语』 S02E10」
        # - 原始:  「『魔法少女小圆：叛逆的物语』 S02E10」
        # - 剥离后:「『魔法少女小圆：叛逆的物语』」（剥离 S02E10）
        # - 书名号剥离外层: 魔法少女小圆：叛逆的物语（剥离外层 『』）
        # - 主段: 魔法少女小圆（分割：）
        # - 媒体前缀: 劇場版 魔法少女小圆 等
        api.bgm_search(
            title="『魔法少女小圆：叛逆的物语』 S02E10",
            ori_title=None,
            premiere_date="",
        )

        # 验证关键变体都存在
        assert "『魔法少女小圆：叛逆的物语』 S02E10" in seen
        # 剥离 S02E10 后的标题
        assert "『魔法少女小圆：叛逆的物语』" in seen
        # 剥离外层书名号
        assert "魔法少女小圆：叛逆的物语" in seen
        # 主段
        assert "魔法少女小圆" in seen
        # 媒体前缀变体
        assert "劇場版 魔法少女小圆" in seen

        # 验证优先级顺序
        idx_original = seen.index("『魔法少女小圆：叛逆的物语』 S02E10")
        idx_stripped = seen.index("『魔法少女小圆：叛逆的物语』")
        idx_unwrapped = seen.index("魔法少女小圆：叛逆的物语")
        idx_main_seg = seen.index("魔法少女小圆")
        idx_prefix = seen.index("劇場版 魔法少女小圆")

        assert idx_original < idx_stripped
        assert idx_stripped < idx_unwrapped
        assert idx_unwrapped < idx_main_seg
        assert idx_main_seg < idx_prefix


class TestBgmSearchHitBehavior:
    """变体命中后停止遍历的行为测试"""

    def test_stops_after_first_hit(self) -> None:
        """search_old 返回非空结果后停止遍历后续变体。"""
        api = _make_api()

        call_count = 0

        def fake_search_old(title: str, list_only: bool = True, subject_type: int = 2):
            nonlocal call_count
            call_count += 1
            # 第一个变体（原始标题）就命中
            if call_count == 1:
                return [{"id": 999, "name": "测试", "name_cn": "测试"}]
            return []

        api.search_old = fake_search_old  # type: ignore[method-assign]
        # get_subject 返回与查询标题一致的 name，确保相似度 > 0.5
        api.get_subject = lambda sid: {"id": sid, "name": "测试", "name_cn": "测试"}  # type: ignore[method-assign]

        result = api.bgm_search(title="测试", ori_title=None, premiere_date="")

        assert result is not None
        assert call_count == 1  # 仅调用一次

    def test_continues_until_hit(self) -> None:
        """前几个变体未命中时继续遍历，直到命中。"""
        api = _make_api()

        call_titles: list[str] = []

        def fake_search_old(title: str, list_only: bool = True, subject_type: int = 2):
            call_titles.append(title)
            # 仅 "完美世界" 命中
            if title == "完美世界":
                return [{"id": 100, "name": "完美世界", "name_cn": "完美世界"}]
            return []

        api.search_old = fake_search_old  # type: ignore[method-assign]
        api.get_subject = lambda sid: {
            "id": sid,
            "name": "完美世界",
            "name_cn": "完美世界",
        }  # type: ignore[method-assign]

        result = api.bgm_search(
            title="完美世界 S06E279", ori_title=None, premiere_date=""
        )

        assert result is not None
        # 原始标题在前，未命中
        # 剥离后变体 "完美世界" 命中
        assert "完美世界 S06E279" in call_titles
        assert "完美世界" in call_titles
        # 命中后停止
        idx_original = call_titles.index("完美世界 S06E279")
        idx_hit = call_titles.index("完美世界")
        assert idx_hit > idx_original


class TestBgmSearchPreciseSearchPath:
    """premiere_date 精确搜索路径测试"""

    def test_precise_search_skips_search_old(self) -> None:
        """精确搜索命中时不进入 search_old 兜底。"""
        api = _make_api()
        search_old_called = False

        def fake_search_old(title: str, list_only: bool = True, subject_type: int = 2):
            nonlocal search_old_called
            search_old_called = True
            return []

        api.search_old = fake_search_old  # type: ignore[method-assign]

        # 精确搜索命中（返回与查询标题一致的 name 确保相似度 > 0.5）
        api.search = lambda **kw: [{"id": 1, "name": "测试", "name_cn": "测试"}]  # type: ignore[method-assign]
        api.get_subject = lambda sid: {"id": sid, "name": "测试", "name_cn": "测试"}  # type: ignore[method-assign]

        result = api.bgm_search(
            title="测试", ori_title=None, premiere_date="2024-01-01"
        )

        assert result is not None
        assert not search_old_called

    def test_precise_search_miss_falls_back_to_variants(self) -> None:
        """精确搜索未命中时降级到 search_old 变体遍历。"""
        api = _make_api()
        call_titles: list[str] = []

        def fake_search(**kw):
            return []

        def fake_search_old(title: str, list_only: bool = True, subject_type: int = 2):
            call_titles.append(title)
            if title == "完美世界":
                return [{"id": 100, "name": "完美世界", "name_cn": "完美世界"}]
            return []

        api.search = fake_search  # type: ignore[method-assign]
        api.search_old = fake_search_old  # type: ignore[method-assign]
        api.get_subject = lambda sid: {
            "id": sid,
            "name": "完美世界",
            "name_cn": "完美世界",
        }  # type: ignore[method-assign]

        result = api.bgm_search(
            title="完美世界 S06E279", ori_title=None, premiere_date="2024-01-01"
        )

        assert result is not None
        # 进入 search_old 兜底
        assert len(call_titles) > 0
        assert "完美世界" in call_titles


class TestVariantCountLimit:
    """变体总数限制测试（避免对 API 产生过多调用）"""

    def test_variant_count_bounded(self) -> None:
        """单次 bgm_search 调用产生的变体数量有上限。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        api.bgm_search(
            title="『复杂标题：副标题』 第二季",
            ori_title="复杂原标题：副标题",
            premiere_date="",
        )

        # 变体总数应受控（原始 + 剥离 + 书名号 + 主段 + 媒体前缀）
        # 上限粗略：4 类 × 2 (title/ori_title) + 4 媒体前缀 × 2 = ~16
        # 实际通过去重后应远小于 30
        assert len(seen) <= 30, f"变体数量过多: {len(seen)}"

    def test_no_infinite_loop_on_empty_inputs(self) -> None:
        """空输入不会导致无限循环或异常。"""
        api = _make_api()
        seen = _capture_search_old_titles(api)

        # 空标题不应抛异常
        result = api.bgm_search(title="", ori_title=None, premiere_date="")
        assert result is None
        # 也不应产生大量变体调用
        assert len(seen) <= 5


class TestReadApiUnreachableShortCircuit:
    """API 不可达标记（TTL 内）下，读接口跳过实际请求直接返回空结果。

    archive 已在方法内先行短路；此标记作用在 archive miss 后的 API 阶段，
    避免每次调用都等待 10s×3 重试，拖垮同步流程。
    """

    def _unreachable(self, api: BangumiApi) -> None:
        api._api_unreachable = True
        api._api_unreachable_until = time.time() + 300

    def test_search_skips_request(self) -> None:
        api = _make_api()
        self._unreachable(api)

        with patch.object(api, "_request_with_retry") as mock_req:
            result = api.search(
                title="测试",
                start_date="2024-01-01",
                end_date="2024-01-03",
            )

        mock_req.assert_not_called()
        assert result == []

    def test_search_old_skips_request(self) -> None:
        api = _make_api()
        self._unreachable(api)

        with patch.object(api, "_request_with_retry") as mock_req:
            result = api.search_old(title="测试")

        mock_req.assert_not_called()
        assert result == []

    def test_get_subject_skips_request(self) -> None:
        api = _make_api()
        self._unreachable(api)

        with patch.object(api, "get") as mock_get:
            result = api.get_subject(123)

        mock_get.assert_not_called()
        assert result == {}

    def test_get_related_subjects_skips_request(self) -> None:
        api = _make_api()
        self._unreachable(api)

        with patch.object(api, "get") as mock_get:
            result = api.get_related_subjects(123)

        mock_get.assert_not_called()
        assert result == []

    def test_get_episodes_skips_request(self) -> None:
        api = _make_api()
        self._unreachable(api)

        with patch.object(api, "get") as mock_get:
            result = api.get_episodes(123)

        mock_get.assert_not_called()
        assert result == {"data": [], "total": 0}

    def test_unreachable_flag_expired_recovers_probe(self) -> None:
        """TTL 过期后 is_api_unreachable 恢复探测，读接口不再跳过。"""
        api = _make_api()
        api._api_unreachable = True
        api._api_unreachable_until = time.time() - 1  # 已过期

        mock_resp = _mock_resp({"data": []})
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.search(
                title="测试",
                start_date="2024-01-01",
                end_date="2024-01-03",
            )

        assert result == []
        assert api._api_unreachable is False


class TestBgmSearchNetworkResilience:
    """网络错误时 bgm_search 不抛异常，而是继续 fallback 并返回 None"""

    def test_connect_error_returns_none_not_raise(self) -> None:
        """所有 API 请求抛 ConnectError 时，bgm_search 返回 None 而非抛出。"""
        api = _make_api()

        with patch.object(
            api, "_request_with_retry", side_effect=httpx.ConnectError("net down")
        ):
            result = api.bgm_search(title="测试", ori_title=None, premiere_date="")

        assert result is None

    def test_precise_search_network_error_falls_back_to_old(self) -> None:
        """精确搜索网络失败后仍继续 search_old 变体遍历（不中断）。"""
        api = _make_api()
        seen: list[str] = []

        def fake_search(**kw):
            raise httpx.ConnectError("net down")

        def fake_search_old(title: str, list_only: bool = True, subject_type: int = 2):
            seen.append(title)
            return []

        api.search = fake_search  # type: ignore[method-assign]
        api.search_old = fake_search_old  # type: ignore[method-assign]

        result = api.bgm_search(
            title="测试", ori_title=None, premiere_date="2024-01-01"
        )

        assert result is None
        # 精确搜索失败后仍进入 search_old 兜底，而不是中断
        assert len(seen) > 0

    def test_get_subject_network_error_skips_candidate(self) -> None:
        """候选详情拉取网络失败时跳过该候选，不中断整个 fallback。"""
        api = _make_api()

        def fake_search_old(title: str, list_only: bool = True, subject_type: int = 2):
            return [{"id": 999, "name": "测试", "name_cn": "测试"}]

        def fake_get_subject(sid):
            raise httpx.ConnectError("net down")

        api.search_old = fake_search_old  # type: ignore[method-assign]
        api.get_subject = fake_get_subject  # type: ignore[method-assign]

        result = api.bgm_search(title="测试", ori_title=None, premiere_date="")

        assert result is None


class TestNetworkErrorNoAttributeError:
    """_request_with_retry 抛 HTTPError 时，search/get_subject 等读接口
    不应抛 AttributeError（占位 dict 无 .json() 方法），应返回空结果。

    回归覆盖：此前 except HTTPError 分支将 res 赋为 dict 后继续走 res.json()，
    对 dict 调 .json() 触发 AttributeError，而 except ValueError 无法捕获，
    导致首次 API 不可达时异常上抛、中断同步流程。
    """

    def test_search_http_error_returns_empty_not_attribute_error(self) -> None:
        api = _make_api()

        with patch.object(
            api, "_request_with_retry", side_effect=httpx.ConnectError("net down")
        ):
            result = api.search(
                title="测试",
                start_date="2024-01-01",
                end_date="2024-01-03",
            )

        assert result == []

    def test_search_http_error_dict_mode(self) -> None:
        api = _make_api()

        with patch.object(
            api, "_request_with_retry", side_effect=httpx.ConnectError("net down")
        ):
            result = api.search(
                title="测试",
                start_date="2024-01-01",
                end_date="2024-01-03",
                list_only=False,
            )

        assert result == {"data": []}

    def test_search_old_http_error_returns_empty_not_attribute_error(self) -> None:
        api = _make_api()

        with patch.object(
            api, "_request_with_retry", side_effect=httpx.ConnectError("net down")
        ):
            result = api.search_old(title="测试")

        assert result == []

    def test_get_subject_http_error_returns_empty_not_attribute_error(self) -> None:
        api = _make_api()

        with patch.object(api, "get", side_effect=httpx.ConnectError("net down")):
            result = api.get_subject(123)

        assert result == {}

    def test_get_related_subjects_http_error_returns_empty_not_attribute_error(
        self,
    ) -> None:
        api = _make_api()

        with patch.object(api, "get", side_effect=httpx.ConnectError("net down")):
            result = api.get_related_subjects(123)

        assert result == []
