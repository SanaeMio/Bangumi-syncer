"""LLM 契约常量。

集中管理跨模块复用的 provider 名等契约字符串，消除字面量散布
（config.py 默认值、client.py 工厂键、llm_usage.py 默认值）。

本模块不依赖任何其他模块（纯字符串常量），可被任意层安全引用。
"""

PROVIDER_OPENAI_COMPAT = "openai_compat"
PROVIDER_ANTHROPIC_COMPAT = "anthropic_compat"

SUPPORTED_PROVIDERS = (PROVIDER_OPENAI_COMPAT, PROVIDER_ANTHROPIC_COMPAT)
