"""
同步服务模块
"""

from __future__ import annotations

# time/asyncio 重新导出以兼容测试 patch（app.services.sync_service.time.sleep 等）
import asyncio  # noqa: F401
import json
import threading
import time  # noqa: F401
import traceback
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ...core.config import config_manager
from ...core.database import database_manager
from ...core.logging import (
    batch_log_context,
    get_sync_run_id as get_sync_run_id,
    logger,
    new_batch_id,
    new_inline_sync_run_id,
    new_retry_sync_run_id,
    sync_log_context,
)
from ...models.sync import CustomItem, SyncResponse
from ...utils.bangumi_api import BangumiApi
from ...utils.bangumi_api.collection import _PendingSyncQueued, is_replay_enabled
from ...utils.bangumi_constants import (
    COLLECTION_TYPE_DONE,
    RELATION_ID_PARENT_STORY as RELATION_ID_PARENT_STORY,
    RELATIONS as RELATIONS,
    SUBJECT_TYPE_ANIME as SUBJECT_TYPE_ANIME,
    SUBJECT_TYPE_REAL as SUBJECT_TYPE_REAL,
)
from ...utils.bangumi_data import BangumiData, bangumi_data
from ...utils.media_type_detector import detect_media_type as detect_media_type
from ..mapping_service import mapping_service
from ..notification_service import notification_service
from .match_trace import MatchCandidate as MatchCandidate, MatchTrace
from .retry import MARK_QUEUED, RetryMixin
from .season_info import SeasonInfoMixin
from .task_manager import TaskManagerMixin
from .title_normalize import TitleNormalizeMixin


def _build_error_detail(exc: Exception) -> dict[str, Any]:
    """构建 MatchStep.error_detail 字典（type/message/traceback）"""
    import traceback as _tb

    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": _tb.format_exc(),
    }


def _extract_infobox_aliases(cand: dict) -> list[str]:
    """从候选条目的 infobox 中提取别名列表（兼容多种历史数据格式）

    用于 P2 infobox_aliases 字段，帮助理解 title_diff_ratio 为何给出该分数。
    """
    aliases: list[str] = []
    infobox = cand.get("infobox")
    if not isinstance(infobox, list):
        return aliases
    for info in infobox:
        if not isinstance(info, dict) or info.get("key") != "别名":
            continue
        value = info.get("value")
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and "v" in item:
                    aliases.append(str(item["v"]))
                elif isinstance(item, str):
                    aliases.append(item)
        elif isinstance(value, str):
            aliases.append(value)
        break
    return aliases


def _detect_candidate_media_type(cand: dict) -> str:
    """检测候选条目的媒体类型（用于 P0 media_type 字段）"""
    try:
        return detect_media_type(
            title=cand.get("name_cn", ""),
            ori_title=cand.get("name", ""),
        )
    except Exception:
        return ""


