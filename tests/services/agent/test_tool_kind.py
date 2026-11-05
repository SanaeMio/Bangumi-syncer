"""ToolKind 能力派生测试。

单枚举锁定四种合法状态，派生方法只读，调用方无法组合出
terminal+idempotent 之类的非法状态。
"""

import pytest

from app.services.agent.protocols import (
    ToolKind,
    is_idempotent,
    is_terminal,
    needs_write_audit,
)


class TestToolKindDerivation:
    @pytest.mark.parametrize(
        ("kind", "idem", "term", "audit"),
        [
            (ToolKind.READ, True, False, False),
            (ToolKind.IDEMPOTENT_WRITE, True, False, True),
            (ToolKind.WRITE, False, False, True),
            (ToolKind.TERMINAL, False, True, False),
        ],
    )
    def test_kind_derives_three_capabilities(self, kind, idem, term, audit):
        assert is_idempotent(kind) is idem
        assert is_terminal(kind) is term
        assert needs_write_audit(kind) is audit

    def test_terminal_is_never_idempotent(self):
        """终止工具不执行 handler，不允许被当成幂等工具提前执行或恢复重跑。"""
        assert is_idempotent(ToolKind.TERMINAL) is False

    def test_read_is_never_write_audited(self):
        assert needs_write_audit(ToolKind.READ) is False

    def test_all_kinds_covered(self):
        """新增枚举值必须同步更新派生规则，避免默认落到「只读」。"""
        assert {k.value for k in ToolKind} == {
            "read",
            "idempotent_write",
            "write",
            "terminal",
        }
