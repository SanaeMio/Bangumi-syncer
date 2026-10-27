"""集数解析 step：调用 _resolve_season_episode 解析季度与集数 ID

输入（ctx）：subject_id / is_season_matched_id / item（匹配阶段产物）
产出（outputs）：subject_id / episode_id / changed / subject_url / episode_url
"""

from __future__ import annotations

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.steps.base import ExecutionStepBase

# 剧场版「正片文件」的判定字段：媒体库（fongmi / Plex 播放事件等）把实际播放的
# 文件地址放在 raw_payload["media"]["url"] 里。有它就说明这次推的是**正在播的
# 正片**，而不是「某条剧场版条目的收藏/想看动作」。
_MEDIA_URL_KEYS = ("media",)


class EpisodeResolveStep(ExecutionStepBase):
    """集数解析：根据 media_type 解析 Bangumi 季度与集数 ID"""

    stage = "episode_resolve"

    @staticmethod
    def _is_movie_file(item) -> bool:
        """请求是否携带剧场版**正片文件**

        只在 ``media_type == "movie"`` 时有意义：此时带文件地址即表示正在播放
        剧场版正片。用于决定「只标条目在看」还是走原有的条目内首集定位。
        """
        payload = getattr(item, "raw_payload", None)
        if not isinstance(payload, dict):
            return False
        for key in _MEDIA_URL_KEYS:
            media = payload.get(key)
            if isinstance(media, dict) and str(media.get("url") or "").strip():
                return True
        return False

    def execute(self, ctx: ExecutionContext, prev: dict | None = None) -> StepOutcome:
        # 集数分段映射换算出的目标集号（非 None 时优先于请求集号）
        target_ep = ctx.mapping_target_episode
        has_movie_episode = ctx.item.media_type == "movie" and self._is_movie_file(
            ctx.item
        )
        inputs = {
            "subject_id": str(ctx.subject_id),
            "is_season_id": bool(ctx.is_season_matched_id),
            "season": ctx.item.season,
            "episode": ctx.item.episode,
            "media_type": ctx.item.media_type,
            "release_date": ctx.item.release_date or "",
            "mapping_target_episode": (str(target_ep) if target_ep is not None else ""),
            "has_movie_episode": has_movie_episode,
        }

        try:
            bgm_se_id, bgm_ep_id = ctx.service._resolve_season_episode(
                ctx.bgm,
                ctx.item,
                ctx.subject_id,
                ctx.is_season_matched_id,
                target_episode=target_ep,
                # 显式绑定：条目内找不到该集时报错，不得沿续集链改选其它条目。
                # 改选恰恰发生在本步内部（get_target_season_episode_id 的回退），
                # 早于 CrossSeasonStep 的守卫，因此必须在这里就禁掉。
                mapping_is_explicit=ctx.mapping_is_explicit,
                has_movie_episode=has_movie_episode,
            )
        except ValueError as ve:
            if "认证失败" in str(ve) or "access_token" in str(ve):
                return StepOutcome(
                    status="error",
                    reason=f"认证失败: {ve}",
                    inputs=inputs,
                    outputs={
                        "subject_id": "",
                        "episode_id": "",
                        "changed": False,
                        "error": str(ve),
                    },
                    error_detail={"type": "auth_failed", "message": str(ve)},
                    is_terminal=True,
                )
            raise ve

        changed = str(bgm_se_id) != str(ctx.subject_id) if bgm_se_id else False
        # 报告用集号：有分段映射时展示换算后的目标集号（否则用户看到媒体集号
        # 与解析结果对不上，会以为解析错了）
        shown_ep = target_ep if target_ep is not None else ctx.item.episode

        # 剧场版正片：只标条目在看，不点单集。
        #
        # 输出 movie_subject_marked 而不是空 episode_id —— 结果链 merge 会丢弃
        # 空串，下游 CrossSeasonStep 拿不到「上游已处理完」的信号，就会按
        # sort=1 去跨季找章节（命中即错标、找不到即「不存在或集数过多」）。
        if has_movie_episode and bgm_se_id and not bgm_ep_id:
            return StepOutcome(
                status="hit",
                subject_id=str(bgm_se_id),
                reason=(f"剧场版正片：subject={bgm_se_id} 标记在看（不点单集）"),
                inputs=inputs,
                outputs={
                    "subject_id": str(bgm_se_id),
                    "episode_id": "",
                    "changed": changed,
                    "movie_subject_marked": True,
                    "subject_url": f"https://bgm.tv/subject/{bgm_se_id}",
                },
            )

        if bgm_ep_id:
            return StepOutcome(
                status="hit",
                subject_id=str(bgm_se_id),
                reason=(
                    f"集数解析：subject={bgm_se_id} episode={shown_ep} → "
                    f"ep_id={bgm_ep_id}"
                ),
                inputs=inputs,
                outputs={
                    "subject_id": str(bgm_se_id),
                    "episode_id": str(bgm_ep_id),
                    "changed": changed,
                    "subject_url": f"https://bgm.tv/subject/{bgm_se_id}",
                    "episode_url": f"https://bgm.tv/ep/{bgm_ep_id}",
                },
            )

        return StepOutcome(
            status="miss",
            subject_id=str(bgm_se_id) if bgm_se_id else None,
            reason=f"集数解析未命中：subject={bgm_se_id} episode={shown_ep}",
            inputs=inputs,
            outputs={
                "subject_id": str(bgm_se_id) if bgm_se_id else "",
                "episode_id": "",
                "changed": changed,
                "error": "未找到对应集数",
            },
        )
