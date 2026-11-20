"""仪表盘卡片注册表、默认布局与首页卡片区渲染的契约测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import pages
from app.core import dashboard_registry as registry
from app.core.dashboard_registry import (
    GRID_COLUMNS,
    DashboardCard,
    dashboard_layout,
    layout_height,
    register_card,
    registered_cards,
    unregister_card,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
CARDS = registered_cards()
GROUPS = set(registry.GROUPS)


def _render_dashboard(llm_available: bool = False) -> str:
    """按已登录用户渲染 /dashboard。"""
    app = FastAPI()
    app.include_router(pages.router)
    client = TestClient(app)

    with (
        patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ),
        patch.object(pages, "_llm_cards_enabled", return_value=llm_available),
    ):
        response = client.get("/dashboard")

    assert response.status_code == 200
    return response.text


def _overlaps(a: dict, b: dict, gap: int) -> bool:
    return (
        a["x"] < b["x"] + b["w"]
        and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"] + gap
        and b["y"] < a["y"] + a["h"] + gap
    )


class TestDashboardRegistry:
    """注册表自身的元信息约束。"""

    def test_ids_unique_and_kebab_case(self):
        ids = [card.id for card in CARDS]
        assert len(ids) == len(set(ids))
        for card in CARDS:
            assert card.id == card.id.lower()
            assert card.id.replace("-", "").isalnum()
            assert "_" not in card.id and " " not in card.id

    def test_builtin_cards_have_partial_templates(self):
        for card in CARDS:
            assert card.template, card.id
            assert card.template.startswith("dashboard/cards/"), card.id
            assert (TEMPLATES_DIR / card.template).is_file(), card.template

    def test_metadata_non_empty_and_bounded(self):
        for card in CARDS:
            assert card.title.strip()
            assert card.description.strip()
            assert card.group in GROUPS
            assert 1 <= card.min_width <= card.width <= GRID_COLUMNS
            assert card.height >= card.min_height >= registry.MIN_CARD_HEIGHT

    def test_llm_cards_hidden_by_default(self):
        for card in CARDS:
            assert card.default_visible is (card.group != "llm"), card.id

    def test_stats_cards_share_one_row(self):
        """四张统计卡横排一行（旧版观感）。"""
        stats = [card for card in CARDS if card.group == "stats"]
        assert sum(card.width for card in stats) == GRID_COLUMNS


class TestRegisterCard:
    """插件接入用的登记接口。"""

    def test_register_and_unregister(self):
        card = DashboardCard(
            id="plugin-demo",
            title="插件示例",
            description="插件注册的自定义卡片。",
            render=lambda: "<div class='card'></div>",
            width=4,
            height=200,
        )
        try:
            assert register_card(card) is card
            assert registry.get_card("plugin-demo") is card
            assert card in registered_cards()
            assert card.render_html() == "<div class='card'></div>"
        finally:
            unregister_card("plugin-demo")
        assert registry.get_card("plugin-demo") is None

    def test_duplicate_id_rejected(self):
        card = DashboardCard(
            id="plugin-dup",
            title="重复",
            description="重复 id 应被拒绝。",
            render=lambda: "",
        )
        register_card(card)
        try:
            with pytest.raises(ValueError, match="重复"):
                register_card(card)
        finally:
            unregister_card("plugin-dup")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"id": "Bad_ID", "title": "t", "description": "d"},
            {"id": "ok-id", "title": "t", "description": "d", "group": "unknown"},
            {"id": "ok-id", "title": "t", "description": "d", "width": 0},
            {"id": "ok-id", "title": "t", "description": "d", "width": 13},
            {
                "id": "ok-id",
                "title": "t",
                "description": "d",
                "width": 2,
                "min_width": 3,
            },
            {"id": "ok-id", "title": "t", "description": "d", "height": 40},
        ],
    )
    def test_invalid_metadata_rejected(self, kwargs):
        with pytest.raises(ValueError):
            DashboardCard(**kwargs)

    def test_template_and_render_are_exclusive(self):
        with pytest.raises(ValueError, match="二选一"):
            DashboardCard(
                id="plugin-both",
                title="t",
                description="d",
                template="dashboard/cards/stats_totals.html",
                render=lambda: "",
            )

        with pytest.raises(ValueError, match="二选一"):
            DashboardCard(id="plugin-none", title="t", description="d")


class TestDashboardLayout:
    """默认摆放：全部卡片都有一席之地、可见卡片互不重叠。"""

    def test_layout_covers_every_card(self):
        layout = dashboard_layout()
        assert [item.card.id for item in layout] == [card.id for card in CARDS]

    def test_visible_cards_do_not_overlap(self):
        gap = registry.GRID_GAP
        visible = [
            {"x": item.x, "y": item.y, "w": item.w, "h": item.h}
            for item in dashboard_layout()
            if item.visible
        ]
        assert visible
        for index, first in enumerate(visible):
            for second in visible[index + 1 :]:
                assert not _overlaps(first, second, gap), (first, second)

    def test_visible_cards_stay_inside_columns(self):
        for item in dashboard_layout():
            if not item.visible:
                assert (item.x, item.y) == (0, 0)
                continue
            assert 0 <= item.x
            assert item.x + item.w <= GRID_COLUMNS

    def test_activity_cards_keep_old_two_column_shape(self):
        """「最近同步」占左 5 列，「热力图 + 趋势」叠在右 7 列，与旧版一致。"""
        by_id = {item.card.id: item for item in dashboard_layout()}
        timeline = by_id["sync-timeline"]
        heatmap = by_id["sync-heatmap"]
        trend = by_id["sync-daily-trend"]

        assert (timeline.x, timeline.w) == (0, 5)
        assert (heatmap.x, heatmap.w) == (5, 7)
        assert trend.x == 5 and trend.w == 7
        assert timeline.y == heatmap.y
        assert trend.y == heatmap.y + heatmap.h + registry.GRID_GAP
        assert timeline.h == heatmap.h + trend.h + registry.GRID_GAP

    def test_layout_height_matches_bottom_of_visible_cards(self):
        layout = dashboard_layout()
        expected = max(item.y + item.h for item in layout if item.visible)
        assert layout_height(layout) == expected

    def test_grid_style_writes_css_variables(self):
        item = dashboard_layout()[0]
        style = item.grid_style
        assert f"--dg-x:{item.x}" in style
        assert f"--dg-y:{item.y}px" in style
        assert f"--dg-w:{item.w}" in style
        assert f"--dg-h:{item.h}px" in style


class TestDashboardPageRendersCards:
    """首页卡片区由注册表驱动，而不是硬编码的行。"""

    def test_grid_and_all_cards(self):
        body = _render_dashboard()
        assert 'id="dashboardGrid"' in body
        assert 'class="dashboard-grid"' in body
        assert "dashboard-cards.js" in body
        assert 'id="dashboardCustomizeBtn"' in body

        for card in CARDS:
            assert f'data-dashboard-card="{card.id}"' in body, card.id
            assert f'data-card-title="{card.title}"' in body, card.id

    def test_llm_cards_rendered_but_hidden(self):
        """LLM 卡也进 DOM（自定义弹窗要能列出），但默认隐藏。"""
        body = _render_dashboard(llm_available=False)
        for card in CARDS:
            if card.group != "llm":
                continue
            marker = f'data-dashboard-card="{card.id}"'
            assert marker in body, card.id
            start = body.index(marker)
            tag_start = body.rindex("<div", 0, start)
            tag = body[tag_start:start]
            assert "d-none" in tag, card.id

        assert 'id="llmTokensChart"' in body
        assert 'id="llmQualityChart"' in body

    def test_grid_items_carry_geometry_and_limits(self):
        body = _render_dashboard()
        for item in dashboard_layout():
            if not item.visible:
                continue
            assert f'data-min-width="{item.card.min_width}"' in body
            assert f"--dg-w:{item.w}" in body
        assert f"--dg-height: {layout_height(dashboard_layout())}px" in body

    def test_legacy_rows_are_gone(self):
        body = _render_dashboard()
        assert 'id="llm-stats-row"' not in body
        assert 'id="llm-trend-row"' not in body
