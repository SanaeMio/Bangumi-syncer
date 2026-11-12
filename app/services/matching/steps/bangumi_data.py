"""bangumi-data 本地匹配 step（阶段三）

对应原 _find_subject_id 阶段 2：bangumi-data 本地匹配。
命中即终止，match_method=bangumi_data。
未命中时回传候选列表到 trace，供候选队列展示。
"""

from __future__ import annotations

from app.core.logging import logger
from app.services.matching.context import MatchContext
from app.services.matching.contracts import (
    SOURCE_BANGUMI_DATA,
    SubjectRef,
    make_candidate,
)
from app.services.matching.gates import BANGUMI_DATA_DISABLED
from app.services.matching.steps.base import (
    MatchStepBase,
    StepOutcome,
    skipped_outcome,
)
from app.services.sync_service.match_trace import MatchCandidate
from app.utils.bangumi_data.index import TmdbEpisodeMatch
from app.utils.bangumi_data.matching import MatchingMixin, date_diff_days

# 日期硬校验阈值（#4）：
# - 请求带 release_date 且命中条目有 begin 时，日期差 > REJECT 天数的命中
#   **不予采信**（降级为候选，交给后续 Archive / APISearch 兜底）。
#   依据：L2 中「巨型金丝雀」(1947→2002，差 55 年) 与「人生单车」
#   (2022→2014，差 8 年) 均因 bangumi-data 只有同名异条目而被直采错标，
#   oracle 条目根本不在 bangumi-data 中 —— 宁可漏标不能错标。
# - REJECT 与 LOW_CONFIDENCE 之间只做标记、不拦（先积累数据，确认无副作用再收紧）。
DATE_GUARD_REJECT_DAYS = 365
DATE_GUARD_LOW_CONFIDENCE_DAYS = 90


