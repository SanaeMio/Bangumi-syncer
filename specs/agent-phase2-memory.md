# Phase 2: 定时任务长期记忆

> 所属计划：Bangumi-Syncer Agent 化三步增量计划
> 前置依赖：Phase 1（ContentBlock 类型）
> 交付物：定时任务第 N 次执行可引用前 N-1 次结果

## 目标

让定时任务（首先是 AI 追番总结 scheduler，后续扩展到其他 scheduler）的第 N 次执行能够引用前 N-1 次执行的结果，打破"每次都从头开始"的限制。

## 核心场景

`summary_scheduler` 每天早上 9 点生成"昨日追番总结"。现在第 10 次执行时，prompt 中只包含"昨天的同步记录"。有了记忆后，prompt 中还会包含：

- 前 9 次总结的摘要（知道之前说过什么）
- 用户反馈/偏好（用户上次说"不要太啰嗦"）
- 历史异常模式（"上周这个时候服务器挂了，数据不完整"）

## 设计

```
每次定时任务执行前：
  1. MemoryRetriever.retrieve(task_id, limit=5)
     → 从 agent_working_memory 查最近的执行摘要
     → 用 FTS5 搜索与当前任务关键词相关的内容
  2. 注入到 LLM prompt 的 "历史上下文" 部分

每次定时任务执行后：
  1. MemoryExtractor.extract(result)
     → 让 LLM 用一句话总结本次执行的关键发现
     → 结构化写入 agent_working_memory
```

## 数据库

```sql
CREATE TABLE agent_working_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,        -- 'summary', 'sync', 'diagnostic', 等
    task_id TEXT NOT NULL,          -- scheduler 名称, 如 'summary-daily'
    run_id TEXT NOT NULL UNIQUE,    -- UUID
    summary TEXT NOT NULL,          -- LLM 生成的一行摘要
    key_findings TEXT,              -- JSON: [{"key": "...", "value": "..."}]
    decisions_taken TEXT,           -- JSON: ["decision1", "decision2"]
    outcome TEXT NOT NULL,          -- 'success', 'partial', 'failed'
    tokens_used INTEGER,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_memory_task ON agent_working_memory(task_type, task_id);
CREATE INDEX idx_memory_created ON agent_working_memory(created_at);

-- FTS5 全文检索
CREATE VIRTUAL TABLE agent_memory_fts USING fts5(
    task_type, summary, key_findings, outcome,
    content='agent_working_memory',
    content_rowid='id'
);
```

## MemoryRetriever / MemoryExtractor

```python
# app/services/memory/retriever.py

class MemoryRetriever:
    """检索与当前任务相关的历史记忆"""

    def __init__(self, repo: AgentMemoryRepository):
        self._repo = repo

    async def retrieve(
        self,
        task_type: str,
        task_id: str,
        limit: int = 5,
        keywords: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """检索历史记忆，按相关性+新鲜度排序"""
        entries = []

        # 1. 最近 N 次同任务执行
        recent = await self._repo.get_recent(task_type, task_id, limit=limit)
        entries.extend(recent)

        # 2. 关键词搜索 (FTS5)
        if keywords:
            kw_results = await self._repo.search_fts(
                " ".join(keywords), limit=limit
            )
            entries.extend(kw_results)

        # 3. 去重 + 排序
        return self._deduplicate_and_rank(entries, limit)

class MemoryExtractor:
    """从执行结果中提取关键信息写入记忆"""

    async def extract_and_store(
        self,
        task_type: str,
        task_id: str,
        run_id: str,
        llm_response: str,
        decisions: list[str],
        outcome: str,
        tokens_used: int,
    ) -> None:
        # 用 LLM 生成一行摘要（或用简单规则截取）
        summary = await self._summarize(llm_response)
        await self._repo.insert(MemoryEntry(
            task_type=task_type,
            task_id=task_id,
            run_id=run_id,
            summary=summary,
            key_findings=self._extract_key_findings(llm_response),
            decisions_taken=json.dumps(decisions),
            outcome=outcome,
            tokens_used=tokens_used,
        ))
        # 清理旧记忆（每个 task 最多保留 100 条）
        await self._repo.prune(task_type, task_id, keep=100)
```

## 接入现有 SummaryScheduler

在 `app/services/summary/service.py` 的 `execute_job()` 中：

```python
async def execute_job(self, job_config: SummaryJobConfig) -> None:
    task_id = f"summary-{job_config.name}"

    # === 新增：注入记忆 ===
    memory_retriever = MemoryRetriever(self.memory_repo)
    past_memories = await memory_retriever.retrieve(
        task_type="summary",
        task_id=task_id,
        limit=5,
        keywords=[job_config.user_filter or ""],
    )
    memory_context = self._format_memory_context(past_memories)

    # === 原有逻辑 ===
    records = await self._query_records(job_config)
    messages = self._build_messages(records, job_config.system_prompt)

    # === 注入历史上下文到 system prompt ===
    if memory_context:
        messages.insert(0, Message(
            role="system",
            content=f"## 历史执行上下文\n{memory_context}"
        ))

    response = await self.llm_client.chat(messages)

    # === 新增：提取记忆 ===
    memory_extractor = MemoryExtractor(self.memory_repo)
    await memory_extractor.extract_and_store(
        task_type="summary",
        task_id=task_id,
        run_id=str(uuid4()),
        llm_response=response.content,
        decisions=[],   # summary 任务暂无决策
        outcome="success",
        tokens_used=response.usage.total_tokens,
    )

    # === 原有逻辑 ===
    await self._dispatch_notification(response)
```

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `app/services/memory/__init__.py` | 记忆模块包 |
| 新增 | `app/services/memory/retriever.py` | MemoryRetriever + MemoryExtractor |
| 新增 | `app/core/database/agent_memory.py` | AgentMemoryRepository（CRUD + FTS5） |
| 修改 | `app/core/database/connection.py` | `__ensure_agent_memory()` migration |
| 修改 | `app/services/summary/service.py` | `execute_job()` 注入记忆，记忆提取回写 |
| 修改 | `app/services/summary/scheduler.py` | 传递 memory_repo（或共享实例） |

> 总计：新增 3 个文件，修改 3 个文件

## 验证方式

1. 手动触发 summary job 3 次（每次使用不同日期范围的测试数据）
2. 检查 `agent_working_memory` 表，确认 3 条记忆记录
3. 检查第 3 次执行的 LLM 请求日志，确认 system prompt 中包含前 2 次执行的摘要
4. 用 FTS5 搜索关键词，确认能检索到相关记忆
