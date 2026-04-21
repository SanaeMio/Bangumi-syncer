"""简单 semver 比较（主版本.次版本.补丁），用于与 GitHub tag 对比。"""

from __future__ import annotations


def normalize_version_label(v: str) -> str:
    """去掉首尾空白与常见前缀 v。"""
    s = (v or "").strip()
    if s.startswith("v") or s.startswith("V"):
        s = s[1:].strip()
    return s


def version_tuple(v: str) -> tuple[int, int, int]:
    """
    将版本号解析为三元组；无法解析的数字段视为 0。
    预发布后缀（如 1.0.0-rc1）在第一个非数字字符处截断主三元组。
    """
    base = normalize_version_label(v).split("+", 1)[0].split("-", 1)[0]
    parts = base.split(".")
    nums: list[int] = []
    for p in parts[:3]:
        chunk = "".join(ch for ch in p if ch.isdigit())
        try:
            nums.append(int(chunk) if chunk else 0)
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def is_less_than(a: str, b: str) -> bool:
    """若 a 的 semver 三元组严格小于 b，返回 True。"""
    return version_tuple(a) < version_tuple(b)


def is_strictly_newer(remote: str, base: str) -> bool:
    """若 remote 的 semver 严格大于 base（即 base < remote）。"""
    return is_less_than(base, remote)
