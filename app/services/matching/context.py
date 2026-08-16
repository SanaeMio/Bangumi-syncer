"""管道中间产物

替代当前用局部变量在 _find_subject_id / _sync_custom_item_body 间传递
subject_id / bgm_se_id / bgm_ep_id 等状态的方式。步骤间显式传递状态，
避免 bgm 实例属性的隐式传递（如 last_match_method 死状态）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.sync import CustomItem
from app.services.sync_service.match_trace import MatchTrace


@dataclass
class MatchContext:
    """管道中间产物，步骤间传递

    编排器注入 item / bgm / trace，各 step 执行时读写 ctx 字段，
    最终由 MatchPipeline._build_result 映射为 MatchResult。
    """

    # 请求输入（编排器注入）
    # bgm 类型收窄为 BangumiSearchPort（只读端口）：
    # 匹配阶段只能调读方法，写方法在编排器标记阶段通过完整 BangumiApi 实例调用。
    # 运行时传入完整 BangumiApi 实例（结构化满足 Protocol），无需包装。
    item: CustomItem
    bgm: Any  # BangumiSearchPort | BangumiApi | None（Any 兼容测试 mock）
    trace: MatchTrace
    # sync_service 实例句柄，供 step 调用 normalize_title /
    # _get_bangumi_data / _check_season_info_in_title / _sort_candidates_by_platform
    # 等方法。保留：step 仍需访问 service 的匹配辅助方法（非 bgm 写操作）。
    service: Any = None

    # 阶段A输出：subject 匹配
    normalized_title: str = ""
    subject_id: str | None = None
    is_season_matched_id: bool = False
    match_stage: str = ""  # archive / api_search
    match_method_detail: str = ""  # 细粒度派生方式（激活死状态 last_match_method）
    final_score: float | None = None
    is_ambiguous: bool = False  # 歧义标记（原 _maybe_notify_match_ambiguous）

    # 阶段B输出：episode 解析
    bgm_se_id: str | None = None
    bgm_ep_id: str | None = None
    bgm_title: str = ""

    # 阶段二新增：bgm_search 内部状态
    stripped_title: str = ""
    stripped_ori: str = ""
    bgm_data: list[dict[str, Any]] | None = None  # bgm_search 返回的候选列表
    matched_variant_method: str = ""  # 命中的变体 method（替代 bgm.last_match_method）
    # bgm_search 调用方传入的 subject_types，供各 step 读取
    subject_types: list[int] | None = None
    # 日期精确搜索结果（用于 debug 日志）
    start_date_str: str = "无日期"
    end_date_str: str = "无日期"

    # 失败信息
    failure_detail: str = ""
