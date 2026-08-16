"""
匹配过程追踪数据结构

记录三段式匹配的完整过程（custom_mapping → bangumi_data → archive/api_search），
供"匹配记录"页面和"调试工具"展示。每次同步请求都会创建 MatchTrace 对象。

archive 与 api_search 共用第三阶段入口：当 BangumiApi 的 archive 短路命中时
stage/final_match_method 标记为 "archive"，否则降级走 API 标记为 "api_search"。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MatchCandidate:
    """匹配候选条目"""

    subject_id: str
    name: str = ""
    name_cn: str = ""
    score: float = 0.0
    platform: str = ""
    air_date: str = ""
    source: str = ""  # bangumi_data / archive / api_search
    # P0: 候选媒体类型（detect_media_type 判断结果），用于媒体类型改选排错
    media_type: str = ""
    # P2: 候选别名列表（infobox/aliases），用于理解 title_diff_ratio 为何给出该分数
    infobox_aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "name": self.name,
            "name_cn": self.name_cn,
            "score": round(self.score, 4),
            "platform": self.platform,
            "air_date": self.air_date,
            "source": self.source,
            "media_type": self.media_type,
            "infobox_aliases": self.infobox_aliases,
        }


@dataclass
class MatchStep:
    """单阶段匹配步骤"""

    stage: str  # custom_mapping / bangumi_data / archive / api_search
    status: str  # hit / miss / skipped / error
    subject_id: str | None = None
    score: float | None = None
    reason: str = ""
    candidates: list[MatchCandidate] = field(default_factory=list)
    elapsed_ms: int = 0
    # 仅 receive step 使用：驱动原始数据 + 驱动处理后数据
    raw_payload: dict[str, Any] | None = None
    processed_payload: dict[str, Any] | None = None
    # P0: status=error 时存储完整异常信息（type/message/traceback）
    error_detail: dict[str, Any] | None = None
    # P1: 实际发送给 API 的搜索参数（title 变体/date_range/subject_types 等）
    request_params: dict[str, Any] | None = None
    # P1: API 返回质量摘要（候选总数/是否 archive 短路/首条摘要）
    api_response_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "subject_id": self.subject_id,
            "score": round(self.score, 4) if self.score is not None else None,
            "reason": self.reason,
            "candidates": [c.to_dict() for c in self.candidates],
            "elapsed_ms": self.elapsed_ms,
            "raw_payload": self.raw_payload,
            "processed_payload": self.processed_payload,
            "error_detail": self.error_detail,
            "request_params": self.request_params,
            "api_response_summary": self.api_response_summary,
        }


@dataclass
class MatchTrace:
    """完整匹配过程追踪

    由 _find_subject_id 创建并填充，记录三段式匹配的每个阶段。
    匹配完成后写入 sync_records.match_trace（JSON）并返回给调用方。
    """

    request_title: str = ""
    request_ori_title: str = ""
    request_season: int = 1
    request_episode: int = 0
    request_media_type: str = ""
    request_release_date: str = ""
    request_sync_action: str = ""
    request_user_name: str = ""
    request_platform_hint: str = ""
    normalized_title: str = ""
    steps: list[MatchStep] = field(default_factory=list)
    final_subject_id: str | None = None
    final_episode_id: str | None = None
    final_match_method: str = (
        ""  # custom_mapping / bangumi_data / archive / api_search / failed
    )
    # 细粒度匹配方式（激活死状态 bgm.last_match_method）：
    # exact / prefix_variant / season_stripped / media_suffix_stripped /
    # unwrapped / main_segment / fuzzy / cross_season_chain
    final_match_method_detail: str = ""
    final_score: float | None = None
    # 新增：同步最终状态/消息/动作（用于流水线最后一步 result）
    final_status: str = ""
    final_message: str = ""
    final_action: str = ""
    # 匹配歧义标记：APISearchStep 检测 top1/top2 分数差 < 0.05 时置 True，
    # 编排器据此发送 match_ambiguous 通知（原 _maybe_notify_match_ambiguous 检测逻辑前移到 step）
    is_ambiguous: bool = False
    # P2: 匹配阶段总耗时（不含 receive/result 阶段），用于性能排错
    total_elapsed_ms: int = 0

    # 内部计时
    _current_step: MatchStep | None = field(default=None, repr=False)
    _step_start: float = field(default=0.0, repr=False)
    _trace_start: float = field(default=0.0, repr=False)

    def start_step(self, stage: str) -> MatchStep:
        """开始一个新匹配阶段"""
        self._finish_current_step()
        if self._trace_start == 0.0:
            self._trace_start = time.perf_counter()
        step = MatchStep(stage=stage, status="miss")
        self._current_step = step
        self._step_start = time.perf_counter()
        return step

    def _finish_current_step(self) -> None:
        """完成当前阶段，记录耗时"""
        if self._current_step is None:
            return
        if self._step_start > 0:
            self._current_step.elapsed_ms = int(
                (time.perf_counter() - self._step_start) * 1000
            )
        self.steps.append(self._current_step)
        self._current_step = None
        self._step_start = 0.0

    def finish(self) -> None:
        """完成整个匹配过程"""
        self._finish_current_step()
        if self._trace_start > 0:
            self.total_elapsed_ms = int(
                (time.perf_counter() - self._trace_start) * 1000
            )
        # final_subject_id 为空时标记 failed，但 low_confidence 除外：
        # low_confidence 已由 _record_trace 设置 final_match_method（如 api_search），
        # 且 final_score 有值，不应覆盖为 failed。
        if self.final_subject_id is None and not self.final_match_method:
            self.final_match_method = "failed"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 JSON 存储/传输）"""
        # 确保最后一步已收尾
        self._finish_current_step()
        if self._trace_start > 0 and self.total_elapsed_ms == 0:
            self.total_elapsed_ms = int(
                (time.perf_counter() - self._trace_start) * 1000
            )
        return {
            "request_title": self.request_title,
            "request_ori_title": self.request_ori_title,
            "request_season": self.request_season,
            "request_episode": self.request_episode,
            "request_media_type": self.request_media_type,
            "request_release_date": self.request_release_date,
            "request_sync_action": self.request_sync_action,
            "request_user_name": self.request_user_name,
            "request_platform_hint": self.request_platform_hint,
            "normalized_title": self.normalized_title,
            "steps": [s.to_dict() for s in self.steps],
            "final_subject_id": self.final_subject_id,
            "final_episode_id": self.final_episode_id,
            "final_match_method": self.final_match_method,
            "final_match_method_detail": self.final_match_method_detail,
            "final_score": round(self.final_score, 4)
            if self.final_score is not None
            else None,
            "final_status": self.final_status,
            "final_message": self.final_message,
            "final_action": self.final_action,
            "is_ambiguous": self.is_ambiguous,
            "total_elapsed_ms": self.total_elapsed_ms,
        }
