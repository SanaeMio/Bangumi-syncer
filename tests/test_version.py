"""根目录 version 模块（供 health 等引用）。"""

import version as ver


def test_get_version_and_name():
    assert ver.get_version() == ver.VERSION
    assert ver.get_version_name() == ver.VERSION_NAME


def test_get_full_name_and_version_info():
    assert ver.VERSION_NAME in ver.get_full_name()
    info = ver.get_version_info()
    assert info["version"] == ver.VERSION
    assert info is not ver.VERSION_INFO