class SyncService(TaskManagerMixin, RetryMixin, SeasonInfoMixin, TitleNormalizeMixin):
    """同步服务"""

    def __init__(self):
        self._bangumi_data_cache: BangumiData | None = None
        self._cached_mappings: dict[str, str] = {}
        self._mapping_file_path: str | None = None
        self._last_modified_time: float = 0
        # BangumiApi 实例缓存：user_name → (实例, 配置快照)
        # 配置快照变更时自动失效重建，避免每次同步都重新构造 httpx.Client
        self._bangumi_api_cache: dict[str, tuple[BangumiApi, dict]] = {}
        # 线程池大小从配置读取
        try:
            scheduler_cfg = config_manager.get_scheduler_config()
            max_workers = max(1, int(scheduler_cfg.get("max_concurrent_syncs", 3)))
        except (TypeError, ValueError, KeyError):
            max_workers = 3
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="sync_worker"
        )
        # 同步任务状态跟踪
        self._tasks_lock = threading.Lock()
        self._sync_tasks = {}
        self._task_counter = 0
        # 阶段四：同步编排器，接管 sync_custom_item 完整流程
        # （请求处理 → 匹配 → 集数解析 → 标记 → 持久化）
        from .orchestrator import SyncOrchestrator

        self._orchestrator = SyncOrchestrator(self)

    def shutdown(self) -> None:
        """关闭线程池，等待正在执行的任务完成"""
        self._executor.shutdown(wait=True)

    # ------------------------------------------------------------------
    # 同步记录查询（API 层通过本服务访问数据库，避免跨层直访）
    # ------------------------------------------------------------------

    def get_sync_records(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        user_name: str | None = None,
        source: str | None = None,
        source_prefix: str | None = None,
        match_method: str | None = None,
        match_platform: str | None = None,
        skip_count: bool = False,
    ) -> dict[str, Any]:
        """获取同步记录列表"""
        return database_manager.get_sync_records(
            limit=limit,
            offset=offset,
            status=status,
            user_name=user_name,
            source=source,
            source_prefix=source_prefix,
            match_method=match_method,
            match_platform=match_platform,
            skip_count=skip_count,
        )

    def get_sync_record_by_id(self, record_id: int) -> dict[str, Any] | None:
        """根据 ID 获取单个同步记录"""
        return database_manager.get_sync_record_by_id(record_id)

    def get_match_records(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        match_method: str | None = None,
        match_platform: str | None = None,
    ) -> dict[str, Any]:
        """获取匹配记录列表（含匹配追踪字段）"""
        return database_manager.get_match_records(
            limit=limit,
            offset=offset,
            status=status,
            match_method=match_method,
            match_platform=match_platform,
        )

    # ------------------------------------------------------------------
    # 待确认候选（候选沉淀）
    # ------------------------------------------------------------------

    def get_pending_candidates(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        """获取待确认候选列表"""
        return database_manager.get_pending_candidates(
            limit=limit, offset=offset, status=status
        )

    def get_pending_candidate_by_id(self, candidate_id: int) -> dict[str, Any] | None:
        """获取单条待确认候选详情"""
        return database_manager.get_pending_candidate_by_id(candidate_id)

    def get_pending_candidate_by_sync_record_id(
        self, sync_record_id: int
    ) -> dict[str, Any] | None:
        """按 sync_record_id 查询关联的候选记录"""
        return database_manager.get_pending_candidate_by_sync_record_id(sync_record_id)

    def confirm_pending_candidate(
        self, candidate_id: int, subject_id: str
    ) -> tuple[bool, str]:
        """确认待确认候选：写入自定义映射并标记为已确认

        返回 (success, message)。映射写入委托
        :meth:`MappingService.upsert_single_mapping`，由其内部完成
        读全量→合并→写回，避免覆盖已有映射。

        subject_id 有效性校验：优先走 archive 短路（无网络开销），
        archive 未命中时降级到 Bangumi API。校验失败（条目不存在或类型
        不是动画/三次元）时拒绝确认并返回原因，避免写入无效映射。
        """
        record = database_manager.get_pending_candidate_by_id(candidate_id)
        if not record:
            return False, "候选记录不存在"
        if record.get("status") != "pending":
            return False, f"候选已处理（状态：{record.get('status')}）"

        title = record.get("request_title", "")
        season = int(record.get("request_season") or 1)
        if not title or not subject_id:
            return False, "标题或 subject_id 为空"

        # subject_id 有效性校验：必须存在且类型为动画/三次元
        ok, reason = self._validate_subject_id(subject_id)
        if not ok:
            return False, f"subject_id 校验失败：{reason}"

        # 写入单条映射（读全量→合并→写回由 mapping_service 封装）
        if not mapping_service.upsert_single_mapping(title, subject_id, season):
            return False, "写入自定义映射失败"

        database_manager.update_pending_candidate_status(
            candidate_id, "confirmed", confirmed_subject_id=str(subject_id)
        )
        # 批量更新同 key 的其它 pending 行，避免残留（去重后通常无额外行）
        database_manager.resolve_similar_pending_candidates(
            request_title=title,
            request_season=season,
            user_name=record.get("user_name", ""),
            source=record.get("source", ""),
            status="confirmed",
            confirmed_subject_id=str(subject_id),
            exclude_id=candidate_id,
        )

        # 候选确认即补发：若有关联的 sync_record_id，自动触发重试
        replay_msg = self._auto_replay_after_confirm(
            record.get("sync_record_id"), record, title
        )
        final_msg = f"已确认并写入映射：{title} → subject/{subject_id}"
        if replay_msg:
            final_msg = f"{final_msg}；{replay_msg}"
        return True, final_msg

    def _auto_replay_after_confirm(
        self,
        sync_record_id: int | None,
        candidate_record: dict[str, Any],
        title: str,
    ) -> str:
        """候选确认后自动补发原同步记录。

        仅当 pending_candidates.sync_record_id 存在且对应 sync_records 状态非 success
        时触发。补发复用 sync_custom_item 流程，结果回写 sync_records：
        - success/ignored → 状态改写为 retried，清理 pending_sync_queue
        - error → 保持原状态，返回错误原因供前端展示

        补发失败不影响 confirm 主流程（映射已写入，下次同步会自动命中）。
        返回补发结果描述字符串；未触发补发时返回空串。
        """
        if not sync_record_id:
            return ""

        try:
            record = database_manager.get_sync_record_by_id(int(sync_record_id))
        except Exception as e:
            logger.warning(
                f"候选确认后补发：查询 sync_record_id={sync_record_id} 失败: {e}"
            )
            return ""

        if not record:
            return ""

        if record.get("status") == "success":
            return ""

        # 构建重试 item（优先用 sync_record 的 match_trace 还原原始请求字段）
        try:
            retry_item = self._build_retry_item_from_record(
                record, f"retry-{record.get('source', 'custom')}"
            )
        except Exception as e:
            logger.warning(
                f"候选确认后补发：构建重试 item 失败 (record={sync_record_id}): {e}"
            )
            return ""

        logger.info(
            f"候选确认触发补发: record_id={sync_record_id}, title={title}, "
            f"原状态={record.get('status')}"
        )
        try:
            with sync_log_context(new_retry_sync_run_id(int(sync_record_id))):
                result = self.sync_custom_item(
                    retry_item,
                    source=f"retry-{record.get('source', 'custom')}",
                )
        except Exception as e:
            logger.warning(f"候选确认后补发异常 (record={sync_record_id}): {e}")
            return f"补发异常: {e}"

        # 回写原 sync_records 状态
        original_status = record.get("status", "")
        if result.status == "success":
            database_manager.update_sync_record_status(
                record_id=int(sync_record_id),
                status="retried",
                message=f"候选确认后补发成功: {result.message}",
            )
            self._cleanup_pending_for_replay(int(sync_record_id), original_status)
            return "已自动补发成功"
        if result.status == "ignored":
            database_manager.update_sync_record_status(
                record_id=int(sync_record_id),
                status="retried",
                message=f"候选确认后补发被忽略: {result.message}",
            )
            self._cleanup_pending_for_replay(int(sync_record_id), original_status)
            return f"补发被忽略: {result.message}"
        # error
        return f"补发失败: {result.message}"

    def _cleanup_pending_for_replay(self, record_id: int, original_status: str) -> None:
        """补发成功后清理 pending_sync_queue 中对应的 pending 行。

        原 queued 记录背后通常有一条 pending_sync_queue 行等待补发，
        补发成功后必须清理，否则调度器仍会捞起该行重放，导致重复标记。
        非 queued 状态原记录无关联 pending 行，直接跳过。
        """
        if original_status != "queued":
            return
        try:
            affected = database_manager.mark_pending_sync_synced_by_sync_record_id(
                record_id
            )
            if affected > 0:
                logger.info(
                    f"补发后清理 pending_sync_queue (record={record_id}): "
                    f"删除 {affected} 条 pending 行"
                )
        except Exception as e:
            logger.warning(f"清理 pending_sync_queue (record={record_id}) 失败: {e}")

    def _build_retry_item_from_record(
        self, record: dict[str, Any], retry_source: str
    ) -> CustomItem:
        """从 sync_record 构建 retry 用的 CustomItem。

        优先从 match_trace 还原原始请求字段；trace 缺失时回退到 sync_records 列。
        与 app/api/sync.py._build_retry_item 行为一致，但下沉到 sync_service
        供候选确认即补发流程复用。
        """
        from ...models.sync import CustomItem as _CustomItem

        # 优先 match_trace，回退到 sync_records 字段
        trace_raw = record.get("match_trace")
        trace: dict[str, Any] | None = None
        if isinstance(trace_raw, dict):
            trace = trace_raw
        elif isinstance(trace_raw, str) and trace_raw:
            try:
                parsed = json.loads(trace_raw)
                if isinstance(parsed, dict):
                    trace = parsed
            except json.JSONDecodeError:
                trace = None

        # receive 步骤中的 processed_payload 含原始请求字段
        receive_step: dict[str, Any] | None = None
        if trace:
            for step in trace.get("steps") or []:
                if isinstance(step, dict) and step.get("stage") == "receive":
                    receive_step = step
                    break

        def _pick(trace_key: str, payload_key: str, fallback: Any) -> Any:
            if trace:
                v = trace.get(trace_key)
                if v not in (None, ""):
                    return v
            if receive_step:
                payload = receive_step.get("processed_payload") or {}
                if isinstance(payload, dict):
                    v = payload.get(payload_key)
                    if v not in (None, ""):
                        return v
            return fallback

        title = _pick("request_title", "title", record.get("title", ""))
        ori_title = _pick("request_ori_title", "ori_title", record.get("ori_title"))
        season = _pick("request_season", "season", record.get("season", 1))
        episode = _pick("request_episode", "episode", record.get("episode", 1))
        media_type = _pick(
            "request_media_type", "media_type", record.get("media_type") or "episode"
        )
        release_date = _pick("request_release_date", "release_date", "")
        user_name = _pick("request_user_name", "user_name", record.get("user_name", ""))
        sync_action = _pick("request_sync_action", "sync_action", None)
        raw_payload = receive_step.get("raw_payload") if receive_step else None

        retry_media = (media_type or "episode").lower()
        if retry_media not in ("episode", "movie", "ova", "oad", "real_action"):
            retry_media = "episode"

        return _CustomItem(
            media_type=retry_media,
            title=title or "",
            ori_title=ori_title or None,
            season=int(season) if season is not None else 1,
            episode=int(episode) if episode is not None else 1,
            release_date=release_date or "",
            user_name=user_name or "",
            source=retry_source,
            sync_action=sync_action or None,
            raw_payload=raw_payload,
        )

    def _validate_subject_id(self, subject_id: str) -> tuple[bool, str]:
        """校验 subject_id 是否有效：存在且类型为动画/三次元

        优先走 archive 短路（本地查询，<1ms），未命中降级到 Bangumi API。
        返回 (True, "") 或 (False, reason)。

        校验失败场景：
        - subject_id 非数字
        - 条目不存在（API 404 或返回空）
        - 类型非动画/三次元（如书籍/音乐/游戏）
        - archive 与 API 均不可用（网络异常时降级放行，避免阻塞用户）
        """
        try:
            sid = int(subject_id)
        except (TypeError, ValueError):
            return False, "ID 必须为纯数字"

        # 优先走 archive 短路（无网络开销）
        from ...utils.bangumi_api._archive_shortcut import archive_shortcut

        if archive_shortcut.enabled:
            result = archive_shortcut.try_get_subject(sid)
            if result.hit and isinstance(result.data, dict):
                stype = result.data.get("type")
                if stype in (SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL):
                    return True, ""
                return False, f"条目类型为 {stype}，仅支持动画/三次元"
            # archive 未命中或不完整：降级到 API

        # 降级到 API：DB 为唯一真相源，取激活账号；无激活则取首个可用账号
        from app.core import accounts as _accounts

        cfg = _accounts.get_active_bangumi_config() or None
        if cfg is None:
            configs = _accounts.list_bangumi_configs()
            if not configs:
                # 无可用配置时降级放行（不阻塞用户，映射写入后再由同步流程校验）
                logger.warning(
                    f"subject_id={sid} 校验跳过：无可用 Bangumi 配置，降级放行"
                )
                return True, ""
            cfg = next(iter(configs.values()))

        dev_snapshot = config_manager.get_dev_http_snapshot()
        api = BangumiApi(
            username=cfg["username"],
            access_token=cfg["access_token"],
            private=cfg.get("private", False),
            http_proxy=dev_snapshot["script_proxy"],
            ssl_verify=dev_snapshot["ssl_verify"],
            bgm_api_proxy=dev_snapshot["bgm_api_proxy"],
            bgm_next_proxy=dev_snapshot["bgm_next_proxy"],
            ech_mode=dev_snapshot["ech_mode"],
        )
        try:
            data = api.get_subject(sid)
        except Exception as e:
            logger.warning(f"subject_id={sid} API 校验异常：{e}，降级放行")
            return True, ""

        if not isinstance(data, dict) or not data:
            return False, "条目不存在或 API 返回空"

        stype = data.get("type")
        if stype not in (SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL):
            return False, f"条目类型为 {stype}，仅支持动画/三次元"

        return True, ""

    def reject_pending_candidate(self, candidate_id: int) -> tuple[bool, str]:
        """拒绝待确认候选"""
        if not database_manager.update_pending_candidate_status(
            candidate_id, "rejected"
        ):
            return False, "候选记录不存在或已处理"
        return True, "已忽略"

    def delete_pending_candidate(self, candidate_id: int) -> tuple[bool, str]:
        """删除待确认候选"""
        if not database_manager.delete_pending_candidate(candidate_id):
            return False, "候选记录不存在"
        return True, "已删除"

    @staticmethod
    def _collect_candidates_from_trace(trace: MatchTrace) -> list[dict[str, Any]]:
        """从 MatchTrace 各步骤中收集候选，去重并按 score 降序"""
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for step in trace.steps:
            for cand in step.candidates:
                if not cand.subject_id or cand.subject_id in seen:
                    continue
                seen.add(cand.subject_id)
                merged.append(cand.to_dict())
        merged.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return merged

    def _sediment_pending_candidate(
        self,
        item: CustomItem,
        actual_source: str,
        trace: MatchTrace,
        sync_record_id: int | None = None,
    ) -> None:
        """匹配失败时沉淀候选，供用户手动确认。

        仅当 trace 中存在候选时才写入 pending_candidates 表，
        并触发 pending_candidate 通知提醒用户前往 WebUI 确认。

        sync_record_id：关联的 sync_records 行 id，用于候选确认后回写原记录状态。
        """
        candidates = self._collect_candidates_from_trace(trace)
        if not candidates:
            return
        try:
            database_manager.log_pending_candidate(
                request_title=item.title,
                request_ori_title=item.ori_title or "",
                request_season=item.season,
                request_episode=item.episode,
                user_name=item.user_name,
                source=actual_source,
                candidates=candidates,
                trace=trace.to_dict(),
                sync_record_id=sync_record_id,
            )
        except Exception as e:
            logger.warning(f"沉淀待确认候选失败（不影响主流程）: {e}")
            return

        # 沉淀成功后触发通知（不影响主流程）
        try:
            top = candidates[0] if candidates else {}
            notification_service.notify(
                "pending_candidate",
                item,
                actual_source,
                candidates_count=len(candidates),
                top_candidate_id=str(top.get("subject_id", "")),
                top_candidate_name=top.get("name_cn") or top.get("name") or "",
            )
        except Exception as e:
            logger.warning(f"发送候选待确认通知失败（不影响主流程）: {e}")

    def _maybe_notify_match_ambiguous(
        self,
        trace: MatchTrace,
        item: CustomItem,
        actual_source: str,
    ) -> None:
        """匹配歧义时触发 match_ambiguous 通知

        歧义检测已前移到 APISearchStep（设置 ctx.is_ambiguous → trace.is_ambiguous），
        本方法仅负责通知发送：当 trace.is_ambiguous 为 True 时，从 trace 收集
        top1/top2 候选信息并发送通知。

        Args:
            trace: 已 finish 的 MatchTrace（需含 is_ambiguous 标记）
            item: 当前同步条目
            actual_source: 实际来源
        """
        try:
            if not trace or not trace.is_ambiguous:
                return
            candidates = self._collect_candidates_from_trace(trace)
            if len(candidates) < 2:
                return
            top1_score = float(candidates[0].get("score", 0.0))
            top2_score = float(candidates[1].get("score", 0.0))

            from ..notification_service import notification_service

            notification_service.notify(
                "match_ambiguous",
                item=item,
                source=actual_source,
                final_subject_id=trace.final_subject_id,
                top1_score=top1_score,
                top2_score=top2_score,
                top1_name=candidates[0].get("name_cn") or candidates[0].get("name", ""),
                top2_name=candidates[1].get("name_cn") or candidates[1].get("name", ""),
                top1_subject_id=candidates[0].get("subject_id"),
                top2_subject_id=candidates[1].get("subject_id"),
                score_diff=round(top1_score - top2_score, 4),
            )
        except Exception as e:
            logger.debug(f"发送 match_ambiguous 通知失败（可忽略）: {e}")

    def test_match(self, item: CustomItem) -> dict[str, Any]:
        """测试匹配过程，返回匹配追踪详情（不执行实际同步、不发通知、不写库）

        用于「匹配记录」页面的匹配测试面板，直观展示三段式匹配的完整过程。
        不写入 sync_records 表，避免污染 dashboard 统计与同步记录列表。
        """
        trace = MatchTrace(
            request_title=item.title,
            request_ori_title=item.ori_title or "",
            request_season=item.season,
            request_episode=item.episode,
            request_media_type=item.media_type,
            request_release_date=item.release_date or "",
            request_user_name=item.user_name,
            request_platform_hint=item.source or "test-match",
        )
        subject_id, is_season_matched_id, failure_detail = self._find_subject_id(
            item, trace=trace
        )
        trace.finish()

        return {
            "subject_id": subject_id,
            "is_season_matched_id": is_season_matched_id,
            "failure_detail": failure_detail,
            "trace": trace.to_dict(),
        }

    def update_sync_record_status(
        self, record_id: int, status: str, message: str = ""
    ) -> bool:
        """更新同步记录的状态"""
        return database_manager.update_sync_record_status(record_id, status, message)

    def update_sync_record_match_fields(
        self,
        record_id: int,
        match_method: str | None = None,
        match_trace: dict | None = None,
        match_score: float | None = None,
        match_platform: str | None = None,
    ) -> bool:
        """回写同步记录的匹配字段，用于重试成功后覆盖原始失败记录的 match_method 等。"""
        return database_manager.update_sync_record_match_fields(
            record_id,
            match_method=match_method,
            match_trace=match_trace,
            match_score=match_score,
            match_platform=match_platform,
        )

    def mark_pending_sync_synced_by_sync_record_id(self, sync_record_id: int) -> int:
        """按 sync_record_id 清理 pending_sync_queue 中的 pending 行。

        用于手动重试 queued 同步记录成功后，避免补发调度器重复捞起导致重复标记。
        """
        return database_manager.mark_pending_sync_synced_by_sync_record_id(
            sync_record_id
        )

    def get_sync_stats(self) -> dict[str, Any]:
        """获取同步统计信息"""
        return database_manager.get_sync_stats()

    def get_heatmap_stats(self) -> list[dict[str, Any]]:
        """获取热力图数据（过去365天每天同步数）"""
        return database_manager.get_heatmap_stats()

    def sync_movie_watching(
        self, item: CustomItem, source: str = "custom"
    ) -> SyncResponse:
        """剧场版：仅将 Bangumi 条目收藏标为「在看」，不解析章节、不点单集。"""
        try:
            actual_source = item.source if item.source else source
            logger.info(f"接收到剧场版在看请求：{item}")
            notification_service.notify("request_received", item, actual_source)

            if not config_manager.get(
                "sync", "movie_playback_start_mark_watching", fallback=True
            ):
                return SyncResponse(
                    status="ignored",
                    message="已在配置中关闭剧场版播放开始标记在看",
                )

            if item.media_type not in ("movie", "real_action"):
                return SyncResponse(
                    status="ignored", message="仅剧场版/真人电影支持播放开始标记在看"
                )

            if not item.title:
                logger.error("同步名称为空，跳过")
                return SyncResponse(status="error", message="同步名称为空")

            if not (
                perm_ok := self._check_user_permission(item.user_name, actual_source)
            )[0]:
                return SyncResponse(status="error", message=perm_ok[1])

            if self._is_title_blocked(item.title, item.ori_title):
                return SyncResponse(
                    status="ignored", message="番剧标题包含屏蔽关键词，跳过同步"
                )

            subject_id, _, error_response, trace = self._find_matching_subject(
                item, actual_source
            )
            if not subject_id:
                return error_response or SyncResponse(
                    status="error", message="未找到匹配的番剧"
                )

            bgm = self._get_bangumi_api_for_user(item.user_name)
            if not bgm:
                logger.error(f"无法为用户 {item.user_name} 创建bangumi API实例")
                return SyncResponse(status="error", message="bangumi配置错误")

            notification_service.notify(
                "bangumi_id_found", item, actual_source, subject_id=str(subject_id)
            )

            try:
                mark_st = bgm.ensure_subject_watching(str(subject_id))
            except _PendingSyncQueued as e:
                # API 不可达：与剧集降级路径对称，入待同步队列而非报错
                if not is_replay_enabled():
                    # 未启用补发：让异常向上抛，由外层捕获为 error
                    raise
                self._enqueue_pending_sync(
                    bgm_api=bgm,
                    subject_id=e.subject_id,
                    ep_id=e.ep_id,
                    reason=e.reason,
                    last_error=str(e.cause) if e.cause else e.reason,
                    payload=item.model_dump(),
                )
                queued_message = (
                    f"📚 Bangumi API 不可达，已入待同步队列："
                    f"{item.title} → subject/{subject_id}"
                )
                logger.warning(queued_message)
                queued_record_id = database_manager.log_sync_record(
                    user_name=item.user_name,
                    title=item.title,
                    ori_title=item.ori_title or "",
                    season=item.season,
                    episode=item.episode,
                    subject_id=str(subject_id),
                    episode_id=None,
                    status="queued",
                    message=queued_message,
                    source=actual_source,
                    media_type=item.media_type,
                    match_method=trace.final_match_method if trace else "",
                    match_score=trace.final_score if trace else None,
                    match_platform=self._extract_matched_platform(trace, subject_id)
                    if trace
                    else "",
                    match_trace=trace.to_dict() if trace else None,
                )
                # 回填 sync_record_id 到刚入队的 pending_sync_queue 行
                if queued_record_id:
                    try:
                        database_manager.link_pending_sync_to_record(
                            user_name=item.user_name,
                            subject_id=str(subject_id),
                            episode_id=None,
                            source=actual_source,
                            sync_record_id=queued_record_id,
                        )
                    except Exception as link_err:
                        logger.warning(
                            f"📚 回填 sync_record_id 失败（不影响入队）: {link_err}"
                        )
                try:
                    notification_service.notify(
                        "sync_queued",
                        item,
                        actual_source,
                        subject_id=str(subject_id),
                        episode_id="",
                    )
                except Exception:
                    pass
                return SyncResponse(
                    status="queued",
                    message=queued_message,
                    data={
                        "title": item.title,
                        "season": item.season,
                        "episode": item.episode,
                        "subject_id": str(subject_id),
                    },
                )
            except ValueError as ve:
                if "认证失败" in str(ve) or "access_token" in str(ve):
                    return SyncResponse(status="error", message=str(ve))
                raise ve

            if mark_st == 0:
                result_message = "条目已在看或已看过，无需变更"
            else:
                result_message = "播放开始：条目标记为在看"

            logger.debug(
                f"bgm: {item.title} {result_message} https://bgm.tv/subject/{subject_id}"
            )

            database_manager.log_sync_record(
                user_name=item.user_name,
                title=item.title,
                ori_title=item.ori_title or "",
                season=item.season,
                episode=item.episode,
                subject_id=str(subject_id),
                episode_id=None,
                status="success",
                message=result_message,
                source=actual_source,
                media_type=item.media_type,
                match_method=trace.final_match_method if trace else "",
                match_score=trace.final_score if trace else None,
                match_platform=self._extract_matched_platform(trace, subject_id)
                if trace
                else "",
                match_trace=trace.to_dict() if trace else None,
            )

            return SyncResponse(
                status="success",
                message=result_message,
                data={
                    "title": item.title,
                    "season": item.season,
                    "episode": item.episode,
                    "subject_id": str(subject_id),
                },
            )
        except Exception as e:
            logger.error(f"剧场版在看处理出错: {e}")
            record_id = database_manager.log_sync_record(
                user_name=item.user_name if "item" in locals() else "unknown",
                title=item.title if "item" in locals() else "unknown",
                ori_title=item.ori_title if "item" in locals() else "",
                season=item.season if "item" in locals() else 0,
                episode=item.episode if "item" in locals() else 0,
                status="error",
                message=str(e),
                source=actual_source if "actual_source" in locals() else source,
                media_type=item.media_type if "item" in locals() else "movie",
                match_method=trace.final_match_method if trace else "",
                match_score=trace.final_score if trace else None,
                match_platform=self._extract_matched_platform(trace, None)
                if trace
                else "",
                match_trace=trace.to_dict() if trace else None,
            )
            notification_service.notify(
                "mark_failed",
                item if "item" in locals() else None,
                actual_source if "actual_source" in locals() else source,
                in_app_ref_id=record_id,
                error_message=str(e),
                error_type="sync_error",
                additional_info=f"完整错误信息: {traceback.format_exc()}",
            )
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")

    def _normalize_custom_item_params(
        self, item: CustomItem, actual_source: str = ""
    ) -> SyncResponse | None:
        """校验自定义条目参数。返回 SyncResponse 表示应立即返回该响应；None 表示校验通过。"""
        # 基本验证
        # 支持的媒体类型：episode/movie（原有）+ ova/oad/real_action（扩展）
        if item.media_type not in ("episode", "movie", "ova", "oad", "real_action"):
            logger.error(f"同步类型{item.media_type}不支持，跳过")
            return SyncResponse(
                status="error", message=f"同步类型{item.media_type}不支持"
            )

        if not item.title:
            logger.error("同步名称为空，跳过")
            return SyncResponse(status="error", message="同步名称为空")

        # episode/ova/oad/real_action 走剧集同步路径，不允许 season=0
        if (
            item.media_type in ("episode", "ova", "oad", "real_action")
            and item.season == 0
        ):
            logger.error("不支持SP标记同步，跳过")
            return SyncResponse(status="error", message="不支持SP标记同步")

        if item.episode == 0:
            logger.error(f"集数{item.episode}不能为0，跳过")
            return SyncResponse(status="error", message=f"集数{item.episode}不能为0")

        # 检查用户权限
        if not (perm_ok := self._check_user_permission(item.user_name, actual_source))[
            0
        ]:
            return SyncResponse(status="error", message=perm_ok[1])

        # 检查是否包含屏蔽关键词
        if self._is_title_blocked(item.title, item.ori_title):
            return SyncResponse(
                status="ignored", message="番剧标题包含屏蔽关键词，跳过同步"
            )

        return None

    # 阶段四：_find_matching_subject 已迁入 SyncOrchestrator._match_subject
    # （请求处理 → 匹配 → 集数解析 → 标记 → 持久化 统一编排）
    # 保留委托方法供 sync_movie_watching 和测试调用
    def _find_matching_subject(
        self, item: CustomItem, actual_source: str
    ) -> tuple[str | None, bool, SyncResponse | None, MatchTrace]:
        """委托给编排器的匹配阶段（阶段四）"""
        return self._orchestrator._match_subject(item, actual_source)

    @staticmethod
    def _extract_matched_platform(trace: MatchTrace, subject_id: str | None) -> str:
        """从匹配追踪的命中候选中提取 Bangumi 条目 platform（TV/OVA/剧场版/日剧等）。

        优先查找与 subject_id 匹配的候选；找不到则取最后命中阶段的第一个候选。
        """
        if not subject_id or not trace:
            return ""
        target_id = str(subject_id)
        # 优先在命中阶段的候选中查找与 subject_id 匹配的条目
        for step in trace.steps:
            if step.status != "hit" or not step.subject_id:
                continue
            for cand in step.candidates:
                if cand.subject_id == target_id:
                    return cand.platform
            # 命中阶段但候选中没有完全匹配的 ID，取该阶段首个候选的 platform
            if step.candidates:
                return step.candidates[0].platform
        return ""

    @staticmethod
    def _pick_mainline_episode_candidate(
        candidates: list[dict], request_title: str
    ) -> dict:
        """在多个 episode 类型的候选中，按"主线剧集优先级"择优。

        场景："完美世界"搜索结果有 542046 剧场版（detect=movie 排除）、
        175141 双食记（6 集衍生短番，detect=episode 误命中）、
        403251 第三季 / 345811 第二季 / 449355 第四季（主线剧集）。
        不能取第一个 episode 候选（会误选双食记），需要择优。

        优先级（从高到低）：
        1. 标题与请求标题精确相等（最直接的主线条目）
        2. 标题含"第N季"声明（明确是季番条目）
        3. eps/total_episodes 最大的候选（主线剧集集数多）
        4. 兜底取第一个候选

        Args:
            candidates: detect_media_type 为 episode 的候选列表（已排除剧场版/电影）
            request_title: 请求侧标题（用于精确匹配判断）

        Returns:
            最佳候选
        """
        if not candidates:
            return {}  # type: ignore[return-value]
        if len(candidates) == 1:
            return candidates[0]

        request_title = (request_title or "").strip()

        # 1) 标题精确等于请求标题
        if request_title:
            for cand in candidates:
                name = (cand.get("name") or "").strip()
                name_cn = (cand.get("name_cn") or "").strip()
                if name == request_title or name_cn == request_title:
                    return cand

        # 2) 标题含"第N季"声明（明确是季番条目，优先于衍生短番）
        season_candidates = []
        for cand in candidates:
            name = cand.get("name") or ""
            name_cn = cand.get("name_cn") or ""
            combined = f"{name} {name_cn}"
            if "季" in combined or "期" in combined or "Season" in combined:
                season_candidates.append(cand)
        if season_candidates:
            # 在季番候选中再按 eps 排序
            season_candidates.sort(
                key=lambda c: int(c.get("eps") or c.get("total_episodes") or 0),
                reverse=True,
            )
            return season_candidates[0]

        # 3) eps/total_episodes 最大的候选
        sorted_by_eps = sorted(
            candidates,
            key=lambda c: int(c.get("eps") or c.get("total_episodes") or 0),
            reverse=True,
        )
        return sorted_by_eps[0]

    def _resolve_season_episode(
        self,
        bgm: BangumiApi,
        item: CustomItem,
        subject_id: str,
        is_season_matched_id: bool,
    ) -> tuple[str, str]:
        """根据 media_type 解析 Bangumi 季度与集数 ID。

        返回 (bgm_se_id, bgm_ep_id)；可能抛出 ValueError（认证错误由调用方处理）。
        """
        release_for_ep = None
        if item.release_date and len(item.release_date) >= 8:
            release_for_ep = item.release_date[:10]
        # 电影走短路径，剧集走季番解析
        if item.media_type == "movie":
            return bgm.get_movie_main_episode_id(subject_id, target_sort=item.episode)
        return bgm.get_target_season_episode_id(
            subject_id=subject_id,
            target_season=item.season,
            target_ep=item.episode,
            is_season_subject_id=is_season_matched_id,
            release_date=release_for_ep,
        )

    def _apply_sync_status(
        self,
        item: CustomItem,
        actual_source: str,
        bgm_se_id: str,
        bgm_ep_id: str,
        bgm_title: str,
        mark_status: int,
    ) -> str:
        """根据标记结果构建结果消息并发送通知。返回 result_message。"""
        if mark_status == 0:
            result_message = "已看过，不再重复标记"
            logger.debug(
                f"bgm: {bgm_title or item.title} S{item.season:02d}E{item.episode:02d} {result_message}"
            )

            notification_service.notify(
                "mark_skipped",
                item,
                actual_source,
                subject_id=bgm_se_id,
                episode_id=bgm_ep_id,
                bgm_title=bgm_title,
            )

        elif mark_status == 1:
            result_message = "已标记为看过"
            logger.debug(
                f"bgm: {bgm_title or item.title} S{item.season:02d}E{item.episode:02d} {result_message} https://bgm.tv/ep/{bgm_ep_id}"
            )

            notification_service.notify(
                "mark_success",
                item,
                actual_source,
                subject_id=bgm_se_id,
                episode_id=bgm_ep_id,
                bgm_title=bgm_title,
            )

        else:
            result_message = "已添加到收藏并标记为看过"
            logger.debug(
                f"bgm: {bgm_title or item.title} 已添加到收藏 https://bgm.tv/subject/{bgm_se_id}"
            )
            logger.debug(
                f"bgm: {bgm_title or item.title} S{item.season:02d}E{item.episode:02d} 已标记为看过 https://bgm.tv/ep/{bgm_ep_id}"
            )

            notification_service.notify(
                "mark_success",
                item,
                actual_source,
                subject_id=bgm_se_id,
                episode_id=bgm_ep_id,
                bgm_title=bgm_title,
            )

        return result_message

    def _mark_subject_completed_if_needed(
        self,
        item: CustomItem,
        bgm: BangumiApi,
        bgm_se_id: str,
        bgm_title: str,
    ) -> None:
        """根据配置在单集标记后尝试将条目归档为「看过」（仅副作用，无返回值）。"""
        if item.media_type == "movie" and config_manager.get(
            "sync", "movie_mark_subject_completed", fallback=True
        ):
            try:
                coll = bgm.get_subject_collection(str(bgm_se_id))
                if coll.get("type") == COLLECTION_TYPE_DONE:
                    logger.debug(
                        "剧场版条目收藏状态已为「看过」，跳过条目标记: "
                        f"subject_id={bgm_se_id}"
                    )
                else:
                    bgm.change_collection_state(subject_id=str(bgm_se_id), state=2)
            except Exception as e:
                logger.warning(
                    f"剧场版条目标记为看过失败（单集已处理）: subject_id={bgm_se_id} {e}"
                )

        if item.media_type != "movie" and config_manager.get(
            "sync", "anime_mark_subject_completed", fallback=False
        ):
            try:
                coll = bgm.get_subject_collection(str(bgm_se_id))
                if coll.get("type") == COLLECTION_TYPE_DONE:
                    logger.debug(
                        "TV条目收藏状态已为「看过」，跳过条目标记: "
                        f"subject_id={bgm_se_id}"
                    )
                else:
                    # 获取番剧的总集数，如果已看集数等于或多于总集数，则自动归档为「看过」
                    subject_info = bgm.get_subject(bgm_se_id)
                    total_eps = subject_info.get("eps", 0)
                    watched_eps = coll.get("ep_status", 0) or 0
                    logger.debug(
                        f"获取到Subject: {bgm_se_id}, 总ep: {total_eps}, 已观看: {watched_eps}, coll: {coll}"
                    )
                    if total_eps > 0:
                        if watched_eps >= total_eps:
                            bgm.change_collection_state(
                                subject_id=str(bgm_se_id), state=2
                            )
                            logger.debug(
                                f"bgm: {bgm_title or item.title} 所有剧集已看完（已看 {watched_eps}/{total_eps} 集），已自动归档为「看过」"
                            )
            except Exception as e:
                logger.warning(
                    f"TV番剧自动归档为「看过」失败（单集已处理）: subject_id={bgm_se_id} {e}"
                )

    def _allocate_inline_run_id(self) -> str:
        """直调 sync_custom_item 时分配 run_id。"""
        with self._tasks_lock:
            self._task_counter += 1
            counter = self._task_counter
        return new_inline_sync_run_id(counter)

    def sync_custom_item(
        self, item: CustomItem, source: str = "custom"
    ) -> SyncResponse:
        """同步自定义项目（阶段四：委托给 SyncOrchestrator）"""
        return self._orchestrator.sync_custom_item(item, source)

    # 阶段四：_sync_custom_item_impl / _sync_custom_item_body 已迁入
    # SyncOrchestrator._sync_impl / _body，统一编排请求→匹配→标记→持久化。

    def _check_user_permission(
        self, user_name: str, source: str = ""
    ) -> tuple[bool, str]:
        """检查用户是否有权限同步。

        返回 (allowed, error_message)：
        - allowed=True 时 error_message 为空
        - allowed=False 时 error_message 为细粒化错误原因
        """
        # 测试来源跳过权限校验（由 sync.test_skip_permission_check 控制）
        is_test_source = source in ("test", "test-match")
        # fongmi 调试同步也视为测试来源
        if source and source.startswith("fongmi-debug"):
            is_test_source = True
        if is_test_source and config_manager.get(
            "sync", "test_skip_permission_check", fallback=False
        ):
            return True, ""

        # DB 为唯一真相源：按账号数量推导单/多用户语义（列表长度=1 即单用户）
        from app.core import accounts as _accounts

        accounts_list = _accounts.list_bangumi_accounts()
        if len(accounts_list) <= 1:
            # 单用户语义：检查 media_server_usernames 是否包含该用户
            allowed = _accounts.get_single_mode_media_usernames()
            if not allowed:
                logger.error(
                    "未设置 Bangumi 账号的 media_server_usernames（媒体服务器用户名），请检查配置"
                )
                return False, (
                    "未配置媒体服务器用户名（media_server_username），"
                    "请在配置页面填写后再同步"
                )
            if user_name not in allowed:
                logger.debug(f"非配置同步用户：{user_name}，跳过")
                return False, (
                    f"用户 {user_name} 不在允许同步的媒体服务器用户名列表中"
                    f"（当前配置: {', '.join(allowed)}）"
                )
        else:
            # 多用户语义：检查用户是否在映射中
            user_mappings = _accounts.get_user_mappings()
            if user_name not in user_mappings:
                logger.debug(f"多用户模式下用户 {user_name} 未配置映射，跳过")
                return False, (
                    f"用户 {user_name} 未在用户映射中配置（多用户模式下"
                    "需在 Bangumi 账号中填写 media_server_username）"
                )

            # 检查对应的bangumi配置是否存在且有效
            bangumi_config = self._get_bangumi_config_for_user(user_name)
            if not bangumi_config:
                logger.error(f"多用户模式下用户 {user_name} 的bangumi配置无效")
                return False, (
                    f"用户 {user_name} 对应的 Bangumi 账号配置无效或缺少 access_token"
                )

        return True, ""

    def _is_title_blocked(self, title: str, ori_title: str = None) -> bool:
        """检查番剧标题是否包含屏蔽关键词"""
        # 获取屏蔽关键词配置
        blocked_keywords_str = config_manager.get(
            "sync", "blocked_keywords", fallback=""
        ).strip()

        # 如果没有配置屏蔽关键词，直接返回False
        if not blocked_keywords_str:
            return False

        # 解析屏蔽关键词列表
        blocked_keywords = [
            keyword.strip()
            for keyword in blocked_keywords_str.split(",")
            if keyword.strip()
        ]

        # 如果解析后的关键词列表为空，直接返回False
        if not blocked_keywords:
            return False

        # 检查主标题
        if title:
            for keyword in blocked_keywords:
                if keyword.lower() in title.lower():
                    logger.info(
                        f'番剧标题 "{title}" 包含屏蔽关键词 "{keyword}"，跳过同步'
                    )
                    return True

        # 检查原始标题
        if ori_title:
            for keyword in blocked_keywords:
                if keyword.lower() in ori_title.lower():
                    logger.info(
                        f'番剧原始标题 "{ori_title}" 包含屏蔽关键词 "{keyword}"，跳过同步'
                    )
                    return True

        return False

    def _format_subject_not_found_message(self, item: CustomItem, detail: str) -> str:
        """同步记录用：未找到条目时的说明（与日志语义对齐）。"""
        parts = ["未查询到番剧信息，跳过"]
        if detail:
            parts.append(detail)
        if item.release_date and len(item.release_date) >= 8:
            parts.append(f"premiere_date={item.release_date[:10]}")
        return "；".join(parts)

    def _get_match_confidence_threshold(self) -> float:
        """读取同步配置 `match_confidence_threshold`（模糊匹配自动采用阈值）。

        低于该相似度的 Bangumi API 匹配不会自动采用，而是沉淀到待审队列。
        默认 0.6；配置缺失或非法时回退到默认值。
        """
        try:
            val = config_manager.get("sync", "match_confidence_threshold", fallback=0.6)
            return float(val)
        except (TypeError, ValueError):
            return 0.6

    def _find_subject_id(
        self, item: CustomItem, trace: MatchTrace | None = None
    ) -> tuple[str | None, bool, str]:
        """根据标题和日期查找番剧ID。

        返回 (subject_id, is_season_matched_id, failure_detail)。
        成功时 failure_detail 为空字符串；失败时为简短原因，供同步记录与日志使用。

        当传入 trace 时，会记录每个匹配阶段的详细过程。

        阶段三：通过 MatchPipeline 编排 4 个 step（Normalize/CustomMapping/
        BangumiData/APISearch），trace 填充收敛到 _record_trace 单一入口。
        """
        # 构建管道上下文
        from app.services.matching.context import MatchContext
        from app.services.matching.pipeline import MatchPipeline
        from app.services.matching.steps.api_search_main import APISearchStep
        from app.services.matching.steps.archive_shortcut import ArchiveShortcutStep
        from app.services.matching.steps.bangumi_data import BangumiDataStep
        from app.services.matching.steps.custom_mapping import CustomMappingStep
        from app.services.matching.steps.normalize import NormalizeStep

        actual_trace = trace or MatchTrace()
        # bgm 延迟到 ArchiveShortcutStep/APISearchStep 内部获取：
        # custom_mapping/bangumi_data 不需要 bgm
        ctx = MatchContext(
            item=item,
            bgm=None,  # ArchiveShortcutStep/APISearchStep 内部通过 service 获取
            trace=actual_trace,
            service=self,
        )

        # 构建管道：Normalize → CustomMapping → BangumiData → ArchiveShortcut → APISearch
        # ArchiveShortcut 与 APISearchStep 同级：archive 开启且命中时设置 ctx.bgm_data，
        # APISearchStep 检测到已有数据后跳过 bgm_search()，直接走候选排序 + post_search 改选。
        # archive 关闭/未命中时 APISearchStep 正常走 API 搜索（托底）。
        pipeline = MatchPipeline(
            [
                NormalizeStep(),
                CustomMappingStep(),
                BangumiDataStep(),
                ArchiveShortcutStep(),
                APISearchStep(),
            ]
        )

        result = pipeline.run(ctx)

        # 传播 ctx.is_ambiguous 到 trace，编排器据此发 match_ambiguous 通知
        actual_trace.is_ambiguous = ctx.is_ambiguous

        # 匹配歧义检测已前移到 APISearchStep（设置 ctx.is_ambiguous），
        # 通知职责由编排器统一发送，_find_subject_id 不再直接发通知。

        return (
            result.subject_id,
            result.is_season_matched_id,
            result.failure_detail,
        )

    def _get_bangumi_config_for_user(self, user_name: str) -> dict[str, str] | None:
        """根据媒体服务器用户名获取对应的bangumi配置（DB 为唯一真相源）"""
        from app.core import accounts as _accounts

        return _accounts.get_bangumi_config_for_user(user_name)

    def _get_bangumi_api_for_user(self, user_name: str) -> BangumiApi | None:
        """根据用户名获取对应的BangumiApi实例

        按用户缓存实例，配置变更时自动失效重建。
        复用实例可避免每次同步都重新构造 httpx.Client（含连接池建立），
        同时让 BangumiApi 内部的 OrderedDict 实例缓存跨调用点生效，
        显著降低单次同步耗时（特别是跨季遍历场景）。

        线程安全说明：dict get/set 在 GIL 下原子，最坏情况是两个线程
        同时 miss 各创建一个实例，最终 dict 被覆盖，可接受（比加锁性能好）。
        """
        bangumi_config = self._get_bangumi_config_for_user(user_name)
        if not bangumi_config:
            return None

        if not bangumi_config["username"] or not bangumi_config["access_token"]:
            logger.error(f"用户 {user_name} 的bangumi配置不完整")
            return None

        # 组装配置快照：bangumi section + dev section 中影响 API 行为的字段
        dev_snapshot = config_manager.get_dev_http_snapshot()
        config_snapshot = {
            "username": bangumi_config["username"],
            "access_token": bangumi_config["access_token"],
            "private": bangumi_config["private"],
            "http_proxy": dev_snapshot["script_proxy"],
            "ssl_verify": dev_snapshot["ssl_verify"],
            "bgm_api_proxy": dev_snapshot["bgm_api_proxy"],
            "bgm_next_proxy": dev_snapshot["bgm_next_proxy"],
            "ech_mode": dev_snapshot["ech_mode"],
        }

        # 缓存命中：实例存在且配置快照未变化
        cached = self._bangumi_api_cache.get(user_name)
        if cached is not None and cached[1] == config_snapshot:
            return cached[0]

        # 未命中或配置变更：创建新实例
        api = BangumiApi(
            username=config_snapshot["username"],
            access_token=config_snapshot["access_token"],
            private=config_snapshot["private"],
            http_proxy=config_snapshot["http_proxy"],
            ssl_verify=config_snapshot["ssl_verify"],
            bgm_api_proxy=config_snapshot["bgm_api_proxy"],
            bgm_next_proxy=config_snapshot["bgm_next_proxy"],
            ech_mode=config_snapshot["ech_mode"],
        )
        self._bangumi_api_cache[user_name] = (api, config_snapshot)
        return api

    def _get_bangumi_data(self) -> BangumiData:
        """获取BangumiData实例（使用实例缓存避免内存泄漏）"""
        if self._bangumi_data_cache is None:
            self._bangumi_data_cache = bangumi_data
        return self._bangumi_data_cache

    def reset_all_api_unreachable_flags(self) -> int:
        """批量复位所有缓存 BangumiApi 实例的不可达标记，返回复位数量。

        补发调度器批量探测成功（_probe_api）后调用：一次探测通过即统一恢复
        全部用户实例，避免每个实例各自等 TTL 到期、每条同步任务各探一次的
        「无用重试」堆积。探测失败时不调用，维持不可达状态。
        """
        count = 0
        # 快照遍历：_get_bangumi_api_for_user（sync worker 线程）会并发写入
        # _bangumi_api_cache，直接遍历 dict 在写入时触发
        # "dictionary changed size during iteration"。GIL 下 list() 原子拷贝
        # 引用，与既有"不加锁、接受偶发覆盖"的设计一致。
        for _user_name, (api, _snapshot) in list(self._bangumi_api_cache.items()):
            if api.is_api_unreachable():
                api.mark_api_reachable()
                count += 1
        if count:
            logger.info(f"📚 已统一复位 {count} 个 BangumiApi 实例的不可达标记")
        return count

    # ------------------------------------------------------------------
    # 待同步队列补发：API 恢复后由调度器或手动 API 触发
    # ------------------------------------------------------------------

    def replay_pending_item(self, record: dict) -> dict[str, Any]:
        """补发单条待同步任务，返回 {success, message, should_mark_synced, sync_record_id}

        从 pending_sync_queue 表读取一条记录，反序列化 payload：
        - 剧场版（media_type 为 movie/real_action）：走 ensure_subject_watching 链路
        - 剧集：走完整的 mark_episode_watched 链路
        成功时 should_mark_synced=True；仍然不可达时 should_mark_synced=False
        （不入队，因为已在队列里，只累加 attempts）；
        业务错误时 should_mark_synced=True（标记 abandoned）。

        返回值中 sync_record_id 为关联的 sync_records 行 id（可能为 None，旧数据无此字段），
        供调用方在补发成功/放弃时回写 sync_records.status 形成状态闭环。
        """
        record_id = int(record.get("id", 0))
        user_name = record.get("user_name", "")
        subject_id = str(record.get("subject_id", ""))
        episode_id = record.get("episode_id") or ""
        payload_json = record.get("payload_json") or "{}"
        # pending_sync_queue.sync_record_id（旧数据为 NULL）
        sync_record_id_raw = record.get("sync_record_id")
        sync_record_id: int | None = None
        if sync_record_id_raw is not None:
            try:
                sync_record_id = int(sync_record_id_raw)
            except (TypeError, ValueError):
                sync_record_id = None

        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = {}

        # 反序列化为 CustomItem 以复用同步逻辑
        try:
            item = CustomItem(**payload)
        except Exception as e:
            logger.error(
                f"补发失败：payload 反序列化异常 record_id={record_id} error={e}"
            )
            return {
                "success": False,
                "message": f"payload 反序列化失败: {e}",
                "should_mark_synced": True,  # 数据损坏，标记为已处理避免无限重试
                "sync_record_id": sync_record_id,
            }

        bgm = self._get_bangumi_api_for_user(user_name)
        if not bgm:
            return {
                "success": False,
                "message": f"用户 {user_name} 的 bangumi 配置不可用",
                "should_mark_synced": False,  # 配置问题，不标记，等用户修复
                "sync_record_id": sync_record_id,
            }

        # 补发场景下，调度器已通过 _probe_api 确认 API 可达；
        # 但缓存的 BangumiApi 实例可能仍带着上一轮失败的 _api_unreachable 标记（TTL 未过期）。
        # 强制清除标记，避免 mark_episode_watched/ensure_subject_watching 第一步就被短路返回 _PendingSyncQueued。
        bgm.mark_api_reachable()

        # 优先按队列里存的 subject_id + episode_id 直接标记
        # （比走完整匹配链路快且避免重复消耗 API 调用）
        try:
            bgm_se_id = str(subject_id)
            bgm_ep_id = str(episode_id) if episode_id else ""
        except Exception as e:
            return {
                "success": False,
                "message": f"subject_id/episode_id 解析失败: {e}",
                "should_mark_synced": True,
                "sync_record_id": sync_record_id,
            }

        # 剧场版场景：仅标记条目为在看，不点单集
        is_movie = payload.get("media_type") in ("movie", "real_action")

        try:
            if is_movie:
                mark_status = bgm.ensure_subject_watching(bgm_se_id)
            else:
                mark_status = self._retry_mark_episode(
                    bgm,
                    bgm_se_id,
                    bgm_ep_id,
                    queue_payload=item.model_dump(),
                )
        except _PendingSyncQueued:
            # 仍然不可达，不累加 attempts（避免 TTL 期内频繁重试时累加无意义次数）
            return {
                "success": False,
                "message": "API 仍不可达，已重新入队（不累加 attempts）",
                "should_mark_synced": False,
                "sync_record_id": sync_record_id,
            }
        except Exception as e:
            if is_movie:
                logger.error(
                    f"补发标记失败 record_id={record_id} subject={bgm_se_id}: {e}"
                )
            else:
                logger.error(
                    f"补发标记失败 record_id={record_id} "
                    f"subject={bgm_se_id} ep={bgm_ep_id}: {e}"
                )
            return {
                "success": False,
                "message": f"标记失败: {e}",
                "should_mark_synced": False,  # 业务异常暂不放弃，由 attempts 累加控制
                "sync_record_id": sync_record_id,
            }

        if mark_status == MARK_QUEUED:
            # 仍然不可达，已重新入队（去重逻辑会刷新原记录的 created_at）
            return {
                "success": False,
                "message": "API 仍不可达，已重新入队",
                "should_mark_synced": False,
                "sync_record_id": sync_record_id,
            }

        # 补发成功，发通知（可选）
        try:
            notification_service.notify(
                "sync_replayed",
                item,
                record.get("source", "") or "replay",
                subject_id=bgm_se_id,
                episode_id=bgm_ep_id,
                mark_status=mark_status,
            )
        except Exception:
            pass

        return {
            "success": True,
            "message": f"补发成功 mark_status={mark_status}",
            "should_mark_synced": True,
            "mark_status": mark_status,
            "sync_record_id": sync_record_id,
        }

    def replay_pending_batch(
        self, limit: int = 20, user_name: str | None = None
    ) -> dict[str, Any]:
        """批量补发待同步任务，返回统计 {total, success, failed, still_unreachable}

        user_name 非 None 时仅补发该用户的任务（多用户隔离）。
        """
        max_attempts = int(
            config_manager.get("bangumi-replay", "max_attempts", fallback=50)
        )
        records = database_manager.fetch_pending_sync(
            limit=limit, max_attempts=max_attempts, user_name=user_name
        )
        if not records:
            return {"total": 0, "success": 0, "failed": 0, "still_unreachable": 0}

        total = len(records)
        success = 0
        failed = 0
        still_unreachable = 0
        threshold = int(
            config_manager.get(
                "bangumi-replay", "replay_unreachable_threshold", fallback=3
            )
        )
        consecutive_unreachable = 0

        for record in self._iter_batch(records):
            record_id = int(record.get("id", 0))
            result = self.replay_pending_item(record)
            sync_record_id = result.get("sync_record_id")
            if result["success"]:
                success += 1
                consecutive_unreachable = 0
                if result.get("should_mark_synced"):
                    database_manager.mark_pending_sync_synced(record_id)
                    # 回写 sync_records：queued → retried（补发成功统一为 retried，
                    # 与手动重试/候选确认补发路径一致，避免统计分裂）
                    if sync_record_id:
                        try:
                            database_manager.update_sync_record_status(
                                sync_record_id,
                                "retried",
                                f"📚 补发成功（{result.get('message', '')}）",
                            )
                        except Exception as e:
                            logger.warning(
                                f"📚 回写 sync_records 状态失败 "
                                f"sync_record_id={sync_record_id}: {e}"
                            )
            else:
                # 检查消息判断是否仍然不可达
                msg = (result.get("message") or "").lower()
                if "不可达" in msg or "unreachable" in msg:
                    still_unreachable += 1
                    consecutive_unreachable += 1
                    # 连续 N 条不可达才中止，避免单条瞬时故障阻断后续
                    if consecutive_unreachable >= threshold:
                        logger.info(f"📚 连续 {threshold} 条任务不可达，中止本轮补发")
                        break
                else:
                    failed += 1
                    consecutive_unreachable = 0
                    database_manager.increment_pending_sync_attempts(
                        record_id, result.get("message", "")
                    )
                    # 超过最大重试次数则放弃
                    attempts = int(record.get("attempts", 0)) + 1
                    if attempts >= max_attempts:
                        database_manager.mark_pending_sync_abandoned(
                            record_id,
                            reason=f"exceeded max attempts ({max_attempts})",
                        )
                        # 回写 sync_records：queued → error（补发放弃）
                        if sync_record_id:
                            try:
                                database_manager.update_sync_record_status(
                                    sync_record_id,
                                    "error",
                                    f"📚 补发放弃：超过最大重试次数 "
                                    f"({max_attempts})，最后错误："
                                    f"{result.get('message', '')}",
                                )
                            except Exception as e:
                                logger.warning(
                                    f"📚 回写 sync_records 状态失败 "
                                    f"sync_record_id={sync_record_id}: {e}"
                                )

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "still_unreachable": still_unreachable,
        }

    def _iter_batch(self, records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """逐条产出补发记录，期间持有批次上下文（[batch:...] 与 batch_id 落库）。

        生成器形式：循环体无需整体缩进，break/提前退出时生成器关闭，
        finally 自动复位批次上下文。
        """
        with batch_log_context(new_batch_id()):
            yield from records

    def _load_custom_mappings(self) -> dict[str, str]:
        """从外部JSON文件读取自定义映射配置"""
        return mapping_service.load_custom_mappings()


# 全局同步服务实例（懒加载：首次访问 sync_service 时才创建实例与线程池）
_sync_service: SyncService | None = None


def get_sync_service() -> SyncService:
    """获取全局同步服务单例（惰性创建）。"""
    global _sync_service
    if _sync_service is None:
        _sync_service = SyncService()
    return _sync_service


def set_sync_service(instance: SyncService) -> None:
    """替换同步服务实例（测试/DI 注入）。"""
    global _sync_service
    _sync_service = instance


def reset_sync_service() -> None:
    """复位同步服务单例，下次访问时重建。"""
    global _sync_service
    _sync_service = None


def __getattr__(name: str) -> Any:
    """模块级懒加载，避免 import 时即创建 ThreadPoolExecutor。"""
    if name == "sync_service":
        return get_sync_service()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
