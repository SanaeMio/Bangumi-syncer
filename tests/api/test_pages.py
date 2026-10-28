"""
页面路由测试
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import pages

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestPages:
    """测试页面路由"""

    def test_index_page(self):
        """测试首页"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/")

        # 首页应该返回 HTML
        assert response.status_code == 200

    def test_dashboard_page(self):
        """测试仪表板页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200

    def test_login_page(self):
        """测试登录页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/login")

        assert response.status_code == 200

    def test_config_page(self):
        """测试配置页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/config")

        assert response.status_code == 200

    def test_records_page(self):
        """测试记录页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/records")

        assert response.status_code == 200

    def test_mappings_page(self):
        """测试映射页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/mappings")

        assert response.status_code == 200

    def test_debug_page(self):
        """测试调试页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/debug")

        assert response.status_code == 200

    def test_logs_page(self):
        """测试日志页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/logs")

        assert response.status_code == 200

    def test_trakt_config_page(self):
        """测试 Trakt 配置页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/trakt/config")

        assert response.status_code == 200

    def test_trakt_auth_page(self):
        """测试 Trakt 授权页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/trakt/auth")

        assert response.status_code == 200

    def test_trakt_auth_success_page(self):
        """测试 Trakt 授权成功页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/trakt/auth/success")

        assert response.status_code == 200


