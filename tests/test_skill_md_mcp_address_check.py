"""SKILL.md 接入前置检查（MCP 地址核对）测试

验证 ``skills/bangumi-syncer/SKILL.md`` 在「场景 B：首次接入与配置」中，
MCP 接入之前加入「地址核对」步骤：非 localhost 访问时先配置
「MCP 服务公共 URL」并重启，再注册 MCP；并在排障章节补对应症状。

SKILL.md 面向 AI 助手引导，不参与 djlint，此处按内容做回归校验。
"""

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parents[1] / "skills" / "bangumi-syncer" / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text():
    return SKILL_PATH.read_text(encoding="utf-8")


def test_address_check_appears_before_mcp_registration(skill_text):
    """「地址核对」应出现在「MCP 接入」之前。"""
    assert "地址核对" in skill_text, "场景 B 应新增『地址核对』小节"
    assert skill_text.index("地址核对") < skill_text.index("MCP 接入"), (
        "『地址核对』必须排在『MCP 接入』之前"
    )


def test_address_check_covers_localhost_and_non_localhost(skill_text):
    """应区分 localhost/127.0.0.1 无需处理，其余需先配置公共 URL。"""
    assert "127.0.0.1" in skill_text, "应说明 127.0.0.1 无需处理"
    assert "无需处理" in skill_text, "应给出 localhost 情形『无需处理』的结论"
    assert "MCP 服务公共 URL" in skill_text, "应引导到管理页『MCP 服务公共 URL』"
    assert "一键填入" in skill_text, "应引导使用『一键填入当前访问地址』"


def test_address_check_explains_oauth_issuer_reason(skill_text):
    """应说明原因：OAuth issuer 必须与客户端访问地址一致。"""
    assert "OAuth issuer" in skill_text, "应说明 OAuth issuer 必须与访问地址一致"
    assert "localhost" in skill_text, (
        "应说明 issuer 为 localhost 会导致客户端授权发现问题"
    )


def test_troubleshooting_has_matching_symptom(skill_text):
    """排障章节应补：授权跳 localhost / 发现失败 / issuer 不匹配 → 检查公共 URL。"""
    assert "授权跳转到 localhost" in skill_text, (
        "排障应包含『授权跳转到 localhost』症状"
    )
    assert "issuer 不匹配" in skill_text, "排障应包含『issuer 不匹配』症状"
    assert "检查「MCP 服务公共 URL」是否与访问地址一致" in skill_text, (
        "排障应给出『检查 MCP 服务公共 URL 是否与访问地址一致』的处置"
    )
