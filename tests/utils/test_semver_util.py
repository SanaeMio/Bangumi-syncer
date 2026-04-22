import app.utils.semver_util as semver_util
from app.utils.semver_util import (
    is_less_than,
    is_strictly_newer,
    normalize_version_label,
    version_sort_key,
    version_tuple,
)


def test_normalize_version_label():
    assert normalize_version_label("  v1.2.3  ") == "1.2.3"
    assert normalize_version_label("V2.0.0") == "2.0.0"
    assert normalize_version_label("") == ""
    assert normalize_version_label("  ") == ""


def test_version_tuple_core_only():
    assert version_tuple("1.2.3") == (1, 2, 3)
    assert version_tuple("v10.20.30-rc1") == (10, 20, 30)
    assert version_tuple("0.0.0.dev") == (0, 0, 0)
    assert version_tuple("2.1-beta") == (2, 1, 0)


def test_is_less_than_stable():
    assert is_less_than("1.0.0", "2.0.0") is True
    assert is_less_than("2.0.0", "2.0.0") is False
    assert is_less_than("0.0.0.dev", "3.7.0") is True


def test_is_strictly_newer():
    assert is_strictly_newer("2.0.0", "1.0.0") is True
    assert is_strictly_newer("1.0.0", "2.0.0") is False
    assert is_strictly_newer("1.0.0", "1.0.0") is False
    assert is_strictly_newer("1.0.0", "1.0.0-rc.1") is True


def test_is_less_than_prerelease_vs_release():
    assert is_less_than("1.0.0-rc.1", "1.0.0") is True
    assert is_less_than("1.0.0", "1.0.0-rc.2") is False
    assert is_less_than("1.0.0-beta", "1.0.0-rc.1") is True


def test_is_less_than_among_prereleases():
    assert is_less_than("1.0.0-rc.1", "1.0.0-rc.2") is True
    assert is_less_than("1.0.0-alpha", "1.0.0-alpha.1") is True
    assert is_less_than("2.0.0-beta.2", "2.0.0-beta.10") is True


def test_is_less_than_numeric_prerelease_segment():
    """同段内纯数字按整数大小。"""
    assert is_less_than("1.0.0-1", "1.0.0-2") is True


def test_numeric_prerelease_identifier_before_alpha():
    """SemVer：纯数字预发布标识符先于含字母的标识符。"""
    assert is_less_than("1.0.0-1", "1.0.0-alpha") is True
    assert is_strictly_newer("1.0.0-alpha", "1.0.0-1") is True


def test_version_sort_key_orders_ascending_old_to_new():
    tags = [
        "1.0.0-rc.1",
        "1.0.0",
        "1.0.0-beta",
        "1.0.0-rc.2",
    ]
    sorted_tags = sorted(tags, key=version_sort_key)
    assert sorted_tags == [
        "1.0.0-beta",
        "1.0.0-rc.1",
        "1.0.0-rc.2",
        "1.0.0",
    ]


def test_build_metadata_ignored():
    assert is_less_than("1.0.0-rc.1+sha.1", "1.0.0-rc.2+sha.2") is True
    assert is_less_than("1.0.0-rc.2+sha", "1.0.0") is True


def test_digits_chunk_to_int_valueerror_branch():
    def boom(_x: str) -> int:
        raise ValueError

    assert semver_util._digits_chunk_to_int("1", _int=boom) == 0


def test_digits_chunk_to_int_empty_chunk():
    assert semver_util._digits_chunk_to_int("") == 0
    assert version_tuple("x.y.z") == (0, 0, 0)


def test_split_core_prerelease_trailing_hyphen_returns_full_string():
    """``1.0.0-``：预发布段为空时返回整串与 ``None``（``_split_core_prerelease`` 早退）。"""
    assert semver_util._split_core_prerelease("1.0.0-") == ("1.0.0-", None)


def test_prerelease_segment_key_empty_segment():
    assert semver_util._prerelease_segment_key("") == (1, "")
