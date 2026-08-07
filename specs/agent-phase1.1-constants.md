# Phase 1.1: LLM 常量抽取重构

> 所属计划：Bangumi-Syncer Agent 化三步增量计划
> 前置依赖：Phase 1（Anthropic 协议支持）
> 交付物：provider 常量 + `ThinkingLevel` Literal 别名
> 执行时机：Phase 1 之后、Phase 2 之前（改动文件都是 Phase 1 动过的，作为 Phase 1 的小尾巴；Phase 2/2.1/2.2 可直接引用抽好的常量）

## 目标

把 LLM 相关的**契约字符串**抽为模块级常量，消除字面量散布。纯重构，行为零变化。

**为什么只抽这两类**（策略详见对话结论）：跨文件复用的契约值 + 枚举语义的值。一次性文案、单函数键名不抽——Python 常量无编译期保障，全局抽取是负收益。

## 范围

### 做什么

**1. provider 常量**：`"openai_compat"` / `"anthropic_compat"` 抽为模块级常量

现状散布位置：

| 位置 | 用途 |
|---|---|
| `app/core/config.py:592` | `get_llm_config()` 默认值 |
| `app/core/database/llm_usage.py:89,119` | 数据库 schema 默认值 + 类型标注 |
| `app/services/llm/client.py:20` | `_PROVIDER_MAP` 工厂键 |

放置：新增 `app/services/llm/constants.py`（跨文件共享契约，沿用项目 `utils/bangumi_constants.py` / `text_constants.py` 惯例）：

```python
# app/services/llm/constants.py

PROVIDER_OPENAI_COMPAT = "openai_compat"
PROVIDER_ANTHROPIC_COMPAT = "anthropic_compat"

# _PROVIDER_MAP 合法键集合，供 client.py 校验提示复用
SUPPORTED_PROVIDERS = (PROVIDER_OPENAI_COMPAT, PROVIDER_ANTHROPIC_COMPAT)
```

调用方引用：`config.py`、`llm_usage.py`、`client.py` 改为 `from app.services.llm.constants import ...`。

**2. `ThinkingLevel` Literal 别名**：`app/services/llm/models.py`

```python
from typing import Literal

ThinkingLevel = Literal["off", "low", "medium", "high"]
```

- provider 构造函数 `thinking_level: ThinkingLevel` 与 `_build_request` 的 kwargs 处理用此类型标注
- 比常量类更 Pythonic：构造与校验由类型系统兜底，不需要运行时比较

**3. ContentBlock `type`**：已用 `Literal["text"]` 等，**不动**

**4. 日志/提示文案**：**不抽**（保持现状）

### 不做什么

- 不抽单函数内一次性的键名/文案
- 不搞 Java 式集中 Constants 类
- 不改 provider 行为（纯重构，行为零变化）
- 不改测试中的字面量（测试断言字符串保持原样）

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `app/services/llm/constants.py` | provider 常量 + SUPPORTED_PROVIDERS |
| 修改 | `app/services/llm/models.py` | 新增 `ThinkingLevel` Literal 别名 |
| 修改 | `app/services/llm/client.py` | `_PROVIDER_MAP` 改用常量 |
| 修改 | `app/core/config.py` | `get_llm_config()` 默认值改用常量 |
| 修改 | `app/core/database/llm_usage.py` | 默认值改用常量 |
| 修改 | `app/services/llm/providers/anthropic.py`（Phase 1 已建） | `thinking_level` 类型标注为 `ThinkingLevel` |

> 总计：新增 1 个文件，修改 5 个文件

## 验证方式

1. Phase 1 全部测试通过（纯重命名，零行为变化）
2. `grep -rn '"openai_compat"' app/` 无命中（测试文件除外，断言字符串保留）
3. 全量回归 `uv run pytest tests/ -q` 通过