class TestPagesAuthenticated:
    """测试已认证用户的页面渲染"""

    @staticmethod
    def _make_app():
        app = FastAPI()
        app.include_router(pages.router)
        return app

    def test_dashboard_authenticated(self):
        """已认证用户访问仪表板"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 200

    def test_config_authenticated(self):
        """已认证用户访问配置页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/config", follow_redirects=False)
        assert r.status_code == 200

    def test_records_authenticated(self):
        """已认证用户访问记录页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/records", follow_redirects=False)
        assert r.status_code == 200

    def test_match_records_redirects_to_records(self):
        """匹配记录页重定向至同步记录"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/match-records", follow_redirects=False)
        assert r.status_code == 302
        assert "/records" in (r.headers.get("location") or "")

    def test_match_records_redirects_with_id_param(self):
        """旧链接 ?id= 重定向至同步记录并打开匹配 Tab"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/match-records?id=42", follow_redirects=False)
        assert r.status_code == 302
        location = r.headers.get("location") or ""
        assert "open=42" in location
        assert "tab=match" in location

    def test_mappings_authenticated(self):
        """已认证用户访问映射页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/mappings", follow_redirects=False)
        assert r.status_code == 200

    def test_debug_authenticated(self):
        """已认证用户访问调试页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/debug", follow_redirects=False)
        assert r.status_code == 200

    def test_logs_authenticated(self):
        """已认证用户访问日志页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/logs", follow_redirects=False)
        assert r.status_code == 200

    def test_trakt_config_authenticated(self):
        """已认证用户访问Trakt配置页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/trakt/config", follow_redirects=False)
        assert r.status_code == 200


class TestPagesGuestAndRedirects:
    """未登录重定向与登录页渲染（patch cookie 用户）"""

    @pytest.mark.parametrize(
        "path",
        [
            "/dashboard",
            "/config",
            "/records",
            "/match-records",
            "/mappings",
            "/debug",
            "/logs",
            "/trakt/config",
        ],
    )
    def test_page_redirects_to_login_when_no_user(self, path):
        app = FastAPI()
        app.include_router(pages.router)
        with patch.object(pages, "get_current_user_from_cookie", return_value=None):
            client = TestClient(app)
            r = client.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert "login" in (r.headers.get("location") or "").lower()

    def test_login_page_renders_when_no_user(self):
        app = FastAPI()
        app.include_router(pages.router)
        with patch.object(pages, "get_current_user_from_cookie", return_value=None):
            client = TestClient(app)
            r = client.get("/login")
        assert r.status_code == 200

    def test_login_page_redirects_when_already_logged_in(self):
        app = FastAPI()
        app.include_router(pages.router)
        with patch.object(
            pages,
            "get_current_user_from_cookie",
            return_value={"username": "u"},
        ):
            client = TestClient(app)
            r = client.get("/login", follow_redirects=False)
        assert r.status_code == 302
        assert "dashboard" in (r.headers.get("location") or "").lower()

    def test_trakt_auth_error_page_passes_query_params(self):
        app = FastAPI()
        app.include_router(pages.router)
        client = TestClient(app)
        r = client.get("/trakt/auth?status=err&message=bad+callback")
        assert r.status_code == 200
        assert len(r.content) > 0


def test_agent_runs_page():
    """运行观测页：列表 / 详情弹窗容器与共用脚本就位（已登录）"""
    app = FastAPI()
    app.include_router(pages.router)

    client = TestClient(app)
    with patch.object(
        pages, "get_current_user_from_cookie", return_value={"username": "u"}
    ):
        response = client.get("/agent-runs")

    assert response.status_code == 200
    body = response.text
    assert 'id="agent-runs-table"' in body
    assert 'id="agentRunDetailModal"' in body
    assert "agent_runs.js" in body
    assert "agent-run-steps.js" in body
    # 加载态容器（setAppTableLoading 依赖）
    assert 'id="loading"' in body
    # 项目表格样式（斑马纹/移动端行为）
    assert "app-data-table" in body


def test_dashboard_llm_trend_canvases():
    """仪表板含 LLM 用量趋势容器（tokens 与质量双图；已登录）"""
    app = FastAPI()
    app.include_router(pages.router)

    client = TestClient(app)
    with patch.object(
        pages, "get_current_user_from_cookie", return_value={"username": "u"}
    ):
        response = client.get("/dashboard")

    assert response.status_code == 200
    body = response.text
    assert 'id="llm-trend-row"' in body
    assert 'id="llmTokensChart"' in body
    assert 'id="llmQualityChart"' in body


class TestSharedModals:
    """同步详情 / 重试日志弹窗与 Toast 的层级约束。"""

    @staticmethod
    def _client() -> TestClient:
        app = FastAPI()
        app.include_router(pages.router)
        return TestClient(app)

    def test_retry_log_modal_available_on_dashboard_and_records(self):
        """重试日志弹窗由 base.html 提供：仪表板与记录页均可用、仅一份。

        详情弹窗同属 base.html，在 DOM 顺序上排在日志弹窗前面，使同时打开时日志弹窗位于上层。
        """
        client = self._client()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            for path in ("/dashboard", "/records"):
                html = client.get(path).text
                assert html.count('id="retryLogModal"') == 1, path
                assert html.index('id="recordDetailModal"') < html.index(
                    'id="retryLogModal"'
                ), path

    def test_toast_container_above_modal(self):
        """Toast 容器层级须高于 Bootstrap modal（1055）。"""
        css = (Path(__file__).resolve().parents[2] / "static/css/pages.css").read_text(
            encoding="utf-8"
        )
        block = css.split(".toast-container {", 1)[1].split("}", 1)[0]
        match = re.search(r"z-index:\s*(\d+)", block)
        assert match, "toast-container 未声明 z-index"
        assert int(match.group(1)) > 1055

    def test_retry_sync_closes_detail_modal_first(self):
        """重试入口先关闭同步详情弹窗，避免日志弹窗被其遮挡。"""
        js = (
            Path(__file__).resolve().parents[2] / "static/js/sync-retry.js"
        ).read_text(encoding="utf-8")
        body = js.split("async function retrySync", 1)[1].split(
            "function startRetrySSE", 1
        )[0]
        assert "recordDetailModal" in body
        assert "hidden.bs.modal" in body


class TestConfigConsoleErrors:
    """配置页控制台报错的回归约束。"""

    @staticmethod
    def _config_html() -> str:
        app = FastAPI()
        app.include_router(pages.router)
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            return TestClient(app).get("/config", follow_redirects=False).text

    def test_config_form_divs_balanced(self):
        """配置表单区域内的 div 必须配平。

        form 内遗留未闭合 div 时，HTML 解析器会忽略其后的 </form>，
        表单之后的内容（小屏「配置操作」卡片、调试工具导航）被解析进
        表单内部，控制台报 Multiple forms 警告。
        """
        html = self._config_html()
        start = html.index('<form id="config-form"')
        end = html.index("</form>", start)
        body = re.sub(r"<script\b.*?</script>", "", html[start:end], flags=re.S)
        opens = len(re.findall(r"<div\b", body))
        closes = body.count("</div>")
        assert opens == closes, f"配置表单区域 div 未配平：{opens} 开 / {closes} 闭"

    def test_small_screen_actions_outside_form(self):
        """小屏「配置操作」卡片位于表单之外，与模板结构一致。"""
        html = self._config_html()
        form_start = html.index('<form id="config-form"')
        assert html.index("</form>", form_start) < html.index("card mb-4 d-lg-none")

    def test_legacy_notification_lists_removed(self):
        """模板与脚本中不含旧的 Webhook / Email 列表容器与渲染函数。

        渠道配置由模态框内的 channel-*-list 承载；对不存在的容器赋值
        innerHTML 会抛 TypeError。
        """
        config = (_REPO_ROOT / "templates/config.html").read_text(encoding="utf-8")
        for legacy in (
            "webhook-configs-container",
            "email-configs-container",
            "loadWebhookConfigs",
            "renderWebhookConfigs",
            "loadEmailConfigs",
            "renderEmailConfigs",
        ):
            assert legacy not in config, legacy

    def test_ui_utils_does_not_load_legacy_webhooks(self):
        """页面加载钩子不调用旧 Webhook 加载器。"""
        js = (_REPO_ROOT / "static/js/ui-utils.js").read_text(encoding="utf-8")
        assert "loadWebhookConfigs" not in js

    def test_channel_toggles_refresh_channel_list(self):
        """渠道开关切换后刷新模态框内的列表。"""
        config = (_REPO_ROOT / "templates/config.html").read_text(encoding="utf-8")
        webhook = config.split("async function toggleWebhookEnabled", 1)[1]
        webhook_body = webhook.split("\n  async function ", 1)[0]
        assert "loadWebhookListInChannelModal()" in webhook_body
        email = config.split("async function toggleEmailEnabled", 1)[1]
        email_body = email.split("\n  async function ", 1)[0]
        assert "loadEmailListInChannelModal()" in email_body

    def test_config_page_has_no_debug_console_log(self):
        """配置页脚本不输出调试日志，控制台只保留错误与警告。"""
        config = (_REPO_ROOT / "templates/config.html").read_text(encoding="utf-8")
        assert "console.log(" not in config

    def test_release_notes_modal_blurs_focus_before_hide(self):
        """关闭发行说明弹窗前把焦点移出，避免 aria-hidden 阻止告警。"""
        js = (_REPO_ROOT / "static/js/version-release.js").read_text(encoding="utf-8")
        assert "hide.bs.modal" in js
        assert "active.blur()" in js

    def test_config_forms_disable_autofill(self):
        """配置页各表单声明 autocomplete=off，后代输入框不触发自动填充。"""
        config = (_REPO_ROOT / "templates/config.html").read_text(encoding="utf-8")
        forms = re.findall(r"<form\b[^>]*>", config)
        assert len(forms) == 6
        for tag in forms:
            assert 'autocomplete="off"' in tag, tag

    def test_config_controls_disable_autofill(self):
        """配置页所有文本类控件声明 autocomplete=off，令牌与密钥不使用填充服务。"""
        html = self._config_html()
        tags = re.findall(r"<(?:input|select|textarea)\b[^>]*>", html)
        assert tags
        skipped = {
            "checkbox",
            "radio",
            "file",
            "color",
            "range",
            "hidden",
            "submit",
            "button",
            "reset",
            "image",
        }
        missing = []
        for tag in tags:
            m = re.search(r'type="([^"]*)"', tag)
            if tag.startswith("<input") and m and m.group(1).lower() in skipped:
                continue
            if (
                'autocomplete="off"' not in tag
                and 'autocomplete="new-password"' not in tag
            ):
                missing.append(tag[:80])
        assert not missing, missing[:5]

    def test_config_password_inputs_ignore_password_managers(self):
        """配置页密码类输入框声明 off 并带各密码管理器的忽略属性。"""
        html = self._config_html()
        inputs = re.findall(r"<input\b[^>]*type=\"password\"[^>]*>", html)
        assert inputs
        for tag in inputs:
            assert 'autocomplete="off"' in tag, tag
            for attr in (
                "data-1p-ignore",
                'data-lpignore="true"',
                "data-bwignore",
                "data-protonpass-ignore",
                'data-form-type="other"',
            ):
                assert attr in tag, (attr, tag)

    def test_config_identity_fields_disable_autofill(self):
        """Chrome 视为账号名的文本框声明 off：同表单存在 new-password 时会被提示补 username。"""
        config = (_REPO_ROOT / "templates/config.html").read_text(encoding="utf-8")
        for marker in (
            'id="inline-email-smtp-username"',
            'id="inline-dingtalk-access-token"',
            "bangumi-account-username",
            "bangumi-account-media-username",
        ):
            idx = config.index(marker)
            tag = config[config.rindex("<input", 0, idx) : config.index(">", idx)]
            assert 'autocomplete="off"' in tag, marker
            assert "data-1p-ignore" in tag and "data-bwignore" in tag, marker

    def test_config_page_has_no_credential_autocomplete(self):
        """配置页不使用登录凭据提示，避免密码管理器填充站点账号。"""
        config = (_REPO_ROOT / "templates/config.html").read_text(encoding="utf-8")
        for hint in ('autocomplete="username"', 'autocomplete="current-password"'):
            assert hint not in config, hint

    def test_login_page_keeps_credential_autocomplete(self):
        """登录页保持凭据提示，且表单不关闭 autocomplete。"""
        login = (_REPO_ROOT / "templates/login.html").read_text(encoding="utf-8")
        assert 'autocomplete="username"' in login
        assert 'autocomplete="current-password"' in login
        assert 'autocomplete="off"' not in login
        assert '<form id="loginForm" autocomplete=' not in login

    def test_login_page_declares_favicon(self):
        """登录页为独立模板，同样声明站点图标，避免回退请求 /favicon.ico。"""
        login = (_REPO_ROOT / "templates/login.html").read_text(encoding="utf-8")
        assert 'rel="icon"' in login
        assert "/static/logo.png" in login

    def test_release_notes_render_deferred_until_open(self):
        """发行说明正文延迟到打开弹窗时渲染，页面加载阶段不注入图片。"""
        js = (_REPO_ROOT / "static/js/version-release.js").read_text(encoding="utf-8")
        assert "renderModalIfNeeded();" in js
        assert "setPendingReleaseInfo(" in js
        assert "var pendingReleaseInfo = null;" in js

    def test_release_notes_flag_invalidated_when_info_arrives_while_closed(self):
        """弹窗关闭时收到新版本信息须让已渲染标记失效。

        标记表示弹窗正文与待渲染内容是否一致；弹窗关闭时到达新信息而不清
        标记，下次打开会沿用旧正文。
        """
        js = (_REPO_ROOT / "static/js/version-release.js").read_text(encoding="utf-8")
        body = js.split("function setPendingReleaseInfo(j) {", 1)[1].split(
            "\n    }", 1
        )[0]
        else_part = body.split("} else {", 1)[1]
        assert "modalRendered = false;" in else_part

    def test_base_template_declares_favicon(self):
        """基础模板声明站点图标，避免浏览器默认请求 /favicon.ico 返回 404。"""
        base = (_REPO_ROOT / "templates/base.html").read_text(encoding="utf-8")
        assert 'rel="icon"' in base
        assert "/static/logo.png" in base
