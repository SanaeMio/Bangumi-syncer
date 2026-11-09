"""sync_service 专用异常。"""


class PendingCandidateContractError(Exception):
    """候选写入契约被违反：当前 decision 不允许写 pending_candidates。

    由 ``_sediment_pending_candidate`` 在入口抛出；调用方捕获后记录日志，
    主同步继续（候选已存在或 run 仍在跑）。
    """

    def __init__(self, decision: str, sync_record_id: int | None) -> None:
        super().__init__(
            f"decision={decision} 不允许写 pending_candidates"
            f"（sync_record_id={sync_record_id}）"
        )
        self.decision = decision
        self.sync_record_id = sync_record_id


class PendingCandidatePersistError(Exception):
    """候选写入失败：候选丢失，需要显式告警。

    与 ``PendingCandidateContractError`` 的区别：契约错误表示"本次不该写"，
    候选已存在；persist 错误表示"该写但没写进去"，用户待确认页会没有记录。
    """

    def __init__(self, sync_record_id: int | None, reason: str = "") -> None:
        super().__init__(f"候选沉淀失败（sync_record_id={sync_record_id}）: {reason}")
        self.sync_record_id = sync_record_id
        self.reason = reason
