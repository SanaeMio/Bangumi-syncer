"""app.core.app_version 版本解析。"""

import json

from app.core import app_version as av


def test_get_version_name_and_description():
    assert av.get_version_name() == av.VERSION_NAME
    assert av.VERSION_DESCRIPTION


def test_get_version_env_overrides_manifest(tmp_path, monkeypatch):
    manifest = tmp_path / "release_manifest.json"
    manifest.write_text(
        json.dumps({"version": "9.9.9", "git_sha": "abc"}), encoding="utf-8"
    )
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("APP_VERSION", "1.0.0-env")
    try:
        assert av.get_version() == "1.0.0-env"
        assert av.get_display_version() == "v1.0.0-env"
        info = av.get_version_info()
        assert info["version"] == "1.0.0-env"
        assert info["display_version"] == "v1.0.0-env"
        assert av.get_full_name() == f"{av.VERSION_NAME} v1.0.0-env"
    finally:
        monkeypatch.delenv("APP_VERSION", raising=False)


def test_get_version_from_manifest(tmp_path, monkeypatch):
    manifest = tmp_path / "release_manifest.json"
    manifest.write_text(
        json.dumps({"version": "3.8.1", "git_sha": "deadbeef"}), encoding="utf-8"
    )
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert av.get_version() == "3.8.1"


def test_get_version_dev_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert av.get_version() == av._DEV_PLACEHOLDER
    assert av.get_display_version() == "v0.0.0.dev"


def test_get_display_version_idempotent_v_prefix():
    assert av.get_display_version("3.2.1") == "v3.2.1"
    assert av.get_display_version("v4.0.0") == "v4.0.0"
    assert av.get_display_version("V5.0.1") == "V5.0.1"


def test_get_version_invalid_manifest_ignored(tmp_path, monkeypatch):
    (tmp_path / "release_manifest.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert av.get_version() == av._DEV_PLACEHOLDER
