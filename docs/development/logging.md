---
title: 🧾 日志分级约定
order: 9
---

# 🧾 日志分级约定

本项目使用自研 Logger（[`app/core/logging.py`](https://github.com/SanaeMio/Bangumi-syncer/blob/main/app/core/logging.py)，print 实现），
默认级别由 `[dev] log_level` 控制（**默认 `INFO`**）。因此：**`logger.debug` 在默认部署下不可见**，
它不能承载「发生了什么」的唯一记录。

## 级别判据

| 级别 | 适用场景 | 判据 |
| --- | --- | --- |
| `ERROR` | 终态失败 / 需人工干预 / 数据不可恢复 | 「这次彻底失败了」 |
| `WARNING` | 异常被捕获并降级、防御兜底触发、数据/协议异常被跳过、外部依赖不可达、降级到可能**过期**数据 | 「事情没按预期发生，但已兜住」——必须默认可见 |
| `INFO` | 关键决策点与分支流转：run/任务级跳过、恢复分派、降级到备用路径、协议降级、通知抑制、缓存策略切换 | 「本可以走另一条路，最后走了这条」——**低频**（每个 run/请求几条） |
| `DEBUG` | 高频细节：逐事件、逐候选、逐工具、逐轮、逐请求 | 量级约束：**新增日志不得使用 `debug` 进入「每轮/每事件」热路径** |

两条硬规则：

1. **`except` 块内禁止 `logger.debug`**：被吞异常/降级用 `WARNING`，重抛前的分支说明至少 `INFO`
   （有 AST 守卫 `tests/services/agent/test_log_levels.py`，范围为 agent 生态）。
2. **`debug` 不得作为唯一记录**：若同一信息已有 span / `match_trace` 等结构化记录，可留 `debug`；
   否则至少 `INFO`。

## agent 生态的落地口径（示例）

- 恢复续跑的**分派分支**（缺失工具无 name 跳过、非只读/未注册回填占位、收尾轮判定）→ `INFO`；
- 补执行工具**失败**、记录器违约、replay 数据异常、工具结果 JSON 降级 → `WARNING`；
- 每轮的停点/延迟执行/terminal 抑制等**轮内细节** → `DEBUG`（span 已覆盖）；
- 「本轮无停点 → 协议降级为轮级执行」按**轮**聚合为一条 `INFO`（避免逐工具刷屏）。

## 与测试配合

- 控制台/文件输出受 `log_level` 阈值过滤，但 **Logger 监听器不受阈值限制**
  （`logger.add_listener(callback)`，`callback(log_line, level)`）——测试用监听器可捕获
  任意级别，例如 `tests/services/agent/test_replay.py` 的 `log_records` fixture；
- **stdlib logging 与自研 Logger 并存**：`agent/execution/tools.py`、`agent/loop.py`、`llm/sse.py`
  用 `logging.getLogger(__name__)` → pytest `caplog` 可捕获；其余模块用自研 Logger →
  须用 `add_listener`（`caplog` 捕不到）；
- 级别调整应同步「级别断言」测试，防止后续被静默降回 `debug`。
