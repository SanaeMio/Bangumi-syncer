"""web_templates：Jinja2 单例与公开路径注入。"""

import json

import pytest
from markupsafe import Markup

import app.core.web_templates as web_templates


@pytest.fixture(autouse=True)
def _reset_template_singleton():
    web_templates._templates = None
    yield
    web_templates._templates = None


def test_get_templates_singleton_registers_env(monkeypatch):
    monkeypatch.setattr(
        "app.core.web_templates.get_public_base_path", lambda: "/Bangumi"
    )
    t1 = web_templates.get_templates()
    t2 = web_templates.get_templates()
    assert t1 is t2
    env = t1.env
    assert "p" in env.filters
    assert env.globals["get_public_base_path"]() == "/Bangumi"
    raw = env.globals["public_base_path_json"]()
    assert isinstance(raw, Markup)
    assert json.loads(str(raw)) == "/Bangumi"


def test_filter_p_delegates_to_join_public(monkeypatch):
    monkeypatch.setattr("app.core.web_templates.get_public_base_path", lambda: "")
    env = web_templates.get_templates().env
    assert env.filters["p"]("/x") == "/x"
