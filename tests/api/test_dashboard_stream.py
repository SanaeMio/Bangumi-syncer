"""首页实时推送（/api/stats/stream）：快照构造与路由注册。"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import sync as sync_api


def _stats(total: int, today: int = 1) -> dict:
    return {
        "total_syncs": total,
        "success_syncs": total,
        "error_syncs": 0,
        "today_syncs": today,
        "success_rate": 100.0,
        "user_stats": [],
        "daily_stats": [],
    }


def _records(*record_ids: int) -> dict:
    return {
        "records": [{"id": rid, "status": "success"} for rid in record_ids],
        "total": len(record_ids),
    }


class TestDashboardSnapshot:
    """快照函数：只取页面需要的字段，并用指纹判断是否真的变化"""

    def test_payload_carries_stats_heatmap_and_recent_records(self):
        with (
            patch.object(
                sync_api.sync_service, "get_sync_stats", return_value=_stats(5)
            ),
            patch.object(
                sync_api.sync_service,
                "get_heatmap_stats",
                return_value=[{"date": "2026-11-12", "count": 2}],
            ),
            patch.object(
                sync_api.sync_service,
                "get_sync_records",
                return_value=_records(9, 8),
            ) as records_mock,
        ):
            snapshot = sync_api._build_dashboard_snapshot()

        assert snapshot["payload"]["stats"]["total_syncs"] == 5
        assert snapshot["payload"]["heatmap"] == [{"date": "2026-11-12", "count": 2}]
        assert [r["id"] for r in snapshot["payload"]["records"]] == [9, 8]
        # 与仪表板时间线一致，只取最近 6 条
        assert records_mock.call_args.kwargs["limit"] == 6
        assert records_mock.call_args.kwargs["skip_count"] is True

    def test_signature_ignores_unrelated_fields(self):
        with (
            patch.object(
                sync_api.sync_service, "get_sync_stats", return_value=_stats(5)
            ),
            patch.object(sync_api.sync_service, "get_heatmap_stats", return_value=[]),
            patch.object(
                sync_api.sync_service,
                "get_sync_records",
                return_value=_records(9, 8),
            ),
        ):
            first = sync_api._build_dashboard_snapshot()["signature"]
            second = sync_api._build_dashboard_snapshot()["signature"]

        assert first == second

    def test_signature_changes_when_new_record_arrives(self):
        common = [
            patch.object(
                sync_api.sync_service, "get_sync_stats", return_value=_stats(5)
            ),
            patch.object(sync_api.sync_service, "get_heatmap_stats", return_value=[]),
        ]
        with common[0], common[1]:
            with patch.object(
                sync_api.sync_service,
                "get_sync_records",
                return_value=_records(9, 8),
            ):
                before = sync_api._build_dashboard_snapshot()["signature"]
            with patch.object(
                sync_api.sync_service,
                "get_sync_records",
                return_value=_records(10, 9, 8),
            ):
                after = sync_api._build_dashboard_snapshot()["signature"]

        assert before != after


class TestDashboardStreamRoute:
    """路由注册与鉴权：SSE 端点挂在 /api/stats/stream"""

    def test_route_is_registered(self):
        app = FastAPI()
        app.include_router(sync_api.router)

        paths = {route.path for route in app.routes}
        assert "/api/stats/stream" in paths

    def test_stream_requires_login(self):
        app = FastAPI()
        app.include_router(sync_api.router)

        with TestClient(app) as client:
            response = client.get("/api/stats/stream")

        assert response.status_code == 401
