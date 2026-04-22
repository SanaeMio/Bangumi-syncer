"""SemVer 2.0 风格比较（主.次.补丁 + 可选预发布），用于与 GitHub tag 对比。"""

from __future__ import annotations

from typing import Callable


def normalize_version_label(v: str) -> str:
    """去掉首尾空白与常见前缀 v。"""
    s = (v or "").strip()
    if s.startswith("v") or s.startswith("V"):
        s = s[1:].strip()
    return s


def _digits_chunk_to_int(chunk: str, _int: Callable[[str], int] = int) -> int:
    """将仅含数字的片段转为 int；异常时回退 0（供单测注入 ``_int`` 覆盖异常分支）。"""
    if not chunk:
        return 0
    try:
        return _int(chunk)
    except ValueError:
        return 0


def _core_numeric_parts(core: str) -> tuple[int, int, int]:
    parts = (core or "").strip().split(".")
    nums: list[int] = []
    for p in parts[:3]:
        chunk = "".join(ch for ch in p if ch.isdigit())
        nums.append(_digits_chunk_to_int(chunk))
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _split_core_prerelease(normalized: str) -> tuple[str, str | None]:
    """去掉 build 元数据后，按第一个 ``-`` 拆成 core 与 prerelease（可含多个 ``.`` 段）。"""
    s = (normalized or "").strip().split("+", 1)[0].strip()
    if "-" not in s:
        return s, None
    i = s.index("-")
    core, pr = s[:i].strip(), s[i + 1 :].strip()
    if not pr:
        return s, None
    return core, pr


def _prerelease_segment_key(seg: str) -> tuple[int, int | str]:
    """
    单段预发布标识符的比较键。
    纯数字段按整数比较；否则按 ASCII；数字段恒小于非数字段（SemVer 2.0.0）。
    """
    if not seg:
        return (1, "")
    if seg.isdigit():
        return (0, int(seg))
    return (1, seg)


def _prerelease_tuple(prerelease: str) -> tuple[tuple[int, int | str], ...]:
    parts = [p for p in prerelease.split(".") if p != ""]
    return tuple(_prerelease_segment_key(p) for p in parts)


def version_sort_key(
    v: str,
) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    """
    用于排序 / 比较的键：升序即「从旧到新」。
    同 ``x.y.z`` 下：任意预发布 < 正式版；预发布之间按 SemVer 预发布规则逐段比较。
    """
    norm = normalize_version_label(v)
    core, pre = _split_core_prerelease(norm)
    maj, mino, pat = _core_numeric_parts(core)
    if pre is None:
        return (maj, mino, pat, 1, ())
    return (maj, mino, pat, 0, _prerelease_tuple(pre))


def version_tuple(v: str) -> tuple[int, int, int]:
    """
    仅主版本三元组（忽略预发布与 build），用于兼容旧逻辑或快速取号段。
    """
    norm = normalize_version_label(v)
    core, _ = _split_core_prerelease(norm)
    return _core_numeric_parts(core)


def is_less_than(a: str, b: str) -> bool:
    """若 a 按 SemVer 严格早于 b，返回 True。"""
    return version_sort_key(a) < version_sort_key(b)


def is_strictly_newer(remote: str, base: str) -> bool:
    """若 remote 严格晚于 base（即 base < remote）。"""
    return is_less_than(base, remote)
