"""Summary 生成服务 —— 编排数据库查询、LLM 调用和通知发送。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.database import database_manager
from app.core.logging import logger
from app.utils.notifier import get_notifier

from ..llm import Message, get_llm_client
from .models import SummaryJobConfig

# 内部常量 —— 用户可自定义的 prompt 结构，不暴露到 config.ini
_USER_PROMPT_TEMPLATE = (
    "{date_from} 至 {date_to} 观影记录（共 {record_count} 条）：\n\n{records}"
)


class SummaryService:
    """生成 AI 驱动的追番观影总结。"""

    async def generate_summary(self, job_config: SummaryJobConfig) -> dict:
        """查询数据库，格式化记录，调用 LLM。

        返回字典，包含以下键：summary_text、model、usage、record_count、
        date_from、date_to。
        """
        # 1. 计算日期范围
        now = datetime.now()
        date_from = (now - timedelta(days=job_config.lookback_days)).strftime(
            "%Y-%m-%d"
        )
        date_to = now.strftime("%Y-%m-%d")

        # 2. 查询记录
        records = database_manager.get_records_in_date_range(
            date_from=date_from,
            date_to=date_to,
            limit=job_config.max_records,
            user_name=job_config.user_name.strip() or None,
        )
        record_count = len(records)

        # 3. 格式化记录为文本
        records_text = self._format_records(records)

        # 4. 构建消息
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

        # 5. 调用 LLM
        client = get_llm_client()
        response = await client.chat(
            messages,
            job_name=job_config.name,
        )

        return {
            "summary_text": response.content,
            "model": response.model,
            "usage": response.usage,
            "latency_ms": response.latency,
            "record_count": record_count,
            "date_from": date_from,
            "date_to": date_to,
        }

    async def execute_job(self, job_config: SummaryJobConfig) -> None:
        """完整执行：生成摘要，然后通过通知器发送。"""
        try:
            result = await self.generate_summary(job_config)

            # LLM 失败时用明确提示替换空摘要
            summary_text = result["summary_text"]
            if not summary_text and not result.get("model"):
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

            # 正常发送成功通知
            self._send_success_notification(job_config, result)
        except Exception as e:
            logger.error(f"Summary job '{job_config.name}' failed: {e}")
            summary_text = (
                f"追番总结任务执行异常：{e}\n"
                "请检查任务配置（Cron 表达式、回溯天数等）是否正确。"
            )
            self._send_failure_notification(
                job_config,
                summary_text,
                inbox_type="summary_job_failed",
                inbox_title=f"追番总结异常：{job_config.name}",
                inbox_body="执行异常，请检查任务配置",
            )

    def _send_success_notification(
        self, job_config: SummaryJobConfig, result: dict
    ) -> None:
        """发送成功通知（webhook + 邮件）。"""
        user_name = job_config.user_name.strip() if job_config.user_name else ""
        usage = result["usage"]
        data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "job_name": job_config.name,
            "user_name": user_name,
            "summary_text": result["summary_text"],
            "date_range": f"{result['date_from']} ~ {result['date_to']}",
            "record_count": result["record_count"],
            "lookback_days": job_config.lookback_days,
            "model": result["model"],
            "tokens_used": usage.total_tokens if usage else 0,
        }
        get_notifier().send_notification_by_type(
            f"watching_summary_{job_config.name}", data, skip_cooldown=True
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
        """发送失败通知（webhook + 邮件 + 收件箱）。"""
        data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "job_name": job_config.name,
            "user_name": job_config.user_name.strip() or "",
            "summary_text": summary_text,
            "date_range": "",
            "record_count": 0,
            "lookback_days": job_config.lookback_days,
            "model": "",
            "tokens_used": 0,
        }
        get_notifier().send_notification_by_type(
            f"watching_summary_{job_config.name}", data, skip_cooldown=True
        )
        try:
            database_manager.insert_notification(
                notif_type=inbox_type,
                title=inbox_title,
                body=inbox_body or summary_text,
            )
        except Exception as e:
            logger.error(f"写入收件箱通知失败: {e}")

    def _format_records(self, records: list[dict]) -> str:
        """将同步记录格式化为紧凑的文本表格。"""
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
                ep_label = f"S{r.get('season', 0)}E{r.get('episode', 0)}"
            source = r.get("source", "")
            status = r.get("status", "")
            line = f"[{ts}] {user} | {display_title} | {ep_label} | {source} | {status}"
            lines.append(line)
        return "\n".join(lines)


# 单例
summary_service = SummaryService()
