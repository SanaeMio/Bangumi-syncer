"""BangumiApi 收藏状态管理（mixin）"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger


class CollectionMixin:
    """收藏/章节状态相关方法（供 BangumiApi 组合）"""

    def get_subject_collection(self, subject_id: int) -> dict[str, Any]:
        res = self.get(f"users/{self.username}/collections/{subject_id}")
        if res.status_code == 404:
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(
                    f"get_subject_collection API返回非字典类型: {type(res)}, 内容: {res}"
                )
                res = {}
        except Exception as e:
            logger.error(f"get_subject_collection JSON解析失败: {e}")
            res = {}
        return res

    def get_ep_collection(self, episode_id: int) -> dict[str, Any]:
        res = self.get(f"users/-/collections/-/episodes/{episode_id}")
        if res.status_code == 404:
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(
                    f"get_ep_collection API返回非字典类型: {type(res)}, 内容: {res}"
                )
                res = {}
        except Exception as e:
            logger.error(f"get_ep_collection JSON解析失败: {e}")
            res = {}
        return res

    def ensure_subject_watching(self, subject_id: int) -> None:
        """
        仅将条目收藏置为「在看」(type=3)，不修改单集进度。

        Returns:
            0: 无需变更（已在看或已看过）
            1: 已新增收藏为在看，或从想看/搁置改为在看
        """
        data = self.get_subject_collection(subject_id)
        if not data:
            self.add_collection_subject(subject_id=subject_id, state=3)
            return 1
        if data.get("type") == 2:
            return 0
        if data.get("type") in (1, 4):
            self.change_collection_state(subject_id=subject_id, state=3)
            return 1
        return 0

    def mark_episode_watched(self, subject_id: int, ep_id: int) -> None:
        data = self.get_subject_collection(subject_id)

        # 如果未收藏，则先标记为在看，再点单集格子
        if not data:
            self.add_collection_subject(subject_id=subject_id)
            self.change_episode_state(ep_id=ep_id, state=2)
            return 2
        else:
            # 如果整部番已看过则跳过
            if data.get("type") == 2:
                return 0
            #  如果条目状态是想看或搁置则调整为在看
            if data.get("type") == 1 or data.get("type") == 4:
                self.change_collection_state(subject_id=subject_id, state=3)

        ep_data = self.get_ep_collection(ep_id)
        logger.debug(ep_data)
        # 如果单集已看过则跳过
        if ep_data.get("type") == 2:
            return 0
        else:
            # 否则直接点单集格子
            self.change_episode_state(ep_id=ep_id, state=2)
            return 1

    def add_collection_subject(
        self, subject_id: int, private: bool | None = None, state: int = 3
    ) -> None:
        private = self.private if private is None else private
        self.post(
            f"users/-/collections/{subject_id}",
            _json={"type": state, "private": bool(private)},
        )

    def change_collection_state(
        self, subject_id: int, private: bool | None = None, state: int = 3
    ) -> None:
        private = self.private if private is None else private
        self.post(
            f"users/-/collections/{subject_id}",
            _json={"type": state, "private": bool(private)},
        )

    def change_episode_state(self, ep_id: int, state: int = 2) -> None:
        res = self.put(f"users/-/collections/-/episodes/{ep_id}", _json={"type": state})
        if 333 < res.status_code < 444:
            raise ValueError(f"{res.status_code=} {res.text}")
        return res
