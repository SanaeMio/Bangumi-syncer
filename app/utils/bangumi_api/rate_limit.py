"""进程级 Bangumi API 令牌桶限速

Bangumi 官方公开 API 已限流（社区实测约 300 请求/分钟/IP，超限封禁 1 小时），
社区客户端普遍以 1 req/s 实践。本模块对所有访问 ``api.bgm.tv`` 的南向请求
做**进程级统一限速**：无论由哪个 ``BangumiApi`` 实例、哪个会话（含直连回退的
临时 client）发出，都经过同一个令牌桶。

设计要点：
- 同步、线程安全（``threading.Lock`` 保护），基于 ``time.monotonic``；
  等待采用"锁内检查、锁外休眠"，休眠期间不持锁，不阻塞其他请求与 429 通知；
- 采用 Go ``golang.org/x/time/rate`` 的**预扣（reserve）**语义：锁内一次性
  占用令牌（允许 ``_tokens`` 透支为负），后续请求按 ``-tokens/rate`` 排队，
  先到先得、等待时长单调不减，保证公平；``timeout``（默认 30s）是**总等待
  预算**：预约时刻超限、或等待中被 429 冻结延长致累计超限 → 放弃预约（回滚
  扣减）并抛 :class:`RateLimitTimeoutError`；
- 429 冷却期内**快速失败**（既定口径）：预约时刻落在冻结窗口内且超过
  ``timeout`` 时立即抛错，**不排队等待冷却结束**；由调用方按自身语义
  重试/降级；
- :meth:`RateLimiter.acquire_async` 提供 async 兼容层：复用同一预约计算，
  等待改用 ``asyncio.sleep``，与同步 :meth:`RateLimiter.acquire` 共享令牌桶状态；
- 构造支持注入 ``clock`` / ``sleep``，便于测试不真实等待；
- 模块级惰性单例 :func:`get_bgm_rate_limiter`，从 ``[bangumi]`` 段读取
  ``api_rate_limit``（默认 1/s）与 ``api_rate_burst``（默认 3），解析失败回退默认；
  ``api_rate_limit`` 在配置层截断到 :data:`MAX_CONFIGURABLE_RATE`（100/s），
  防止极端速率与长冻结相乘产生巨额令牌债务；
- 收到 429 时由 :meth:`RateLimiter.notify_rate_limited` 冻结令牌桶：
  默认 60s，连续命中按 60→120→240 指数延长，上限 3600s；
  成功请求由 :meth:`RateLimiter.notify_success` 重置升级计数。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

from ...core.config import config_manager
from ...core.logging import logger

__all__ = [
    "DEFAULT_ACQUIRE_TIMEOUT",
    "DEFAULT_BURST",
    "DEFAULT_COOLDOWN",
    "DEFAULT_RATE",
    "MAX_CONFIGURABLE_RATE",
    "MAX_COOLDOWN",
    "RateLimitTimeoutError",
    "RateLimiter",
    "get_bgm_rate_limiter",
    "reset_bgm_rate_limiter",
]

DEFAULT_RATE = 1.0
DEFAULT_BURST = 3
DEFAULT_COOLDOWN = 60.0
MAX_COOLDOWN = 3600.0

# 可配置速率的合理上界（个/秒）。
# 冻结/等待延长会把「额外秒数 × rate」折算为令牌债务；若 rate 被配置得极大，
# 一次长冻结即可产生数万乃至更大的债务，令令牌桶长期瘫痪。配置层读取时统一
# 截断到该上界（不影响 ``RateLimiter`` 自身的构造语义，测试可用高速率实例）。
MAX_CONFIGURABLE_RATE = 100.0

# 单次 acquire 允许预约（排队）的最长等待秒数，超过则放弃并抛
# RateLimitTimeoutError，避免请求被无上限地挂起。
DEFAULT_ACQUIRE_TIMEOUT = 30.0

# 等待超过该阈值时记录 warning，便于线上定位限速拖慢
_WARN_WAIT_THRESHOLD = 1.0


class RateLimitTimeoutError(Exception):
    """预约的放行时刻超过调用方给定的 ``timeout``

    刻意不继承任何 httpx 异常，确保不会被 ``retry.RETRY_EXCEPTIONS``
    当作网络/服务端错误重复重试（重试只会加剧限速）。
    """


class RateLimiter:
    """同步、线程安全的令牌桶限速器

    Args:
        rate: 令牌补充速率（个/秒），必须 > 0
        burst: 桶容量（允许的突发请求数），必须 >= 1
        clock: 单调时钟注入点（默认 ``time.monotonic``）
        sleep: 等待注入点（默认 ``time.sleep``），测试可注入假实现
    """

    def __init__(
        self,
        rate: float = DEFAULT_RATE,
        burst: int = DEFAULT_BURST,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # 防御：非法参数回落默认值（配置层已归一化，此处兜底并记日志）
        if not isinstance(rate, (int, float)) or rate <= 0:
            logger.warning(
                f"⏳ RateLimiter 非法 rate={rate!r}，回退默认 {DEFAULT_RATE}/s"
            )
            rate = DEFAULT_RATE
        if not isinstance(burst, int) or burst < 1:
            logger.warning(
                f"⏳ RateLimiter 非法 burst={burst!r}，回退默认 {DEFAULT_BURST}"
            )
            burst = DEFAULT_BURST

        self.rate = float(rate)
        self.burst = int(burst)
        self._clock = clock
        self._sleep = sleep

        self._lock = threading.Lock()
        self._tokens = float(self.burst)
        self._last_refill = clock()
        # 429 自适应冷却
        self._frozen_until = 0.0
        self._consecutive_429 = 0

    # ------------------------------------------------------------------
    # 令牌获取
    # ------------------------------------------------------------------

    def acquire(self, timeout: float = DEFAULT_ACQUIRE_TIMEOUT) -> float:
        """获取一个令牌，令牌不足或处于冷却期时阻塞等待

        采用 Go ``rate.Limiter`` 的**预扣（reserve）**语义：先在锁内一次性
        完成预约（占用令牌、允许透支为负），再在锁外按预约的放行时刻休眠。
        等待期间不持有互斥锁，其他线程（含 :meth:`notify_rate_limited` /
        :meth:`notify_success`）可立即更新冻结状态；休眠醒来后复查，若等待
        期间收到更长的 429 冻结，则继续等待到新的放行时刻（并同步增加令牌
        债务，令后来者排在其后，保持 FIFO）。

        ``timeout`` 是**总等待预算**：预约时刻与等待中的冻结延长共同受约束；
        累计需要等待超过 ``timeout`` 时放弃本次预约（回滚令牌扣减，已放行者
        的时刻不受影响）并抛 :class:`RateLimitTimeoutError`。

        Args:
            timeout: 允许累计等待的最大秒数；超过时放弃并抛
                :class:`RateLimitTimeoutError`。
                默认 :data:`DEFAULT_ACQUIRE_TIMEOUT`。

        Returns:
            本次实际等待的秒数（无需等待时为 0.0）。

        Raises:
            RateLimitTimeoutError: 累计等待超过 ``timeout``。
        """
        reserved_act, reserved_debt = self._reserve(timeout)
        total_wait = 0.0
        while True:
            remaining, reserved_act, reason, extra_debt = self._remaining_wait(
                reserved_act
            )
            reserved_debt += extra_debt
            if remaining <= 0:
                return total_wait
            if total_wait + remaining > timeout:
                # 等待中放行时刻被 429 冻结推迟 → 累计将超预算：放弃并回滚
                self._rollback(reserved_debt)
                logger.warning(
                    f"⏳ 等待中放行时刻被 429 冻结推迟，累计等待 "
                    f"{total_wait + remaining:.2f}s 超过 timeout={timeout:.2f}s，"
                    "放弃本次令牌获取"
                )
                raise RateLimitTimeoutError(
                    f"限速排队累计需等待 {total_wait + remaining:.2f}s，"
                    f"超过 timeout={timeout:.2f}s（等待中冻结延长）"
                )
            total_wait += remaining
            # 锁外休眠：避免长时间持锁阻塞其他请求/通知
            self._wait(remaining, reason)

    async def acquire_async(self, timeout: float = DEFAULT_ACQUIRE_TIMEOUT) -> float:
        """``acquire`` 的 async 兼容层，供 agent 的异步循环使用

        预约计算复用 :meth:`_reserve` / :meth:`_remaining_wait`（仅短暂持有
        ``threading.Lock``，绝不跨 ``await`` 持锁），等待改用
        ``asyncio.sleep``，因此不会阻塞事件循环。与同步 :meth:`acquire`
        共享同一令牌桶状态，同一实例上混用安全。

        ``timeout`` 语义与 :meth:`acquire` 一致：**总等待预算**，预约时刻与
        等待中的冻结延长共同受约束，超限即放弃（回滚）并抛错。

        取消语义：预约在锁内已一次性扣减令牌（含冻结折算债务），令牌真正被
        "使用" 发生在 ``await`` 正常返回之后。若等待期间任务被
        ``CancelledError`` 取消（或累计等待超过 ``timeout`` 而放弃），必须在
        锁内冲销本预约的全部扣减（初始 1 令牌 + ``_reserve`` 冻结债务 +
        等待循环中 :meth:`_remaining_wait` 追加的冻结债务），否则令牌桶被永久
        透支；冲销后夹紧到 ``burst``（等待期间 :meth:`_refill` 可能已把令牌补到
        ``burst``，直接相加会把 ``_tokens`` 推过桶容量），最后原样 ``raise``，
        绝不吞掉取消。

        Args:
            timeout: 同 :meth:`acquire`。

        Returns:
            本次实际等待的秒数。

        Raises:
            RateLimitTimeoutError: 累计等待超过 ``timeout``。
            asyncio.CancelledError: 等待期间任务被取消（冲销预约后原样抛出）。
        """
        reserved_act, reserved_debt = self._reserve(timeout)
        total_wait = 0.0
        try:
            while True:
                remaining, reserved_act, reason, extra_debt = self._remaining_wait(
                    reserved_act
                )
                # 累计本次预约的全部扣减，供取消/超时放弃时精确冲销
                reserved_debt += extra_debt
                if remaining <= 0:
                    return total_wait
                if total_wait + remaining > timeout:
                    logger.warning(
                        f"⏳ 等待中放行时刻被 429 冻结推迟，累计等待 "
                        f"{total_wait + remaining:.2f}s 超过 timeout={timeout:.2f}s，"
                        "放弃本次令牌获取"
                    )
                    raise RateLimitTimeoutError(
                        f"限速排队累计需等待 {total_wait + remaining:.2f}s，"
                        f"超过 timeout={timeout:.2f}s（等待中冻结延长）"
                    )
                total_wait += remaining
                self._log_wait(remaining, reason)
                await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            self._rollback(reserved_debt)
            logger.info(
                f"⏳ acquire_async 等待被取消，已回滚预约扣减 {reserved_debt:.2f} 令牌"
            )
            raise
        except RateLimitTimeoutError:
            self._rollback(reserved_debt)
            raise

    def _rollback(self, reserved_debt: float) -> None:
        """锁内冲销预约扣减（含冻结折算债务），并夹紧到桶容量。

        供同步/异步等待路径在"放弃预约"（累计等待超预算 / 等待被取消）时归还
        容量；夹紧避免等待期间 :meth:`_refill` 已把令牌补到 ``burst`` 时直接相加
        把 ``_tokens`` 推过桶容量（超发）。
        """
        with self._lock:
            self._tokens = min(self._tokens + reserved_debt, self.burst)

    def _reserve(self, timeout: float) -> tuple[float, float]:
        """锁内完成令牌预约，返回 ``(预约放行时刻, 本预约扣减的令牌总量)``

        预扣算法（参考 Go ``reserveN``）：
        - 令牌可用时刻 ``bucket_act``：足够则为 ``now``，否则为
          ``now + (1 - _tokens) / rate``（``_tokens`` 透支为负）；
        - 放行时刻 ``act = max(bucket_act, _frozen_until)``，429 冻结作为下界；
        - 提交时 ``_tokens -= 1``，并把冻结造成的额外延时
          ``(act - bucket_act) * rate`` 一并折算为令牌债务，使冻结解除后
          排队的请求之间仍保持 ``1/rate`` 间隔（不超发）。

        返回的第二项是本次预约累计扣减的令牌数（``1 + 冻结折算债务``），
        供 :meth:`acquire_async` 在等待被取消时精确冲销。

        若 ``act - now > timeout``，则不修改任何状态并抛
        :class:`RateLimitTimeoutError`（等价于回滚预约，不影响其他等待者）。
        """
        with self._lock:
            now = self._clock()
            self._refill(now)
            bucket_act = (
                now if self._tokens >= 1.0 else now + (1.0 - self._tokens) / self.rate
            )
            act = max(bucket_act, self._frozen_until)
            required = act - now
            if required > timeout:
                # 放弃预约：未扣减令牌，已排队等待者的放行时刻不受影响
                logger.warning(
                    f"⏳ Bangumi API 预约等待 {required:.2f}s 超过 timeout="
                    f"{timeout:.2f}s，放弃本次令牌获取"
                )
                raise RateLimitTimeoutError(
                    f"限速排队需等待 {required:.2f}s，超过 timeout={timeout:.2f}s"
                )

            # 预扣一个令牌；冻结额外延时折算为令牌债务，令后来者排在其后
            self._tokens -= 1.0
            reserved_debt = 1.0
            freeze_extra = act - bucket_act
            if freeze_extra > 0:
                debt = freeze_extra * self.rate
                self._tokens -= debt
                reserved_debt += debt
                logger.debug(
                    f"⏳ 预约受 429 冻结推迟 {freeze_extra:.2f}s，"
                    f"折算令牌债务 {debt:.2f}"
                )
            return act, reserved_debt

    def _remaining_wait(self, reserved_act: float) -> tuple[float, float, str, float]:
        """锁内复查预约状态，返回 ``(还需等待秒数, 最新放行时刻, 等待原因, 本轮追加债务)``

        等待期间可能收到更长的 429 冻结（:meth:`notify_rate_limited`）。此处
        把超出原预约时刻的部分折算为令牌债务，保证冻结解除后仍严格执行 FIFO：
        后到但尚未预约的请求会排在当前等待者之后，不会抢先放行。

        不变式：所有南向 Bangumi 请求均经本令牌桶门控（见模块 docstring），
        等待期间不会再有请求真正触网产生新的 429；``_frozen_until`` 只会被
        :meth:`notify_rate_limited` 单向延长，且每次延长都把尚未放行者推到
        更晚时刻（等价于让其排到队尾）。因此延长不会让**已放行者**倒挂——
        已放行者的 ``reserved_act`` 早于或等于冻结起点，其放行时刻已是过去，
        延长只影响尚未放行者，FIFO 顺序保持单调不减。

        返回的第四项为本轮新折算的令牌债务增量（未延长时为 ``0.0``），供
        :meth:`acquire_async` 累计后在被取消时精确冲销。
        """
        with self._lock:
            now = self._clock()
            self._refill(now)
            extra_debt = 0.0
            if self._frozen_until > reserved_act:
                extra = self._frozen_until - reserved_act
                extra_debt = extra * self.rate
                self._tokens -= extra_debt
                reserved_act = self._frozen_until
                logger.debug(
                    f"⏳ 429 冻结延长 {extra:.2f}s，折算令牌债务 {extra_debt:.2f}"
                )
            remaining = reserved_act - now
            reason = (
                "Bangumi API 429 冷却等待"
                if self._frozen_until > now
                else "Bangumi API 限速等待"
            )
            return remaining, reserved_act, reason, extra_debt

    def _refill(self, now: float) -> None:
        """按经过时间补充令牌（上限为 burst）"""
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
        self._last_refill = now

    def _log_wait(self, seconds: float, reason: str) -> None:
        if seconds > _WARN_WAIT_THRESHOLD:
            logger.warning(f"⏳ {reason}: 等待 {seconds:.2f} 秒后继续")

    def _wait(self, seconds: float, reason: str) -> None:
        if seconds <= 0:
            return
        self._log_wait(seconds, reason)
        self._sleep(seconds)

    # ------------------------------------------------------------------
    # 429 自适应冷却
    # ------------------------------------------------------------------

    def notify_rate_limited(self, retry_after: float | None = None) -> float:
        """收到 429 时冻结令牌桶

        Args:
            retry_after: 服务端 ``Retry-After`` 解析出的秒数；None 表示无头或解析失败。

        Returns:
            本次生效的冷却秒数。
        """
        with self._lock:
            now = self._clock()
            self._consecutive_429 += 1
            # 指数升级：60 → 120 → 240 …（上限 3600）
            backoff = min(
                DEFAULT_COOLDOWN * (2 ** (self._consecutive_429 - 1)), MAX_COOLDOWN
            )

            hint = 0.0
            if retry_after is not None:
                try:
                    hint = max(0.0, float(retry_after))
                except (TypeError, ValueError):
                    logger.warning(
                        f"⏳ 无法解析 Retry-After={retry_after!r}，使用默认冷却"
                    )

            # 冻结不低于默认冷却；服务端给的更长时尊重服务端
            cooldown = min(max(backoff, hint), MAX_COOLDOWN)
            self._frozen_until = max(self._frozen_until, now + cooldown)
            logger.warning(
                f"🚦 Bangumi API 返回 429，令牌桶冷却 {cooldown:.0f}s"
                f"（连续第 {self._consecutive_429} 次）"
            )
            # 注：acquire/acquire_async 采用"锁内预约、锁外休眠 + 醒来复查"，
            # 等待者无需唤醒即会按这里更新的 _frozen_until 重新计算放行时刻，
            # 并把延长部分折算为令牌债务，保证冻结解除后仍按 FIFO 排队。
            return cooldown

    def notify_success(self) -> None:
        """请求成功时重置 429 指数升级计数"""
        with self._lock:
            self._consecutive_429 = 0


# ----------------------------------------------------------------------
# 进程级单例
# ----------------------------------------------------------------------

_limiter: RateLimiter | None = None
_limiter_lock = threading.Lock()


def _read_positive_float(raw: object, default: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(f"⏳ 非法 api_rate_limit={raw!r}，回退默认 {default}/s")
        return default
    if value <= 0:
        logger.warning(f"⏳ 非法 api_rate_limit={raw!r}，回退默认 {default}/s")
        return default
    return value


def _clamp_configurable_rate(rate: float) -> float:
    """把配置读取到的速率截断到 :data:`MAX_CONFIGURABLE_RATE`

    这是**配置层**的防御，不改变 ``RateLimiter.__init__`` 的语义（后者仍允许
    任意正值，便于测试注入高速率实例）。超限时记 warning 并说明被截断的值。
    """
    if rate > MAX_CONFIGURABLE_RATE:
        logger.warning(
            f"⏳ api_rate_limit={rate}/s 超过配置上界 "
            f"{MAX_CONFIGURABLE_RATE}/s，已截断为 {MAX_CONFIGURABLE_RATE}/s"
        )
        return MAX_CONFIGURABLE_RATE
    return rate


def _read_positive_int(raw: object, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(f"⏳ 非法 api_rate_burst={raw!r}，回退默认 {default}")
        return default
    if value < 1:
        logger.warning(f"⏳ 非法 api_rate_burst={raw!r}，回退默认 {default}")
        return default
    return value


def get_bgm_rate_limiter() -> RateLimiter:
    """获取进程级令牌桶单例（首次调用时从配置惰性构建）"""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                rate = _clamp_configurable_rate(
                    _read_positive_float(
                        config_manager.get("bangumi", "api_rate_limit", fallback=1),
                        DEFAULT_RATE,
                    )
                )
                burst = _read_positive_int(
                    config_manager.get("bangumi", "api_rate_burst", fallback=3),
                    DEFAULT_BURST,
                )
                _limiter = RateLimiter(rate=rate, burst=burst)
                logger.info(f"🚦 Bangumi API 限速器已初始化：{rate}/s，burst={burst}")
    return _limiter


def reset_bgm_rate_limiter(limiter: RateLimiter | None = None) -> None:
    """替换/清空进程级令牌桶单例

    供测试注入假时钟实例或"测试加速"使用；传 None 表示清空，下次调用重新加载配置。
    """
    global _limiter
    with _limiter_lock:
        _limiter = limiter
