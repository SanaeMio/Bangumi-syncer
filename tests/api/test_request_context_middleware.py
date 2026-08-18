"""
X-Request-ID 请求上下文中间件测试
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import get_request_id
from app.main import request_context_middleware


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/echo")
    async def echo():
        return {"request_id": get_request_id()}

    app.middleware("http")(request_context_middleware)
    return app


def test_echo_with_provided_request_id():
    app = _make_app()
    client = TestClient(app)
    r = client.get("/echo", headers={"X-Request-ID": "custom-id-42"})
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "custom-id-42"
    assert r.json() == {"request_id": "custom-id-42"}


def test_echo_generates_request_id_when_absent():
    app = _make_app()
    client = TestClient(app)
    r = client.get("/echo")
    assert r.status_code == 200
    rid = r.headers["X-Request-ID"]
    assert len(rid) == 12
    assert rid == r.json()["request_id"]


def test_invalid_request_id_is_regenerated():
    app = _make_app()
    client = TestClient(app)
    # ]/] 会破坏日志行头结构，应被拒绝并重新生成
    r = client.get("/echo", headers={"X-Request-ID": "bad]id[payload"})
    assert r.status_code == 200
    rid = r.headers["X-Request-ID"]
    assert rid != "bad]id[payload"
    assert len(rid) == 12


def test_request_id_context_reset_after_request():
    """请求结束后上下文中不再残留 request_id"""
    app = _make_app()
    client = TestClient(app)
    with client:
        r = client.get("/echo", headers={"X-Request-ID": "ctx-reset-test"})
        assert r.status_code == 200
    assert get_request_id() == ""
