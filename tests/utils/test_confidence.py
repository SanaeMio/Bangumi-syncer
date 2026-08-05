"""置信度分级工具单元测试（功能三）。"""

from app.utils.confidence import (
    classify_confidence,
    confidence_badge_class,
    confidence_label,
)


def test_classify_confidence_high():
    assert classify_confidence(1.0) == "high"
    assert classify_confidence(0.8) == "high"
    assert classify_confidence(0.95) == "high"


def test_classify_confidence_medium():
    assert classify_confidence(0.5) == "medium"
    assert classify_confidence(0.79) == "medium"


def test_classify_confidence_low():
    assert classify_confidence(0.49) == "low"
    assert classify_confidence(0.0) == "low"


def test_classify_confidence_none_and_invalid():
    assert classify_confidence(None) == "low"
    assert classify_confidence("abc") == "low"
    # 字符串数值应被安全转换
    assert classify_confidence("0.9") == "high"
    assert classify_confidence("0.4") == "low"


def test_confidence_label():
    assert confidence_label("high") == "高"
    assert confidence_label("medium") == "中"
    assert confidence_label("low") == "低"
    # 未知等级原样返回
    assert confidence_label("unknown") == "unknown"


def test_confidence_badge_class():
    assert confidence_badge_class("high") == "bg-success"
    assert confidence_badge_class("medium") == "bg-warning text-dark"
    assert confidence_badge_class("low") == "bg-danger"
    # 未知等级回退
    assert confidence_badge_class("x") == "bg-secondary"
