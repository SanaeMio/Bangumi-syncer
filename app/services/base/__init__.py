"""驱动抽象基类

为 feiniu/fongmi/trakt 等驱动提供统一的调度器与数据模型基类。
"""

from .models import BaseSyncResult, BaseWatchRecord
from .scheduler import BaseScheduler

__all__ = ["BaseScheduler", "BaseSyncResult", "BaseWatchRecord"]
