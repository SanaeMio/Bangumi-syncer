"""AiringTodayScheduler 单元测试

覆盖 _is_enabled / _get_driver_config / _run_sync_job 的关键分支：
- enabled 与 archive 启用的耦合
- cron 默认值与自定义值
- 禁用/未导入/空放送/正常触发/降级/超时
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.airing_today_scheduler import AiringTodayScheduler


def _make_get_side_effect(values: dict[tuple[str, str], object]):
    """构造 config_manager.get 的 side_effect：按 (section, key) 查表，未命中返回 fallback。"""

    def _get(section, key, fallback=None):
        return values.get((section, key), fallback)

    return _get


def _existing_path():
    """构造 exists()=True 的 path mock"""
    p = MagicMock()
    p.exists.return_value = True
    return p


def _missing_path():
    """构造 exists()=False 的 path mock"""
    p = MagicMock()
    p.exists.return_value = False
    return p


# ----------------------------------------------------------------------
# 1. _is_enabled
# ----------------------------------------------------------------------


class TestIsEnabled:
    """notify-airing-today enabled 与 archive 启用必须同时为真"""

    def test_enabled_and_archive_on_returns_true(self):
        s = AiringTodayScheduler()
        with (
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
        ):
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "enabled"): True}
            )
            ba.enabled = True
            assert s._is_enabled() is True

    def test_disabled_returns_false(self):
        s = AiringTodayScheduler()
        with (
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
        ):
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "enabled"): False}
            )
            ba.enabled = True
            assert s._is_enabled() is False

    def test_archive_disabled_returns_false(self):
        """archive 未启用时即使 enabled=True 也不应启用"""
        s = AiringTodayScheduler()
        with (
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
        ):
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "enabled"): True}
            )
            ba.enabled = False
            assert s._is_enabled() is False

    def test_unconfigured_fallback_true(self):
        """未配置 enabled 时 fallback True（仍需 archive 启用）"""
        s = AiringTodayScheduler()
        with (
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
        ):
            cm.get.side_effect = _make_get_side_effect({})
            ba.enabled = True
            assert s._is_enabled() is True


# ----------------------------------------------------------------------
# 2. _get_driver_config
# ----------------------------------------------------------------------


class TestGetDriverConfig:
    """cron 默认值与自定义值"""

    def test_default_cron_when_unconfigured(self):
        s = AiringTodayScheduler()
        with patch("app.services.airing_today_scheduler.config_manager") as cm:
            cm.get.side_effect = _make_get_side_effect({})
            cfg = s._get_driver_config()
        assert cfg["sync_interval"] == "0 9 * * *"

    def test_custom_cron(self):
        s = AiringTodayScheduler()
        with patch("app.services.airing_today_scheduler.config_manager") as cm:
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "cron"): "30 8 * * *"}
            )
            cfg = s._get_driver_config()
        assert cfg["sync_interval"] == "30 8 * * *"


# ----------------------------------------------------------------------
# 3. _run_sync_job
# ----------------------------------------------------------------------


class TestRunSyncJob:
    """_run_sync_job 流程：启用检查 → 数据查询 → 通知触发"""

    @pytest.mark.asyncio
    async def test_disabled_returns_without_query(self):
        s = AiringTodayScheduler()
        with (
            patch.object(s, "_is_enabled", return_value=False),
            patch("app.services.airing_today_scheduler.archive_store") as store,
        ):
            await s._run_sync_job()
        store.get_episodes_by_airdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_archive_db_missing_skips(self):
        """Archive 数据未导入（db 文件不存在）时跳过，不查询不通知"""
        s = AiringTodayScheduler()
        s._scheduler_config = {"job_timeout": 120}
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
            patch("app.services.airing_today_scheduler.archive_store") as store,
            patch("app.services.airing_today_scheduler.notify_airing_today") as notify,
        ):
            ba.get_active_db_path.return_value = _missing_path()
            await s._run_sync_job()
        store.get_episodes_by_airdate.assert_not_called()
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_airing_skips_notify(self):
        """今日无放送数据时不触发通知"""
        s = AiringTodayScheduler()
        s._scheduler_config = {"job_timeout": 120}
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
            patch("app.services.airing_today_scheduler.archive_store") as store,
            patch("app.services.airing_today_scheduler.notify_airing_today") as notify,
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch(
                "app.services.airing_today_scheduler._build_bangumi_api",
                return_value=None,
            ),
        ):
            ba.get_active_db_path.return_value = _existing_path()
            store.get_episodes_by_airdate.return_value = []
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "only_watching"): True}
            )
            await s._run_sync_job()
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_triggers_notify_with_payload(self):
        """有放送数据时触发通知，payload 含 total/episodes/airdate"""
        s = AiringTodayScheduler()
        s._scheduler_config = {"job_timeout": 120}
        mock_rows = [
            {
                "episode_id": 101,
                "subject_id": 1,
                "subject_name": "Anime A",
                "subject_name_cn": "动画A",
                "subject_type": 2,
                "ep_sort": 1,
                "ep_name": "EP01",
                "ep_name_cn": "第一集",
                "airdate": "2026-07-31",
            }
        ]
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
            patch("app.services.airing_today_scheduler.archive_store") as store,
            patch("app.services.airing_today_scheduler.notify_airing_today") as notify,
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch(
                "app.services.airing_today_scheduler._build_bangumi_api",
                return_value=None,
            ),
        ):
            ba.get_active_db_path.return_value = _existing_path()
            store.get_episodes_by_airdate.return_value = mock_rows
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "only_watching"): False}
            )
            await s._run_sync_job()

        notify.assert_called_once()
        kwargs = notify.call_args.kwargs
        assert kwargs["total"] == 1
        assert len(kwargs["episodes"]) == 1
        assert kwargs["episodes"][0]["subject_name"] == "Anime A"
        assert kwargs["only_watching"] is False
        assert kwargs["airdate"]  # 自动取今天日期

    @pytest.mark.asyncio
    async def test_only_watching_failure_skips_notify(self):
        """获取在看列表失败时不推送通知（不降级为全部放送）

        "我的追番"语义下全部放送对用户无意义，静默推送会误导。
        """
        s = AiringTodayScheduler()
        s._scheduler_config = {"job_timeout": 120}
        mock_api = MagicMock()
        mock_rows = [
            {
                "episode_id": 101,
                "subject_id": 1,
                "subject_name": "Anime A",
                "subject_name_cn": "",
                "subject_type": 2,
                "ep_sort": 1,
                "ep_name": "",
                "ep_name_cn": "",
                "airdate": "2026-07-31",
            }
        ]
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
            patch("app.services.airing_today_scheduler.archive_store") as store,
            patch("app.services.airing_today_scheduler.notify_airing_today") as notify,
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch(
                "app.services.airing_today_scheduler._build_bangumi_api",
                return_value=mock_api,
            ),
            patch(
                "app.services.airing_today_scheduler.get_watching_subject_ids",
                side_effect=RuntimeError("api error"),
            ),
        ):
            ba.get_active_db_path.return_value = _existing_path()
            store.get_episodes_by_airdate.return_value = mock_rows
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "only_watching"): True}
            )
            await s._run_sync_job()

        # 不应触发通知（不降级为全部放送）
        notify.assert_not_called()
        # 不应查询 Archive（避免无意义 IO）
        store.get_episodes_by_airdate.assert_not_called()
        # 临时 api 实例仍应被关闭（资源释放）
        mock_api.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_only_watching_no_config_skips_notify(self):
        """only_watching=True 但未配置 Bangumi 账号时不推送通知"""
        s = AiringTodayScheduler()
        s._scheduler_config = {"job_timeout": 120}
        mock_rows = [
            {
                "episode_id": 101,
                "subject_id": 1,
                "subject_name": "Anime A",
                "subject_name_cn": "",
                "subject_type": 2,
                "ep_sort": 1,
                "ep_name": "",
                "ep_name_cn": "",
                "airdate": "2026-07-31",
            }
        ]
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
            patch("app.services.airing_today_scheduler.archive_store") as store,
            patch("app.services.airing_today_scheduler.notify_airing_today") as notify,
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch(
                "app.services.airing_today_scheduler._build_bangumi_api",
                return_value=None,
            ),
        ):
            ba.get_active_db_path.return_value = _existing_path()
            store.get_episodes_by_airdate.return_value = mock_rows
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "only_watching"): True}
            )
            await s._run_sync_job()

        notify.assert_not_called()
        store.get_episodes_by_airdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_watching_success_filters_subject_ids(self):
        """获取在看列表成功时传 subject_ids 给查询"""
        s = AiringTodayScheduler()
        s._scheduler_config = {"job_timeout": 120}
        mock_api = MagicMock()
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
            patch("app.services.airing_today_scheduler.archive_store") as store,
            patch("app.services.airing_today_scheduler.notify_airing_today"),
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch(
                "app.services.airing_today_scheduler._build_bangumi_api",
                return_value=mock_api,
            ),
            patch(
                "app.services.airing_today_scheduler.get_watching_subject_ids",
                return_value={1, 2, 3},
            ),
        ):
            ba.get_active_db_path.return_value = _existing_path()
            store.get_episodes_by_airdate.return_value = []
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "only_watching"): True}
            )
            await s._run_sync_job()

        _args = store.get_episodes_by_airdate.call_args
        assert _args.kwargs.get("subject_ids") == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_timeout_does_not_raise(self):
        """查询超时不应抛出，由 _run_sync_job 内部捕获"""
        s = AiringTodayScheduler()
        s._scheduler_config = {"job_timeout": 120}

        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
            patch("app.services.airing_today_scheduler.archive_store"),
            patch("app.services.airing_today_scheduler.notify_airing_today"),
            patch(
                "app.services.airing_today_scheduler.notify_scheduler_failure"
            ) as mock_fail,
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch(
                "app.services.airing_today_scheduler._build_bangumi_api",
                return_value=None,
            ),
            patch(
                "app.services.airing_today_scheduler.asyncio.wait_for",
                new_callable=AsyncMock,
                side_effect=asyncio.TimeoutError,
            ),
        ):
            ba.get_active_db_path.return_value = _existing_path()
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "only_watching"): False}
            )
            # 不应抛出
            await s._run_sync_job()

        # 超时应触发 scheduler_job_failed 通知
        mock_fail.assert_called_once()
        assert mock_fail.call_args.kwargs.get("timeout") is True

    @pytest.mark.asyncio
    async def test_payload_truncates_to_50_episodes(self):
        """超过 50 条时 payload 只保留前 50 条，但 total 仍为真实数量"""
        s = AiringTodayScheduler()
        s._scheduler_config = {"job_timeout": 120}
        mock_rows = [
            {
                "episode_id": i,
                "subject_id": i,
                "subject_name": f"Anime {i}",
                "subject_name_cn": "",
                "subject_type": 2,
                "ep_sort": 1,
                "ep_name": "",
                "ep_name_cn": "",
                "airdate": "2026-07-31",
            }
            for i in range(60)
        ]
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch("app.services.airing_today_scheduler.bangumi_archive") as ba,
            patch("app.services.airing_today_scheduler.archive_store") as store,
            patch("app.services.airing_today_scheduler.notify_airing_today") as notify,
            patch("app.services.airing_today_scheduler.config_manager") as cm,
            patch(
                "app.services.airing_today_scheduler._build_bangumi_api",
                return_value=None,
            ),
        ):
            ba.get_active_db_path.return_value = _existing_path()
            store.get_episodes_by_airdate.return_value = mock_rows
            cm.get.side_effect = _make_get_side_effect(
                {("notify-airing-today", "only_watching"): False}
            )
            await s._run_sync_job()

        kwargs = notify.call_args.kwargs
        assert kwargs["total"] == 60  # 真实总数
        assert len(kwargs["episodes"]) == 50  # payload 截断
