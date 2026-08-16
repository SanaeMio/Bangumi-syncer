"""匹配步骤包

每个 step 职责单一：接收 ctx，执行匹配操作，返回 outcome。
不做 IO（不写 DB / 不发通知 / 不创建 bgm）。
"""
