"""Agent 会话数据模型。

``AgentRunRecord`` 是 ``agent_runs`` 表单行的类型化视图，字段对齐
``app/core/database/connection.py`` 中的建表 schema。调度器把 repo 返回的
dict 行经 ``model_validate`` 转为模型后以属性访问消费，避免 ``run["x"]``
拼写错误在运行时才暴露。

``extra="allow"``：未来给 ``agent_runs`` 增列时不会让解析失败（多余列保留），
保证调度器对 schema 演进保持兼容。
"""

from pydantic import BaseModel, Field


class AgentRunRecord(BaseModel):
    """agent_runs 单行记录（调度器消费 repo 返回 dict 的类型化视图）。"""

    model_config = {"extra": "allow"}

    id: int | None = Field(None, description="自增主键")
    run_id: str = Field(..., description="会话唯一标识")
    task_type: str = Field(..., description="任务类型（如 match）")
    sync_record_id: int | None = Field(
        None, description="关联同步记录 id（persist 后回填，存在毫秒级窗口）"
    )
    business_key: str = Field("", description="业务去重键")
    status: str = Field("pending", description="会话状态")
    stop_reason: str = Field("", description="终止原因")
    attempts: int = Field(0, description="调度轮次失败计数")
    total_attempts: int = Field(0, description="业务键维度累计失败次数")
    last_attempt_at: int = Field(0, description="最近一次尝试时间（epoch 秒）")
    last_error: str | None = Field(None, description="最近一次错误")
    total_tokens: int = Field(0, description="全轮累计 token 用量")
    started_at: int = Field(0, description="开始时间（epoch 秒）")
    ended_at: int = Field(0, description="结束时间（epoch 秒）")
    created_at: int = Field(0, description="创建时间（epoch 秒）")
