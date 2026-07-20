"""Summary generation service — orchestrates DB query, LLM call, and notification delivery."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.database import database_manager
from app.core.logging import logger
from app.utils.notifier import get_notifier

from ..llm import Message, get_llm_client
from .models import SummaryJobConfig

# Internal constant — user-customizable prompt structure, not exposed to config.ini
_USER_PROMPT_TEMPLATE = (
    "{date_from} 至 {date_to} 观影记录（共 {record_count} 条）：\n\n{records}"
)


class SummaryService:
    """Generates AI-powered summaries of watching records."""

    async def generate_summary(self, job_config: SummaryJobConfig) -> dict:
        """Query DB, format records, call LLM.

        Returns dict with keys: summary_text, model, usage, record_count,
        date_from, date_to.
        """
        # 1. Calculate date range
        now = datetime.now()
        date_from = (now - timedelta(days=job_config.lookback_days)).strftime(
            "%Y-%m-%d"
        )
        date_to = now.strftime("%Y-%m-%d")

        # 2. Query records
        records = database_manager.get_records_in_date_range(
            date_from=date_from,
            date_to=date_to,
            limit=job_config.max_records,
            user_name=job_config.user_name.strip() or None,
        )
        record_count = len(records)

        # 3. Format records into text
        records_text = self._format_records(records)

        # 4. Build messages
        system_prompt = job_config.system_prompt.strip()
        if not system_prompt:
            system_prompt = SummaryJobConfig.system_prompt

        user_content = _USER_PROMPT_TEMPLATE.format(
            date_from=date_from,
            date_to=date_to,
            records=records_text,
            record_count=record_count,
        )
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_content),
        ]

        # 5. Call LLM
        client = get_llm_client()
        response = await client.chat(
            messages,
            job_id=job_config.id,
            job_name=job_config.name,
        )

        return {
            "summary_text": response.content,
            "model": response.model,
            "usage": response.usage,
            "record_count": record_count,
            "date_from": date_from,
            "date_to": date_to,
        }

    async def execute_job(self, job_config: SummaryJobConfig) -> None:
        """Full execution: generate summary, then send via Notifier."""
        try:
            result = await self.generate_summary(job_config)

            # Build notification type
            user_name = job_config.user_name.strip() if job_config.user_name else ""
            notif_type = (
                f"watching_summary_{user_name}" if user_name else "watching_summary"
            )

            # Build data dict
            usage = result["usage"]
            data = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "job_name": job_config.name,
                "job_id": job_config.id,
                "user_name": user_name,
                "summary_text": result["summary_text"],
                "date_range": f"{result['date_from']} ~ {result['date_to']}",
                "record_count": result["record_count"],
                "lookback_days": job_config.lookback_days,
                "model": result["model"],
                "tokens_used": usage.total_tokens if usage else 0,
            }
            # TODO 需要考虑 88 行的 notif_type 是否能匹配新增前端新增的类型，联动前端保存数据同步看
            get_notifier().send_notification_by_type(notif_type, data)
        except Exception as e:
            logger.error(
                f"Summary job '{job_config.name}' (id={job_config.id}) failed: {e}"
            )

    def _format_records(self, records: list[dict]) -> str:
        """Format sync records into a compact text table."""
        if not records:
            return "（无记录）"
        lines = []
        for r in records:
            ts = str(r.get("timestamp", ""))[:16]
            user = r.get("user_name", "")
            title = r.get("title", "")
            bgm = r.get("bgm_title", "")
            display_title = f"{title}（{bgm}）" if bgm and bgm != title else title
            media = r.get("media_type", "episode")
            if media == "movie":
                ep_label = "剧场版"
            else:
                # TODO 需要确定这里是否符合数据入库规则
                ep_label = f"S{r.get('season', 0)}E{r.get('episode', 0)}"
            source = r.get("source", "")
            status = r.get("status", "")
            line = f"[{ts}] {user} | {display_title} | {ep_label} | {source} | {status}"
            lines.append(line)
        return "\n".join(lines)


# Singleton
summary_service = SummaryService()
