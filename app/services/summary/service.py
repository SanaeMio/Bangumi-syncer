"""Summary 生成服务 —— 编排数据库查询、LLM 调用和通知发送。"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from app.core.database import database_manager
from app.core.logging import logger
from app.models.memory import MemoryEntry

from ..llm import Message, get_llm_client
from ..memory.service import MemoryService
from ..notification_service import notification_service
from .models import SummaryJobConfig, SummaryRecord

# 内部常量 —— 用户可自定义的 prompt 结构，不暴露到 config.ini
_USER_PROMPT_TEMPLATE = (
    "{date_from} 至 {date_to} 观影记录（共 {record_count} 条）：\n\n{records}"
)

_MEMORY_SECTION = "## 历史执行上下文"

# 执行阶段（execute_job 出错时用于定位失败环节；任何阶段失败都会通知，文案按阶段区分）
_STAGE_QUERY = "query"  # 查询明细 + 注入记忆 + 构建消息
_STAGE_CHAT = "chat"  # LLM 调用
_STAGE_STORE = "store"  # 记忆写入（含消费标记）
_STAGE_NOTIFY = "notify"  # 通知投递

_STAGE_FAILURE_META = {
    _STAGE_QUERY: {
        "inbox_type": "summary_job_failed",
        "inbox_title": "追番总结异常：{name}",
        "inbox_body": "执行异常，请检查任务配置（Cron 表达式、回溯天数等）是否正确",
        "summary_prefix": "追番总结任务执行异常（查询/构建阶段）",
    },
    _STAGE_CHAT: {
        "inbox_type": "summary_llm_failed",
        "inbox_title": "追番总结失败：{name}",
        "inbox_body": "LLM 调用失败，请检查 API 地址和密钥",
        "summary_prefix": "AI 追番总结生成失败（LLM 调用阶段）",
    },
    _STAGE_STORE: {
        "inbox_type": "summary_job_failed",
        "inbox_title": "追番总结异常：{name}",
        "inbox_body": "记忆写入失败，本次执行未记录，下一次将重新总结",
        "summary_prefix": "追番总结任务异常（记忆写入阶段）",
    },
}


class SummaryService:
    """生成 AI 驱动的追番观影总结。"""

    def __init__(self):
        # 记忆统一入口（extractor/retriever 是其内部组件，业务层不直接碰 repository）
        self.memory = MemoryService(database_manager.memory)

    @property
    def llm_client(self):
        """每次取模块级单例——LLM 配置保存会 reset_llm_client()，缓存实例会失效。"""
        return get_llm_client()

    # ------------------------------------------------------------------
    # 查询与构建（Phase 2.0.2 拆解，execute_job / generate_summary 共用）
    # ------------------------------------------------------------------

    def _query_records(
        self, job_config: SummaryJobConfig, incremental: bool = False
    ) -> tuple[list[SummaryRecord], str, str]:
        """计算日期范围并查询记录，返回 (records, date_from, date_to)。

        增量窗口（incremental=True，execute_job 使用）：记忆开启
        （memory_limit>0）且本任务存在历史记忆时，date_from = 本任务最后一条
        记忆的 created_at 日期（只总结上次总结点之后的增量记录）；无历史记忆
        或 preview（generate_summary，incremental=False）时回退 lookback_days。
        """
        now = datetime.now()
        date_to = now.strftime("%Y-%m-%d")

        date_from = (now - timedelta(days=job_config.lookback_days)).strftime(
            "%Y-%m-%d"
        )
        # 增量窗口：记忆开启 + 有历史 → 起点 = 上次总结点（created_at 日期）
        if incremental and job_config.memory_limit > 0:
            task_id = f"summary-{job_config.name}"
            last = self.memory.recent("summary", task_id, limit=1)
            if last and last[0].created_at:
                # "YYYY-MM-DD HH:MM:SS" → 日期；长度不足（异常格式）时跳过
                # 保持 lookback 默认，避免 date_from 变非法字符串
                last_date = last[0].created_at[:10]
                if len(last_date) == 10:
                    date_from = last_date

        # 查询记录（仅记忆开启时携带消费标记做排除，避免无条件加重查询）
        records = database_manager.get_records_in_date_range(
            date_from=date_from,
            date_to=date_to,
            limit=job_config.max_records,
            user_name=job_config.user_name.strip() or None,
            include_consumed=(job_config.memory_limit > 0),
        )
        converted = [
            SummaryRecord(
                id=r["id"],
                timestamp=r["timestamp"],
                user_name=r["user_name"],
                title=r["title"],
                bgm_title=r.get("bgm_title") or "",
                season=r["season"],
                episode=r["episode"],
                media_type=r.get("media_type") or "episode",
                source=r["source"],
                status=r["status"],
                consumed_run_ids=r.get("consumed_run_ids") or set(),
            )
            for r in records
        ]
        return converted, date_from, date_to

    def _build_messages(
        self,
        records: list[SummaryRecord],
        system_prompt: str,
        date_from: str,
        date_to: str,
    ) -> list[Message]:
        """格式化记录并构建 system + user 两条消息。"""
        records_text = self._format_records(records)
        user_content = _USER_PROMPT_TEMPLATE.format(
            date_from=date_from,
            date_to=date_to,
            records=records_text,
            record_count=len(records),
        )
        return [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_content),
        ]

    def _build_memory_context(
        self, job_config: SummaryJobConfig, task_id: str, records: list[SummaryRecord]
    ) -> str:
        """注入文本：recent（最近 N 条摘要）+ related（同剧关联）合并去重。

        合并/去重/排序在 service 层完成（MemoryRetriever 保持通用方法）：
        recent 在前（连续性优先），related 随后（跨窗口回忆）冠 [同剧历史] 前缀；
        按 run_id 去重防双路径命中。
        """
        merged: list[tuple[MemoryEntry, bool]] = []  # (entry, is_related)
        seen: set[str] = set()

        # 1. 最近 N 条摘要（连续性）
        if job_config.memory_limit > 0:
            for e in self.memory.recent(
                "summary", task_id, limit=job_config.memory_limit
            ):
                if e.run_id not in seen:
                    seen.add(e.run_id)
                    merged.append((e, False))

        # 2. 同剧关联（跨窗口回忆；日期倒序最近 N 条，不占 memory_limit 额度）
        if job_config.related_limit > 0:
            titles = list(
                dict.fromkeys(r.bgm_title for r in records if r.bgm_title)
            )  # 去重且保序
            if titles:
                for e in self.memory.related(
                    "summary", task_id, titles, limit=job_config.related_limit
                ):
                    if e.run_id not in seen:
                        seen.add(e.run_id)
                        merged.append((e, True))

        lines = []
        for e, is_related in merged:
            prefix = "[同剧历史] " if is_related else ""
            lines.append(f"- {prefix}{e.summary}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    async def generate_summary(self, job_config: SummaryJobConfig) -> dict:
        """查询数据库，格式化记录，调用 LLM（预览用，不含记忆注入）。

        返回字典，包含以下键：summary_text、model、usage、record_count、
        date_from、date_to。
        """
        records, date_from, date_to = self._query_records(job_config)
        system_prompt = job_config.system_prompt.strip()
        if not system_prompt:
            system_prompt = SummaryJobConfig.system_prompt
        messages = self._build_messages(records, system_prompt, date_from, date_to)

        response = await self.llm_client.chat(
            messages,
            job_name=job_config.name,
        )

        return {
            "summary_text": response.content,
            "model": response.model,
            "usage": response.usage,
            "latency_ms": response.latency,
            "record_count": len(records),
            "date_from": date_from,
            "date_to": date_to,
        }

    async def execute_job(self, job_config: SummaryJobConfig) -> None:
        """完整执行：查询 → 注入记忆 → 调 LLM → 提取记忆 → 发送通知。

        错误处理策略：**总结过程任何阶段出错都向用户发送失败通知**，
        不静默吞掉——只是按失败阶段（_STAGE_*）区分通知类型与文案，
        方便用户定位问题环节。唯一例外：通知投递本身失败（stage=notify）
        时不再二次通知——投递层问题走通知重试/告警兜底，重发可能重复失败。
        """
        task_id = f"summary-{job_config.name}"
        stage = _STAGE_QUERY
        try:
            # 1. 查询明细（增量窗口：记忆开启时起点=上次总结点）
            records, date_from, date_to = self._query_records(
                job_config, incremental=True
            )

            # 记忆开启（memory_limit>0）时：本任务已消费记录不进 prompt（信息由摘要
            # 承继，避免重复总结、提升连贯性）；消费排除**按任务隔离**——仅当记录的
            # consumed_run_ids 与【当前任务】的 run_id 集合有交集才剔除；被其他任务
            # 消费（如年度总结被每日总结消费过的记录）仍保留，跨任务互斥解除。
            # 消费标记只标新记录（extract_and_store 用过滤后的 records 列表）
            memory_enabled = job_config.memory_limit > 0
            if memory_enabled:
                my_run_ids = self.memory.get_task_run_ids("summary", task_id)
                records = [r for r in records if not (r.consumed_run_ids & my_run_ids)]

            # 注入历史上下文：recent（memory_limit>0）与 related（related_limit>0）
            # 各自独立生效（M1：related 不受 memory_limit 门控）；两者皆 0 时短路
            memory_context = ""
            if job_config.memory_limit > 0 or job_config.related_limit > 0:
                memory_context = self._build_memory_context(
                    job_config, task_id, records
                )

            # 历史上下文拼进 system prompt（多 system message 对 OpenAI 兼容端点不安全）
            system_prompt = job_config.system_prompt.strip()
            if not system_prompt:
                system_prompt = SummaryJobConfig.system_prompt
            if memory_context:
                system_prompt = (
                    f"{_MEMORY_SECTION}\n{memory_context}\n\n{system_prompt}"
                )
            messages = self._build_messages(records, system_prompt, date_from, date_to)

            # 2. 调 LLM 生成总结
            stage = _STAGE_CHAT
            response = await self.llm_client.chat(
                messages,
                job_name=job_config.name,
            )

            # 3. 提取记忆（读写同开关：memory_limit=0 不注入也不写入）
            if memory_enabled:
                stage = _STAGE_STORE
                run_id = str(uuid4())  # 单次执行的唯一标识（记忆写入与消费标记共用）
                await self.memory.extract_and_store(
                    task_type="summary",
                    task_id=task_id,
                    run_id=run_id,
                    messages=messages,  # 完整上下文：缓存前缀 + 摘要来源
                    response=response,  # 响应：summary 生成 + full_text 存储
                    outcome="success",
                    tokens_used=response.usage.total_tokens if response.usage else 0,
                    record_ids=[r.id for r in records],  # 同一事务标记消费（仅新记录）
                    job_name=job_config.name,  # 摘要调用用量归属 llm_usage
                )

            # 4. 通知
            stage = _STAGE_NOTIFY
            self._dispatch_notification(
                job_config, response, records, date_from, date_to
            )
        except Exception as e:
            if stage == _STAGE_NOTIFY:
                # 通知投递失败：总结/记忆已成功，消费标记已写，不二次通知
                # （投递层问题走通知重试/告警兜底）。
                logger.error(f"Notification failed for '{job_config.name}': {e}")
                return
            logger.error(
                f"Summary job '{job_config.name}' failed at stage={stage}: {e}"
            )
            self._send_stage_failure_notification(job_config, stage, e)

    def _send_stage_failure_notification(
        self, job_config: SummaryJobConfig, stage: str, error: Exception
    ) -> None:
        """按失败阶段发送差异化失败通知（统一错误出口，便于用户定位环节）。"""
        meta = _STAGE_FAILURE_META[stage]
        self._send_failure_notification(
            job_config,
            f"{meta['summary_prefix']}：{error}",
            inbox_type=meta["inbox_type"],
            inbox_title=meta["inbox_title"].format(name=job_config.name),
            inbox_body=meta["inbox_body"],
        )

    def _dispatch_notification(
        self,
        job_config: SummaryJobConfig,
        response,
        records: list[SummaryRecord],
        date_from: str,
        date_to: str,
    ) -> None:
        """空内容→失败通知 / 正常→成功通知（保持既有失败语义）。"""
        # H1 修正：provider 空内容时 model 可能仍非空，仅以 content 判定失败
        # （空 choices + model 名 会误走成功分支、吞掉失败通知）
        if not response.content:
            summary_text = (
                "AI 追番总结生成失败：LLM 返回空内容（所有重试已耗尽）。\n"
                "请检查 LLM 配置中的 api_base、api_key 是否正确，"
                "以及网络连通性。"
            )
            logger.error(
                f"Summary job '{job_config.name}' LLM 返回空内容，发送失败提示通知"
            )
            self._send_failure_notification(
                job_config,
                summary_text,
                inbox_type="summary_llm_failed",
                inbox_title=f"追番总结失败：{job_config.name}",
                inbox_body="LLM 返回空内容，请检查 API 地址和密钥",
            )
            return

        result = {
            "summary_text": response.content,
            "model": response.model,
            "usage": response.usage,
            "latency_ms": response.latency,
            "record_count": len(records),
            "date_from": date_from,
            "date_to": date_to,
        }
        self._send_success_notification(job_config, result)

    def _send_success_notification(
        self, job_config: SummaryJobConfig, result: dict
    ) -> None:
        """发送成功通知（webhook + 邮件）。

        P5：通过 notification_service.notify() 统一入口发送，仅走 webhook/email 渠道，
        不写站内信（write_in_app=False）。
        """
        user_name = job_config.user_name.strip() if job_config.user_name else ""
        usage = result["usage"]
        notification_service.notify(
            f"watching_summary_{job_config.name}",
            source="summary",
            skip_cooldown=True,
            write_in_app=False,
            job_name=job_config.name,
            user_name=user_name,
            summary_text=result["summary_text"],
            date_range=f"{result['date_from']} ~ {result['date_to']}",
            record_count=result["record_count"],
            lookback_days=job_config.lookback_days,
            model=result["model"],
            tokens_used=usage.total_tokens if usage else 0,
        )

    def _send_failure_notification(
        self,
        job_config: SummaryJobConfig,
        summary_text: str,
        *,
        inbox_type: str,
        inbox_title: str,
        inbox_body: str = "",
    ) -> None:
        """发送失败通知（webhook + 邮件 + 收件箱）。

        P4.7：通过 notification_service.notify() 统一入口发送，替代原先的
        get_notifier().send_notification_by_type() + database_manager.insert_notification()
        显式双调用。webhook/email 类型为 watching_summary_{name}（按 job 配置段），
        站内信 type 由 inbox_type 显式指定（按失败原因），两者解耦。
        """
        notification_service.notify(
            f"watching_summary_{job_config.name}",
            source="summary",
            skip_cooldown=True,
            in_app_type=inbox_type,
            in_app_title=inbox_title,
            in_app_body=inbox_body or summary_text,
            job_name=job_config.name,
            user_name=job_config.user_name.strip() or "",
            summary_text=summary_text,
            date_range="",
            record_count=0,
            lookback_days=job_config.lookback_days,
            model="",
            tokens_used=0,
        )

    def _format_records(self, records: list[SummaryRecord]) -> str:
        """将同步记录格式化为紧凑的文本表格。"""
        if not records:
            return "（无记录）"
        lines = []
        for r in records:
            ts = str(r.timestamp)[:16]
            user = r.user_name
            title = r.title
            bgm = r.bgm_title
            display_title = f"{title}（{bgm}）" if bgm and bgm != title else title
            if r.media_type == "movie":
                ep_label = "剧场版"
            else:
                ep_label = f"S{r.season}E{r.episode}"
            line = f"[{ts}] {user} | {display_title} | {ep_label} | {r.source} | {r.status}"
            lines.append(line)
        return "\n".join(lines)


# 单例
summary_service = SummaryService()
