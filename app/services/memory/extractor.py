"""记忆提取器：总结执行后提炼一行摘要并写入记忆（含消费标记）。

摘要成功 → 正常记忆行；摘要失败（LLM 异常/空响应）→ 写 summary 留空的
「消费占位行」，使本次 run 在记忆表中有归属、消费标记生效（见 B1 修订）。
"""

from __future__ import annotations

from app.core.database.agent_memory import AgentMemoryRepository
from app.core.logging import logger
from app.models.memory import MemoryEntry
from app.services.llm import Message, get_llm_client
from app.services.llm.models import ChatResponse

_SUMMARY_PROMPT = (
    "请用一句话总结以下追番总结的内容（50–100 字为宜，根据内容量自然把握），"
    "保留关键信息：看了哪些番剧、进度、异常情况。只输出摘要本身。"
)


class MemoryExtractor:
    """提炼一行摘要写入记忆：成功写正常行，失败写「消费占位行」（B1）。"""

    def __init__(self, repo: AgentMemoryRepository, llm_client=None):
        self._repo = repo
        # 测试可注入；None 时 _summarize 内现取全局单例（LLM 配置保存会
        # reset_llm_client()，缓存实例会滞留旧 api_base/key）
        self._llm = llm_client

    async def extract_and_store(
        self,
        task_type: str,
        task_id: str,
        run_id: str,
        messages: list[Message],  # 总结调用的完整对话上下文（缓存前缀 + 摘要来源）
        response: ChatResponse,  # 总结响应（含全文 content）
        outcome: str,
        tokens_used: int,
        record_ids: list[int],  # 今日明细记录 id（store_and_mark 标记消费用）
        job_name: str | None = None,  # 摘要调用用量归属 llm_usage 用
    ) -> None:
        summary = await self._summarize(messages, response, job_name=job_name)
        if summary:
            entry = MemoryEntry(
                task_type=task_type,
                task_id=task_id,
                run_id=run_id,
                summary=summary,
                full_text=response.content,  # 全文存 full_text（回溯用，不注入）
                outcome=outcome,
                tokens_used=tokens_used,
            )
        else:
            # B1 消费占位行：摘要失败（LLM 异常/空响应）不再直接 return，而是写一条
            # summary 留空的占位记忆——让本次 run 在记忆表中有归属，store_and_mark
            # 同事务打上消费标记，消费排除因此生效，下次调度不再重复总结同一批记录
            # （避免重复通知 + token 白烧）。空 summary 由读取侧过滤，不注入、不统计；
            # full_text 照存主总结全文供回溯，outcome 标记失败来源。
            entry = MemoryEntry(
                task_type=task_type,
                task_id=task_id,
                run_id=run_id,
                summary="",  # NOT NULL 用空串，读取侧据此过滤
                full_text=response.content or "",  # 主总结全文照存（回溯用）
                outcome="summary_failed",
                tokens_used=tokens_used,  # 与成功行同口径（主调用 token）
            )
        # 原子单元：INSERT 记忆 + 标记消费（同一事务，见 store_and_mark）。
        # 占位行与成功行同待遇：写库失败仍抛异常，交给 execute_job 的
        # _STAGE_STORE 失败通知机制（不在此吞异常）。
        self._repo.store_and_mark(entry, record_ids=record_ids)
        # 清理旧记忆（独立 best-effort 事务，失败不回滚上面的 run）
        # L4：魔数收编——保留上限与 prune 上限同源（closeout §4 E3）
        # 占位行也参与 1000 条上限，与成功行一致
        self._repo.prune(task_type, task_id, keep=1000)

    async def _summarize(
        self,
        messages: list[Message],
        response: ChatResponse,
        job_name: str | None = None,
    ) -> str:
        """一行摘要：复用总结调用的完整对话上下文作前缀，命中 LLM prompt 缓存。

        缓存利用：摘要调用紧跟总结调用（同一 execute_job 内，Anthropic 5 分钟
        TTL / OpenAI 自动前缀缓存）——完整历史作前缀，输入 token 按缓存价格，
        且无截断信息损失。job_name 使摘要调用 token 在 llm_usage 中归属任务
        （与主调用同组，用量统计口径完整）。
        """
        if not response.content:
            return ""
        try:
            summary_messages = list(messages)
            summary_messages.append(Message(role="assistant", content=response.content))
            summary_messages.append(Message(role="user", content=_SUMMARY_PROMPT))
            llm = self._llm or get_llm_client()
            resp = await llm.chat(summary_messages, job_name=job_name)
            if resp.content:
                return resp.content.strip()
        except Exception as e:
            # LLM 摘要失败：返回空串，由 extract_and_store 写消费占位行——不保留
            # 截断兜底，保证所有非空入库摘要均为 LLM 完整输出、零截断
            # （见 closeout §4 修订）
            logger.warning(f"摘要 LLM 调用失败，将写消费占位行: {e}")
        return ""