class BangumiDataStep(MatchStepBase):
    """bangumi-data 本地匹配

    - 调用 bgm_data.find_bangumi_id
    - 命中时设置 ctx.subject_id + ctx.match_stage=bangumi_data，终止管道
    - 未命中时回传候选列表到 outcome.candidates
    - bangumi-data 禁用时 skipped

    候选来源：find_bangumi_id 未命中时必然已发生过一次全表扫描，
    这里通过 candidates_out 出参直接取回这次扫描的候选，
    不再调用 find_bangumi_candidates 触发第二次全表扫描
    （未命中路径原本要扫两遍，见 CONTRACT-AND-PERF.md 1.4）。
    """

    stage = "bangumi_data"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        # 延迟导入：与 sync_service 内部使用同一 config_manager 引用，
        # 使测试 patch("app.services.sync_service.config_manager") 生效
        from app.services.sync_service import config_manager

        if not config_manager.get("bangumi_data", "enabled", fallback=True):
            return skipped_outcome(BANGUMI_DATA_DISABLED)

        release_date = None
        if ctx.item.release_date and len(ctx.item.release_date) >= 8:
            release_date = ctx.item.release_date[:10]
        inputs = {
            "title": ctx.item.title,
            "ori_title": ctx.item.ori_title or "",
            "release_date": release_date or "",
            "season": ctx.item.season,
            "media_type": ctx.item.media_type,
            "tmdb_id": ctx.item.tmdb_id or "",
        }

        try:
            bgm_data = ctx.service._get_bangumi_data()

            # 集级起点是 TMDB id + 集号的确定性映射，优先于标题近似匹配。
            # 命中时只写换算集号，不置 mapping_is_explicit：跨季链兜底与
            # 显式绑定语义均保持不变。未命中时完全回落到既有匹配链。
            episode_match = self._resolve_tmdb_episode(ctx, bgm_data)
            if episode_match is not None:
                return self._episode_range_outcome(ctx, episode_match, inputs)

            # 候选出参：复用 find_bangumi_id 内部那一次扫描的结果
            candidates_out: list[dict] = []
            result = bgm_data.find_bangumi_id(
                title=ctx.item.title,
                ori_title=ctx.item.ori_title,
                release_date=release_date,
                season=ctx.item.season,
                media_type=ctx.item.media_type,
                candidates_out=candidates_out,
            )

            if not result:
                # 未命中时回传候选列表到 trace，供候选队列展示
                candidates = self._to_match_candidates(candidates_out)
                reason = (
                    f"bangumi-data 无精确命中，回传 {len(candidates)} 条候选"
                    if candidates
                    else "bangumi-data 无匹配结果"
                )
                return StepOutcome(
                    status="miss",
                    reason=reason,
                    inputs=inputs,
                    outputs={
                        "subject_id": "",
                        "total_candidates": len(candidates),
                    },
                    candidates=candidates,
                )

            bangumi_data_id, matched_title, date_matched, matched_item = result

            # ---- 日期硬校验（#4） ----
            # 命中条目与请求日期相差过大时不采信：bangumi-data 可能只收录了同名异条目
            # （oracle 根本不在库中），直采即错标。
            guard_diff = self._matched_date_diff(
                candidates_out, bangumi_data_id, release_date
            )
            if guard_diff is not None and guard_diff > DATE_GUARD_REJECT_DAYS:
                logger.info(
                    f"bangumi-data 命中 {matched_title}({bangumi_data_id})，"
                    f"但放送日期与请求相差 {guard_diff} 天 > {DATE_GUARD_REJECT_DAYS} 天，"
                    "不予采信，降级为候选交给后续步骤兜底"
                )
                return StepOutcome(
                    status="miss",
                    reason=(
                        f"bangumi-data 命中 {matched_title}({bangumi_data_id}) 但日期差 "
                        f"{guard_diff} 天 > {DATE_GUARD_REJECT_DAYS} 天，不予采信"
                    ),
                    inputs=inputs,
                    outputs={
                        "subject_id": "",
                        "rejected_subject_id": str(bangumi_data_id),
                        "date_guard": "reject",
                        "date_diff_days": guard_diff,
                        "total_candidates": len(candidates_out),
                    },
                    candidates=self._to_match_candidates(candidates_out),
                )

            # 季度ID可信度判定
            #
            # _verify_season_by_relation 需要读 ctx.subject_id 做前传链查询，
            # 故先回填命中 ID（本步骤是终止步骤，ctx.subject_id 的最终值不变）。
            ctx.subject_id = bangumi_data_id
            is_season_matched_id = self._judge_season_matched_id(
                ctx, matched_title, date_matched, matched_item
            )

            date_guard = "ok"
            if guard_diff is not None and guard_diff > DATE_GUARD_LOW_CONFIDENCE_DAYS:
                date_guard = "low_confidence"

            ctx.match_stage = "bangumi_data"
            ctx.is_season_matched_id = is_season_matched_id
            outputs = {
                "subject_id": str(bangumi_data_id),
                "matched_title": matched_title,
                "date_matched": bool(date_matched),
                "is_season_matched_id": is_season_matched_id,
                "date_guard": date_guard,
            }
            if guard_diff is not None:
                outputs["date_diff_days"] = guard_diff
            return StepOutcome(
                status="hit",
                subject_id=bangumi_data_id,
                reason=(
                    f"bangumi-data 匹配到「{matched_title}」"
                    f"（日期{'对得上' if date_matched else '未对上'}，"
                    f"季度 ID {'可信' if is_season_matched_id else '存疑'}）"
                    + (
                        f"，播出日期相差 {guard_diff} 天（低置信）"
                        if date_guard != "ok"
                        else ""
                    )
                ),
                score=1.0 if date_matched else 0.8,
                inputs=inputs,
                outputs=outputs,
                is_terminal=True,
            )
        except Exception as e:
            logger.error(f"bangumi-data 匹配出错: {e}")
            from app.services.sync_service import _build_error_detail

            return StepOutcome(
                status="error",
                reason=f"bangumi-data 匹配异常：{e}",
                inputs=inputs,
                error_detail=_build_error_detail(e),
                # 显式声明「出错但可降级」：bangumi-data 是可选本地数据源，
                # 异常不应中断匹配，后续 ArchiveShortcut/APISearch 会托底。
                # 必须显式写 False —— StepOutcome 默认值在两条管线里语义不同
                # （SyncPipeline 见 status="error" 会无条件终止），见 StepOutcome 文档。
                is_terminal=False,
            )

    @staticmethod
    def _resolve_tmdb_episode(ctx: MatchContext, bgm_data) -> TmdbEpisodeMatch | None:
        """按请求的 TMDB 基 id 与集号解析集级起点。

        仅当请求携带 TMDB 基 id、且索引给出确定性的集级起点时返回结果。
        返回类型严格校验：bgm_data 可能是 mock 对象，任意方法的返回值
        不得被当成命中。
        """
        # 集级起点表达的是「剧集在合并季里的集边界」，剧场版没有集号概念。
        if ctx.item.media_type == "movie":
            return None
        tmdb_id = getattr(ctx.item, "tmdb_id", None)
        if not tmdb_id or not isinstance(tmdb_id, str):
            return None
        try:
            match = bgm_data.resolve_tmdb_episode(
                tmdb_id, ctx.item.season, ctx.item.episode
            )
        except Exception as e:  # noqa: BLE001 — 索引不可用时回落既有匹配链
            logger.debug(f"集级起点解析跳过: {e!r}"[:120])
            return None
        return match if isinstance(match, TmdbEpisodeMatch) else None

    @staticmethod
    def _episode_range_outcome(
        ctx: MatchContext, match: TmdbEpisodeMatch, inputs: dict
    ) -> StepOutcome:
        """集级起点命中的产物：直接采信条目与换算集号。

        日期硬护栏针对「标题近似匹配可能采信同名异条目」；集级起点由
        TMDB id 与集号确定性定位，不经该护栏。请求日期保留在 inputs 中供
        trace 观测。换算集号写入 mapping_target_episode（与自定义映射
        segments 共用）；mapping_is_explicit 保持原值，执行阶段的跨季链
        兜底不变。
        """
        ctx.subject_id = match.subject_id
        ctx.match_stage = "bangumi_data"
        ctx.is_season_matched_id = True
        ctx.mapping_target_episode = match.target_episode
        outputs = {
            "subject_id": match.subject_id,
            "matched_title": match.title,
            "is_season_matched_id": True,
            "tmdb_start_episode": match.start_episode,
            "target_episode": str(match.target_episode),
            "match_method": "tmdb_episode_range",
        }
        # C4：任何策略命中都必须产出候选，不许只回一个裸 id
        candidate = make_candidate(
            SubjectRef(
                subject_id=match.subject_id,
                name=match.title,
                air_date=match.begin[:10] if match.begin else "",
                source=SOURCE_BANGUMI_DATA,
            ),
            score=1.0,
        )
        return StepOutcome(
            status="hit",
            subject_id=match.subject_id,
            reason=(
                f"bangumi-data 集级起点命中：{match.title}，"
                f"媒体 S{ctx.item.season}E{ctx.item.episode} → "
                f"条目内第 {match.target_episode} 集"
            ),
            score=1.0,
            inputs=inputs,
            outputs=outputs,
            candidates=[candidate],
            is_terminal=True,
        )

    @staticmethod
    def _to_match_candidates(candidates_out: list[dict]) -> list[MatchCandidate]:
        """把 bangumi-data 候选字典转成 trace 候选（回传失败不影响主流程）"""
        try:
            return [
                MatchCandidate(
                    subject_id=str(c.get("id", "")),
                    name=c.get("name", ""),
                    name_cn=c.get("name_cn", ""),
                    score=float(c.get("score", 0.0)),
                )
                for c in candidates_out or []
            ]
        except Exception as cand_err:
            logger.debug(f"bangumi_data 候选回传失败（不影响主流程）: {cand_err}")
            return []

    @staticmethod
    def _matched_date_diff(
        candidates_out: list[dict], bangumi_data_id: str, release_date: str | None
    ) -> int | None:
        """命中条目 begin 与请求 release_date 的天数差

        返回 None 表示**不校验**：请求无日期、命中条目无 begin，或候选回传里
        找不到该命中条目（精确索引 O(1) 路径不产生 candidates_out —— 该路径
        本身已强制日期差 ≤180 天，无需再校验）。
        """
        if not release_date:
            return None
        begin = ""
        for c in candidates_out or []:
            if str(c.get("id", "")) == str(bangumi_data_id):
                begin = c.get("date") or ""
                break
        if not begin:
            return None
        return date_diff_days(begin, release_date)

    @staticmethod
    def _judge_season_matched_id(
        ctx: MatchContext,
        matched_title: str,
        date_matched: bool,
        matched_item: dict | None = None,
    ) -> bool:
        """判断 bangumi-data 命中的 ID 是否为特定季度ID

        四条判据依次尝试（任一成立即认为「命中的就是目标季」）：
        1. 请求本身是第一季 → 无需判断；
        2. 日期匹配 → 命中条目与请求同档期，视为同季；
        3. 标题含季度信息 —— 判定与下游季节过滤器
           （_filter_candidates_by_season / _candidate_season_numbers）共用同一
           证据基：先扫描命中条目的全部标题变体，任一变体声明了目标季号即视为
           季度可信，避免「过滤器放行、季度检查却因首个变体季号模糊而拒绝」的
           自相矛盾（issue #280：11145 的 zh-Hans[0] 为「笨蛋，测验，召唤兽2」
           不含季号，但另一变体「笨蛋测试召唤兽 第二季」声明了第2季）；兜底再查
           选定的 matched_title 本身（``第2季`` / ``S2`` / 结尾数字 ``XXX2`` 等）。
        4. 关联关系验证（本函数下方）—— 标题启发式的兜底。

        为什么需要第 4 条：Bangumi 大量续作的原名**完全不含季号**，例如
        ``バカとテストと召喚獣にっ！``（11145 = 第二季，仅靠 ``にっ！`` 暗示
        续作）、``WORKING'!!``。这类条目即使 bangumi-data 精确命中，前三条
        也全部为假 → 被误判成「命中的只是系列根条目」→ 执行阶段去爬续集链
        找第 N 季 → 链上已无下一季 → 报「不存在或集数过多」。Emby 推
        S02E12 的失败即由此而来。
        """
        if ctx.item.season <= 1:
            return True
        if date_matched:
            return True
        # 命中条目的任一标题变体声明了目标季号即置信（与过滤器口径一致）
        if (
            matched_item is not None
            and ctx.item.season in MatchingMixin._candidate_season_numbers(matched_item)
        ):
            return True
        # 兜底：检查选定的标题本身是否含季度信息
        if ctx.service._check_season_info_in_title(matched_title, ctx.item.season):
            return True
        return BangumiDataStep._verify_season_by_relation(ctx)

    @staticmethod
    def _verify_season_by_relation(ctx: MatchContext) -> bool:
        """用 Bangumi 关联关系确认「命中条目就是第 N 季」

        判据：沿「前传」链回溯，若链上条目数 == season - 1，则命中条目本身
        就是第 season 季（第一季无前传 → 命中条目是第 1 季；有一条前传链
        → 命中条目是第 2 季）。权威关系优于标题正则，且不受续作命名风格影响。

        仅在 ``season > 1`` 且前面三条判据都不成立时调用，因此这一次关系查询
        只发生在「本来就会失败」的路径上 —— 不增加正常请求的开销。Archive
        启用时走本地短路，几乎零 API 成本。

        任何异常（网络 / 归档不可用）都按「无法确认」处理，回落到原有行为
        （is_season_matched_id=False，由执行阶段爬续集链），不让新逻辑引入
        新的失败面。
        """
        season = ctx.item.season
        subject_id = ctx.subject_id
        if not subject_id:
            return False
        try:
            bgm = ctx.bgm
            if bgm is None:
                return False
            # 多走一跳（max_hops=season 而非 season-1）：search_previous_subjects
            # 在 max_hops 处截断，若按 season-1 截断，实际命中第 3+ 季条目时前传
            # 链被截成 season-1 条，会被误判成「就是第 season 季」。多要一跳后，
            # 被截断的链长度 ≥ season > season-1，判据不再有截断歧义。
            prequels = bgm.search_previous_subjects(
                int(subject_id), max_hops=max(1, season)
            )
            prequels = prequels or []
        except Exception as e:  # noqa: BLE001 — 关系不可用时回落到原有行为
            logger.debug(f"季度关系验证跳过（关系查询失败）: {e!r}"[:120])
            return False

        # 前传链长度 == season - 1 → 命中条目恰为第 season 季
        if len(prequels) == season - 1:
            logger.debug(
                f"季度关系验证通过: subject_id={subject_id}, season={season}, "
                f"前传链={prequels}"
            )
            return True
        logger.debug(
            f"季度关系验证未通过: subject_id={subject_id}, season={season}, "
            f"前传链长度={len(prequels)}"
        )
        return False
