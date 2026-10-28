"""收件箱通知跳转目标测试。"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _js(name: str) -> str:
    return (_REPO_ROOT / "static/js" / name).read_text(encoding="utf-8")


def test_notification_targets_map_known_types():
    """放送提醒到仪表盘放送卡片，汇总失败到配置页对应节区。"""
    js = _js("inbox.js")
    assert "airing_today: '/dashboard#airing-calendar-row'" in js
    assert "summary_llm_failed: '/config#config-section-llm'" in js
    assert "summary_job_failed: '/config#config-section-summary'" in js


def test_notification_targets_fall_back_to_records():
    """未登记的类型跳转到同步记录，并带记录 ID 打开详情。"""
    js = _js("inbox.js")
    assert "appUrl(target || '/records')" in js
    assert "url += '?open=' + encodeURIComponent(refId)" in js


def test_airing_calendar_scrolls_on_deep_link():
    """从通知跳转进入时放送卡片滚动到位。"""
    js = _js("airing_calendar.js")
    assert "window.location.hash !== '#airing-calendar-row'" in js
    assert "scrollIntoView({ behavior: 'smooth', block: 'start' })" in js
