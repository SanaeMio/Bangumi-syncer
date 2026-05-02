"""
SequenceMatcher vs rapidfuzz 对比测试

运行方式: uv run python tests/test_matching_comparison.py
"""

import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

from difflib import SequenceMatcher

from rapidfuzz import fuzz

TEST_CASES = [
    ("進撃の巨人", "进击的巨人", "简繁体/中日文差异"),
    ("鬼灭之刃", "鬼滅の刃", "简繁体差异"),
    ("我推的孩子", "【推しの子】", "括号和特殊字符"),
    ("赛马娘", "ウマ娘 プリティーダービー", "完全不同的语言"),
    ("孤独摇滚", "ぼっち・ざ・ろっく！", "中文简称 vs 日文全称"),
    ("间谍过家家", "SPY×FAMILY", "中文 vs 英文"),
    ("咒术回战", "呪術廻戦", "简繁体差异(术/術)"),
    ("辉夜大小姐想让我告白", "かぐや様は告らせたい", "中文 vs 日文"),
    ("测试番剧第一季", "测试番剧", "包含关系"),
    ("测试番剧", "测试番剧第一季", "反向包含关系"),
    ("测试番剧第二季", "测试番剧", "季数差异"),
    ("测试 番剧", "测试番剧", "空格差异"),
    ("TestShowTitle", "TestShowName", "英文近似"),
    ("TestShowTitle", "TestShowTitleExtra", "英文包含"),
    ("ABC", "ABD", "短字符串微小差异"),
    ("测试番剧标题一二三", "测试番剧标题一二", "几乎相同(差一字)"),
    ("进击的巨人 最终季", "进击的巨人", "后缀差异"),
    ("魔法少女小圆", "魔法少女まどか☆マギカ", "中文简称 vs 日文"),
    ("从零开始的异世界生活", "Re:ゼロから始める異世界生活", "中文 vs 日文+英文"),
    ("刀剑神域", "ソードアート・オンライン", "中文 vs 日文"),
    ("测试番剧标题A", "测试番剧标题B", "~0.85相似度"),
    ("测试番剧标题一二", "测试番剧标题三四", "~0.6相似度"),
    ("abcdef", "abcdeg", "6字符差1字符"),
    ("abcdefghij", "abcdefghik", "10字符差1字符"),
    ("测试一二三四五", "测试一二三四六", "中文7字符差1字符"),
    ("完全不同的标题", "另一个无关的名称", "低相似度"),
    ("Attack on Titan", "Attack on Titan Final Season", "英文包含+后缀"),
    ("我的英雄学院", "僕のヒーローアカデミア", "中文 vs 日文"),
    ("Re:Creators", "Re:CREATORS", "大小写差异"),
    ("Fate/Zero", "Fate/stay night", "同系列不同作品"),
    ("", "", "两个空字符串"),
    ("abc", "", "一个空字符串"),
    ("你好世界", "你好世界", "中文完全相同"),
    ("你好世界", "你好地球", "中文差一半"),
    ("a" * 100, "a" * 99 + "b", "长字符串差1字符"),
]

THRESHOLDS = [
    (0.7, "bangumi_data.py:586", "模糊匹配中文标题"),
    (0.9, "bangumi_data.py:682", "关键字符匹配"),
    (0.9, "bangumi_data.py:725", "高度相似判定"),
    (0.4, "bangumi_data.py:396", "部分匹配阈值"),
    (0.6, "bangumi_data.py:506", "模糊匹配采纳阈值"),
    (0.5, "bangumi_api.py:932", "API搜索相似度阈值"),
]


def main():
    print("=" * 90)
    print("SequenceMatcher vs rapidfuzz 对比测试 (替换后验证)")
    print("=" * 90)
    print()

    crossing_cases = 0
    all_crossings = []

    for s1, s2, desc in TEST_CASES:
        sm = SequenceMatcher(None, s1, s2).ratio()
        rf = fuzz.ratio(s1, s2) / 100.0
        diff = abs(sm - rf)

        crossings = []
        for threshold, _location, tdesc in THRESHOLDS:
            if (sm > threshold) != (rf > threshold):
                crossings.append(f"  [!] {tdesc} @ {threshold}")

        if crossings:
            crossing_cases += 1
            all_crossings.append((desc, s1, s2, sm, rf, crossings))

        mark = " [CROSS]" if crossings else ""
        print(f"{desc:<35} SM={sm:.4f} RF={rf:.4f} diff={diff:.4f}{mark}")

    print()
    print("=" * 90)
    print(f"Total: {len(TEST_CASES)} cases, {crossing_cases} crossings")
    print("=" * 90)

    if all_crossings:
        print("\nThreshold crossings:")
        for desc, s1, s2, sm, rf, crossings in all_crossings:
            print(f"\n  {desc}: '{s1}' vs '{s2}'")
            print(f"  SM={sm:.4f}, RF={rf:.4f}")
            for c in crossings:
                print(c)
    else:
        print("\n[OK] No threshold crossings. Safe to replace.")


if __name__ == "__main__":
    main()
