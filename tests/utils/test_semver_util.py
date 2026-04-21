from app.utils.semver_util import is_less_than, normalize_version_label, version_tuple


def test_normalize_version_label():
    assert normalize_version_label("  v1.2.3  ") == "1.2.3"
    assert normalize_version_label("V2.0.0") == "2.0.0"


def test_version_tuple():
    assert version_tuple("1.2.3") == (1, 2, 3)
    assert version_tuple("v10.20.30-rc1") == (10, 20, 30)
    assert version_tuple("0.0.0.dev") == (0, 0, 0)


def test_is_less_than():
    assert is_less_than("1.0.0", "2.0.0") is True
    assert is_less_than("2.0.0", "2.0.0") is False
    assert is_less_than("0.0.0.dev", "3.7.0") is True
