"""MappingService 与真实 bangumi_mapping.json 路径（tmp cwd）。"""

import json
from unittest.mock import patch

from app.services.mapping_service import MappingService


def test_load_creates_default_when_no_mapping_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    svc = MappingService()
    m = svc.load_custom_mappings()
    fp = tmp_path / "bangumi_mapping.json"
    assert fp.is_file()
    assert isinstance(m, dict)
    assert "假面骑士加布" in m


def test_load_reads_existing_file_and_uses_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fp = tmp_path / "bangumi_mapping.json"
    fp.write_text(
        json.dumps({"mappings": {"番A": "111", "番B": "222"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    svc = MappingService()
    a = svc.load_custom_mappings()
    b = svc.load_custom_mappings()
    assert a == b == {"番A": "111", "番B": "222"}


def test_load_corrupt_json_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fp = tmp_path / "bangumi_mapping.json"
    fp.write_text("{not valid json", encoding="utf-8")
    svc = MappingService()
    assert svc.load_custom_mappings() == {}


def test_reload_clears_and_rereads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fp = tmp_path / "bangumi_mapping.json"
    fp.write_text(json.dumps({"mappings": {"x": "1"}}), encoding="utf-8")
    svc = MappingService()
    assert svc.load_custom_mappings() == {"x": "1"}
    fp.write_text(json.dumps({"mappings": {"y": "2"}}), encoding="utf-8")
    out = svc.reload_custom_mappings()
    assert out == {"y": "2"}


def test_get_mappings_status_reflects_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fp = tmp_path / "bangumi_mapping.json"
    fp.write_text(json.dumps({"mappings": {"k": "9"}}), encoding="utf-8")
    svc = MappingService()
    svc.load_custom_mappings()
    st = svc.get_mappings_status()
    assert st["mappings_count"] == 1
    assert st["mappings"] == {"k": "9"}
    assert st["file_path"] and str(st["file_path"]).endswith("bangumi_mapping.json")


def test_delete_custom_mapping_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fp = tmp_path / "bangumi_mapping.json"
    fp.write_text(
        json.dumps({"mappings": {"keep": "1", "gone": "2"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    svc = MappingService()
    assert svc.delete_custom_mapping("gone") is True
    data = json.loads(fp.read_text(encoding="utf-8"))
    assert data["mappings"] == {"keep": "1"}


def test_update_custom_mappings_write_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fp = tmp_path / "bangumi_mapping.json"
    fp.write_text(json.dumps({"mappings": {}}), encoding="utf-8")
    svc = MappingService()
    with patch("builtins.open", side_effect=OSError("disk full")):
        assert svc.update_custom_mappings({"a": "b"}) is False


def test_load_create_default_fails_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    svc = MappingService()
    with patch("os.path.exists", return_value=False):
        with patch("builtins.open", side_effect=OSError("cannot create")):
            assert svc.load_custom_mappings() == {}
