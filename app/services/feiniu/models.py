"""飞牛观看记录行模型"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FeiniuUser:
    guid: str
    username: str


@dataclass(frozen=True)
class FeiniuWatchRecord:
    """单条可同步的观看记录（已满足「看完」条件）"""

    item_guid: str
    user_guid: str
    username: str
    display_title: str
    original_title: Optional[str]
    season: int
    episode: int
    release_date: str
    update_time_ms: int
