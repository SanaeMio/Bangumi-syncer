"""收件箱通知跳转 E2E 测试。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

_READ_API = "**/api/inbox/notifications/*/read"


def _inject_notification(page, *, notif_type: str, ref_id: str = "") -> None:
    """在收件箱列表注入一条通知卡片，供点击跳转使用。"""
    page.evaluate(
        """(payload) => {
            const list = document.getElementById('inboxNotificationsList');
            list.innerHTML =
                '<div class="card" data-notification-id="1" data-notification-group="1"' +
                ' data-type="' + payload.type + '" data-ref-id="' + payload.ref + '">' +
                '<span>通知</span></div>';
        }""",
        {"type": notif_type, "ref": ref_id},
    )


def _click_notification(page) -> None:
    """点击注入的通知卡片（弹窗可能未展开，直接派发点击事件）。"""
    page.evaluate(
        """() => {
            const card = document.querySelector(
                '#inboxNotificationsList [data-notification-id]'
            );
            card.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        }"""
    )


def test_airing_notification_opens_dashboard_airing_card(authed_page, base_url: str):
    """「今日放送」通知跳到仪表盘的放送卡片，而不是同步记录。"""
    page = authed_page
    page.route(
        _READ_API,
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status":"success","data":{"read":true}}',
        ),
    )
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    _inject_notification(page, notif_type="airing_today")
    _click_notification(page)

    page.wait_for_url("**/dashboard#airing-calendar-row", timeout=10000)
    assert "#airing-calendar-row" in page.url


def test_sync_failed_notification_opens_record_detail(authed_page, base_url: str):
    """同步失败类通知跳转到同步记录并带记录 ID。"""
    page = authed_page
    page.route(
        _READ_API,
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status":"success","data":{"read":true}}',
        ),
    )
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    _inject_notification(page, notif_type="sync_failed", ref_id="42")
    _click_notification(page)

    page.wait_for_url("**/records?open=42", timeout=10000)
    assert "/records" in page.url


def test_airing_deep_link_scrolls_card_into_view(authed_page, base_url: str):
    """带 #airing-calendar-row 进入仪表盘时滚动到放送卡片。"""
    page = authed_page
    page.add_init_script(
        """window.__scrollCalls = [];
        const original = Element.prototype.scrollIntoView;
        Element.prototype.scrollIntoView = function (...args) {
            window.__scrollCalls.push(this.id || this.className);
            return original.apply(this, args);
        };"""
    )
    page.route(
        "**/api/airing-calendar*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"archive_enabled":true,"status":"ok","days":[],'
                '"total_episodes":0,"today":"2026-01-01"}'
            ),
        ),
    )

    page.goto(f"{base_url}/dashboard#airing-calendar-row")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)

    calls = page.evaluate("() => window.__scrollCalls")
    assert "airing-calendar-row" in calls
