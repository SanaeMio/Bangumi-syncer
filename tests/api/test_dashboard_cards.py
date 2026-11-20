"""仪表盘卡片注册表与首页卡片区渲染的契约测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import pages
from app.core.dashboard_registry import CARDS, dashboard_cards

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
GROUPS = {"stats", "llm", "activity"}


def _render_dashboard(llm_enabled: bool) -> str:
    """按已登录用户渲染 /dashboard（LLM 卡片开关显式指定）。"""
    app = FastAPI()
    app.include_router(pages.router)
    client = TestClient(app)

    with (
        patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ),
        patch.object(pages, "_llm_cards_enabled", return_value=llm_enabled),
    ):
        response = client.get("/dashboard")

    assert response.status_code == 200
    return response.text


class TestDashboardRegistry:
    """注册表自身的元信息约束。"""

    def test_ids_unique_and_kebab_case(self):
        ids = [card.id for card in CARDS]
        assert len(ids) == len(set(ids))
        for card in CARDS:
            assert card.id == card.id.lower()
            assert card.id.replace("-", "").isalnum()
            assert "_" not in card.id and " " not in card.id

    def test_partial_templates_exist(self):
        for card in CARDS:
            assert card.template.startswith("dashboard/cards/"), card.id
            assert (TEMPLATES_DIR / card.template).is_file(), card.template

    def test_span_and_metadata_non_empty(self):
        for card in CARDS:
            assert card.span.strip()
            assert card.title.strip()
            assert card.description.strip()
            assert card.group in GROUPS

    def test_llm_cards_hidden_by_default(self):
        for card in CARDS:
            expected = card.group == "llm"
            assert card.default_visible is (not expected), card.id


class TestDashboardCardsHelper:
    """``dashboard_cards()`` 的分组过滤。"""

    def test_without_llm_group(self):
        cards = dashboard_cards(llm_enabled=False)
        assert cards
        assert all(card.group != "llm" for card in cards)
        assert {card.id for card in cards} == {
            card.id for card in CARDS if card.group != "llm"
        }

    def test_with_llm_group(self):
        cards = dashboard_cards(llm_enabled=True)
        assert [card.id for card in cards] == [card.id for card in CARDS]
        assert any(card.group == "llm" for card in cards)

    def test_default_argument_excludes_llm(self):
        assert dashboard_cards() == dashboard_cards(llm_enabled=False)


class TestDashboardPageRendersCards:
    """首页卡片区由注册表驱动，而不是硬编码的行。"""

    def test_grid_and_non_llm_cards(self):
        body = _render_dashboard(llm_enabled=False)
        assert 'class="row g-4 mb-4 dashboard-grid"' in body
        assert 'id="dashboardGrid"' in body
        assert "dashboard-cards.js" in body
        assert 'id="dashboardCustomizeBtn"' in body

        for card in dashboard_cards(llm_enabled=False):
            assert f'data-dashboard-card="{card.id}"' in body, card.id
        for card in dashboard_cards(llm_enabled=True):
            if card.group == "llm":
                assert f'data-dashboard-card="{card.id}"' not in body, card.id

    def test_llm_cards_render_when_enabled(self):
        body = _render_dashboard(llm_enabled=True)
        for card in dashboard_cards(llm_enabled=True):
            assert f'data-dashboard-card="{card.id}"' in body, card.id
        assert 'id="llmTokensChart"' in body
        assert 'id="llmQualityChart"' in body

    def test_legacy_rows_are_gone(self):
        body = _render_dashboard(llm_enabled=False)
        assert 'id="llm-stats-row"' not in body
        assert 'id="llm-trend-row"' not in body
