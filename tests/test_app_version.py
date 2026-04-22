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


def test_get_version_manifest_not_dict_ignored(tmp_path, monkeypatch):
    (tmp_path / "release_manifest.json").write_text("[1,2,3]", encoding="utf-8")
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert av.get_version() == av._DEV_PLACEHOLDER


def test_get_version_manifest_missing_or_blank_version(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("APP_VERSION", raising=False)
    (tmp_path / "release_manifest.json").write_text(
        '{"git_sha":"abc"}', encoding="utf-8"
    )
    assert av.get_version() == av._DEV_PLACEHOLDER
    (tmp_path / "release_manifest.json").write_text(
        '{"version":"","git_sha":"abc"}', encoding="utf-8"
    )
    assert av.get_version() == av._DEV_PLACEHOLDER


def test_get_version_manifest_numeric_version_coerced(tmp_path, monkeypatch):
    (tmp_path / "release_manifest.json").write_text('{"version":2}', encoding="utf-8")
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert av.get_version() == "2"


def test_get_version_app_version_blank_falls_through_to_manifest(tmp_path, monkeypatch):
    (tmp_path / "release_manifest.json").write_text(
        '{"version":"2.5.0"}', encoding="utf-8"
    )
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("APP_VERSION", "   ")
    try:
        assert av.get_version() == "2.5.0"
    finally:
        monkeypatch.delenv("APP_VERSION", raising=False)


def test_get_display_version_prerelease_and_empty():
    assert av.get_display_version("1.0.0-rc.1") == "v1.0.0-rc.1"
    assert av.get_display_version("v1.0.0-rc.2") == "v1.0.0-rc.2"
    assert av.get_display_version("") == "v0.0.0.dev"
    assert av.get_display_version("   ") == "v0.0.0.dev"


def test_get_version_info_keys(tmp_path, monkeypatch):
    (tmp_path / "release_manifest.json").write_text(
        '{"version":"4.0.1","git_sha":"abc"}', encoding="utf-8"
    )
    monkeypatch.setattr(av, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("APP_VERSION", raising=False)
    info = av.get_version_info()
    assert info["version"] == "4.0.1"
    assert info["display_version"] == "v4.0.1"
    assert info["name"] == av.VERSION_NAME
    assert info["description"] == av.VERSION_DESCRIPTION
    assert info["full_name"] == f"{av.VERSION_NAME} v4.0.1"
