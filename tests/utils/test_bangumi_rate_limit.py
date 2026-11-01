"""进程级 Bangumi API 令牌桶限速测试

覆盖 BDD 场景：
1. 超出速率时等待（注入时钟/sleep，不真实等待）
2. 多实例共享同一令牌桶
3. 直连回退路径同样限速
4. 429 自适应冷却（Retry-After / 默认 / 指数延长 / 成功重置）
5. 配置解析（生效值与非法/缺失回退）
6. 预扣（reserve）公平模式：FIFO 单调等待、冻结解除后按 1/rate 间隔放行
7. 获取超时：超过 timeout 放弃预约（回滚）并抛 RateLimitTimeoutError
8. async 兼容层 acquire_async：不阻塞事件循环、与同步共享令牌桶
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core.config import config_manager
from app.utils.bangumi_api import BangumiApi, rate_limit
from app.utils.bangumi_api.http_layer import _parse_retry_after
from app.utils.bangumi_api.rate_limit import (
    DEFAULT_ACQUIRE_TIMEOUT,
    DEFAULT_BURST,
    DEFAULT_RATE,
    MAX_CONFIGURABLE_RATE,
    RateLimiter,
    RateLimitTimeoutError,
    get_bgm_rate_limiter,
    reset_bgm_rate_limiter,
)
from app.utils.retry import RETRY_EXCEPTIONS

# 冻结类用例会预约 60~3600s 的等待，显式给出足够大的 timeout，
# 与「超时」场景（小 timeout）区分开。
_FREEZE_TIMEOUT = 4000.0


class FakeClock:
    """可注入时钟：单调递增的秒计数"""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSleeper:
    """可注入 sleep：记录等待并推进注入时钟（不真实等待）"""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(seconds)


class _AbortReservation(Exception):
    """测试用：在 sleep 时中断，仅采集预约的放行等待，不真正推进时钟"""


class AbortOnSleep:
    """记录每次预约所需的等待并立刻中断，便于单线程观察排队时序

    预约（reserve）在进入 ``sleep`` 前已提交令牌扣减，因此中断后令牌债务
    保留，后续 ``acquire`` 会看到连续排队的效果（等价于并发到达）。
    """

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        raise _AbortReservation


class RecordingLimiter:
    """记录调用的假限速器，用于验证 http_layer 的挂载点"""

    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.notified: list[float | None] = []
        self.acquire_calls = 0
        self.success_calls = 0

    def acquire(self) -> None:
        self.acquire_calls += 1
        self.events.append("acquire")

    def notify_rate_limited(self, retry_after: float | None = None) -> None:
        self.notified.append(retry_after)

    def notify_success(self) -> None:
        self.success_calls += 1


def _install_limiter(limiter: RateLimiter | RecordingLimiter) -> None:
    """RecordingLimiter 等鸭子类型测试替身一并兼容，cast 以满足签名。"""
    reset_bgm_rate_limiter(cast(RateLimiter, limiter))


def _mock_response(status_code: int = 200, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers if headers is not None else {}
    resp.text = ""
    resp.json.return_value = {}
    resp.elapsed.total_seconds.return_value = 0.01
    resp.request = MagicMock()
    return resp


@pytest.fixture(autouse=True)
def _reset_limiter_after_test():
    """每个用例结束后恢复惰性单例，避免用例间相互污染"""
    yield
    reset_bgm_rate_limiter(None)


# ---------------------------------------------------------------------------
# 场景 1：超出速率时等待
# ---------------------------------------------------------------------------


def test_acquire_within_burst_does_not_wait():
    """burst 内连续请求无需等待"""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = RateLimiter(rate=1.0, burst=3, clock=clock, sleep=sleeper)

    for _ in range(3):
        limiter.acquire()

    assert sleeper.calls == []


def test_acquire_waits_when_tokens_exhausted():
    """速率=1/s、burst=3，令牌耗尽后第 4 个请求等待 1 秒"""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = RateLimiter(rate=1.0, burst=3, clock=clock, sleep=sleeper)
    for _ in range(3):
        limiter.acquire()

    limiter.acquire()

    assert len(sleeper.calls) == 1
    assert sleeper.calls[0] == pytest.approx(1.0)


def test_acquire_is_thread_safe_under_concurrency():
    """并发调用不丢令牌、不死锁"""
    limiter = RateLimiter(rate=10000.0, burst=100)
    results: list[bool] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(5):
                limiter.acquire()
            results.append(True)
        except Exception as e:  # noqa: BLE001  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    assert len(results) == 8


def test_acquire_waits_without_holding_lock():
    """等待期间不持锁休眠：另一线程能在 sleep 进行中立即 notify

    若 ``acquire`` 在锁内休眠，notify 会一直阻塞到等待结束（此处 5s 超时），
    断言随即失败，可稳定暴露"持锁休眠"问题。
    """
    clock = FakeClock()
    entered = threading.Event()
    release = threading.Event()

    def slow_sleep(seconds: float) -> None:
        entered.set()
        release.wait(timeout=5)
        clock.advance(seconds)

    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=slow_sleep)
    limiter.acquire()  # 耗尽唯一令牌

    worker = threading.Thread(target=lambda: limiter.acquire(), daemon=True)
    worker.start()
    assert entered.wait(timeout=5), "acquire 未进入等待"

    notified = threading.Event()

    def notifier() -> None:
        limiter.notify_rate_limited(0.0)
        limiter.notify_success()
        notified.set()

    notifier_thread = threading.Thread(target=notifier, daemon=True)
    notifier_thread.start()
    assert notified.wait(timeout=2.0), (
        "notify 在 acquire 等待期间被阻塞（疑似持锁休眠）"
    )

    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()


def test_freeze_update_visible_during_wait():
    """等待期间 notify_rate_limited 生效：等待者按新冻结时间重新计算"""
    clock = FakeClock()
    entered = threading.Event()
    release = threading.Event()
    waits: list[float] = []

    def controlled_sleep(seconds: float) -> None:
        waits.append(seconds)
        entered.set()
        release.wait(timeout=5)
        clock.advance(seconds)

    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=controlled_sleep)
    limiter.acquire()  # 耗尽唯一令牌

    total: list[float] = []
    # 给足总等待预算（300s 冻结 + 首轮 1s）；timeout 语义 = 总等待上限
    worker = threading.Thread(
        target=lambda: total.append(limiter.acquire(timeout=400.0)), daemon=True
    )
    worker.start()
    assert entered.wait(timeout=5), "acquire 未进入等待"

    # 等待期间下发更长的 429 冻结（300s > 首轮 1s 的补令牌等待）
    notified = threading.Event()

    def notifier() -> None:
        limiter.notify_rate_limited(300.0)
        notified.set()

    notifier_thread = threading.Thread(target=notifier, daemon=True)
    notifier_thread.start()
    assert notified.wait(timeout=2.0), "notify 在 acquire 等待期间被阻塞"

    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    # 首轮按旧状态等 1s（补令牌），复查后按新冻结续等 299s，累计 300s
    assert waits[0] == pytest.approx(1.0)
    assert waits[1] == pytest.approx(299.0)
    assert total == [pytest.approx(300.0)]


# ---------------------------------------------------------------------------
# 场景 6：预扣（reserve）公平模式 —— FIFO 排队 + 冻结后间隔放行
# ---------------------------------------------------------------------------


def test_default_acquire_timeout_is_30_seconds():
    """默认 timeout 常量为 30s"""
    assert DEFAULT_ACQUIRE_TIMEOUT == pytest.approx(30.0)


def test_acquire_reservations_are_fifo_with_monotonic_wait():
    """连续预约的放行时刻按到达顺序单调递增，相邻间隔 1/rate（先到先得）"""
    clock = FakeClock()
    aborter = AbortOnSleep()
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=aborter)

    # 第一个请求消耗唯一令牌，立即放行
    assert limiter.acquire(timeout=_FREEZE_TIMEOUT) == pytest.approx(0.0)

    # 随后三个请求依次排队：1s、2s、3s，严格单调不减
    for _ in range(3):
        with pytest.raises(_AbortReservation):
            limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert aborter.calls == [
        pytest.approx(1.0),
        pytest.approx(2.0),
        pytest.approx(3.0),
    ]
    intervals = [b - a for a, b in zip(aborter.calls, aborter.calls[1:], strict=False)]
    assert intervals == [pytest.approx(1.0), pytest.approx(1.0)]


def test_freeze_thaw_preserves_release_spacing():
    """冻结期间排队的请求，解除后按 1/rate 间隔依次放行（不超发）"""
    clock = FakeClock()
    aborter = AbortOnSleep()
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=aborter)

    limiter.notify_rate_limited(60.0)  # t=1000 冻结至 1060

    for _ in range(3):
        with pytest.raises(_AbortReservation):
            limiter.acquire(timeout=_FREEZE_TIMEOUT)

    # 放行时刻为 1060、1061、1062：等待 60、61、62，间隔恢复为 1/rate
    assert aborter.calls == [
        pytest.approx(60.0),
        pytest.approx(61.0),
        pytest.approx(62.0),
    ]


def test_acquire_default_timeout_gives_up_when_frozen_longer_than_timeout():
    """冻结时长超过默认 timeout 时，acquire 放弃预约并抛错（不静默阻塞）"""
    limiter, sleeper = _make_limiter()  # rate=1/s、burst=3
    limiter.notify_rate_limited(60.0)  # 冻结 60s > 默认 30s

    with pytest.raises(RateLimitTimeoutError):
        limiter.acquire()

    assert sleeper.calls == []


# ---------------------------------------------------------------------------
# 场景 7：获取超时 —— 放弃预约并回滚
# ---------------------------------------------------------------------------


def test_acquire_timeout_raises_and_rolls_back_reservation():
    """预约等待超过 timeout 时抛 RateLimitTimeoutError，且归还透支令牌"""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=sleeper)

    limiter.acquire()  # 耗尽唯一令牌

    with pytest.raises(RateLimitTimeoutError):
        limiter.acquire(timeout=0.5)  # 需排队 1.0s > 0.5s

    # 未产生真实等待，且未扣减令牌
    assert sleeper.calls == []

    # 若预约未回滚，这里会因透支再等 1s；回滚后补满令牌即可立即获取
    clock.advance(1.0)
    assert limiter.acquire() == pytest.approx(0.0)
    assert sleeper.calls == []


def test_acquire_timeout_rollback_does_not_disturb_earlier_waiter():
    """超时回滚不改变已排队等待者的放行时刻（不影响他人预约）"""
    clock = FakeClock()
    aborter = AbortOnSleep()
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=aborter)

    limiter.acquire()  # 耗尽唯一令牌

    # 先到者预约 1s 后放行
    with pytest.raises(_AbortReservation):
        limiter.acquire(timeout=_FREEZE_TIMEOUT)
    assert aborter.calls == [pytest.approx(1.0)]

    # 后到者 timeout 过小，放弃预约
    with pytest.raises(RateLimitTimeoutError):
        limiter.acquire(timeout=0.5)

    # 先到者的令牌债务保留：下一个请求仍在 1s 之后（而非被超时者挤回）
    with pytest.raises(_AbortReservation):
        limiter.acquire(timeout=_FREEZE_TIMEOUT)
    assert aborter.calls == [pytest.approx(1.0), pytest.approx(2.0)]


def test_rate_limit_timeout_error_is_not_retryable():
    """RateLimitTimeoutError 不属于 RETRY_EXCEPTIONS，不会被当作可重试异常"""
    assert not issubclass(RateLimitTimeoutError, RETRY_EXCEPTIONS)
    assert not issubclass(RateLimitTimeoutError, httpx.HTTPError)


# ---------------------------------------------------------------------------
# 场景 8：async 兼容层 acquire_async
# ---------------------------------------------------------------------------


async def test_acquire_async_returns_immediately_when_tokens_available():
    """令牌充足时 acquire_async 立即返回 0，不进入任何等待"""
    clock = FakeClock()
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock)

    assert await limiter.acquire_async() == pytest.approx(0.0)


async def test_acquire_async_does_not_block_event_loop():
    """等待期间事件循环不被阻塞：并发心跳任务持续推进"""
    limiter = RateLimiter(rate=20.0, burst=1)  # 真实时钟，第二次约等 0.05s
    await limiter.acquire_async()

    heartbeats = 0

    async def heartbeat() -> None:
        nonlocal heartbeats
        while True:
            heartbeats += 1
            await asyncio.sleep(0.001)

    task = asyncio.create_task(heartbeat())
    try:
        await limiter.acquire_async()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert heartbeats >= 2


async def test_acquire_async_timeout_raises_and_rolls_back():
    """acquire_async 超时抛 RateLimitTimeoutError，并回滚令牌预约"""
    clock = FakeClock()
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock)

    limiter.acquire()  # 同步耗尽唯一令牌

    with pytest.raises(RateLimitTimeoutError):
        await limiter.acquire_async(timeout=0.5)

    clock.advance(1.0)
    assert await limiter.acquire_async() == pytest.approx(0.0)


async def test_sync_and_async_share_same_bucket_state():
    """同一实例上同步/异步混用共享令牌桶：互相看得到对方的占用与债务"""
    # 同步占用 → 异步感知
    clock = FakeClock()
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock)
    limiter.acquire()
    with pytest.raises(RateLimitTimeoutError):
        await limiter.acquire_async(timeout=0.5)

    # 异步占用 → 同步感知
    clock2 = FakeClock()
    limiter2 = RateLimiter(rate=1.0, burst=1, clock=clock2)
    assert await limiter2.acquire_async() == pytest.approx(0.0)
    with pytest.raises(RateLimitTimeoutError):
        limiter2.acquire(timeout=0.5)


# ---------------------------------------------------------------------------
# 场景 8（取消回滚）：acquire_async 在等待期间被取消时归还预约扣减
#
# 预约（reserve）在锁内一次性扣减令牌（并可能折算冻结债务），真正使用令牌
# 发生在 ``await`` 返回之后。若等待期间任务被 cancel，扣减必须冲销，否则
# 令牌桶被永久拖累（透支再也不恢复）。
# ---------------------------------------------------------------------------


async def test_acquire_async_cancel_rolls_back_reserved_token():
    """等待期间被取消：已预约的令牌在锁内归还，不残留透支"""
    limiter = RateLimiter(rate=1.0, burst=1)

    await limiter.acquire_async()  # 耗尽唯一令牌
    with limiter._lock:
        before = limiter._tokens

    task = asyncio.create_task(limiter.acquire_async(timeout=30.0))
    await asyncio.sleep(0.05)  # 让任务进入 asyncio.sleep 等待
    assert not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with limiter._lock:
        after = limiter._tokens
    # 自然补充约 0.05s 令牌；若未回滚则残留约 -1.0 的透支
    assert after == pytest.approx(0.05, abs=0.05)
    assert after > before


async def test_acquire_async_cancel_rolls_back_freeze_debt():
    """预约已折算 429 冻结债务时，取消也要一并冲销该债务"""
    limiter = RateLimiter(rate=1.0, burst=1)

    await limiter.acquire_async()  # 耗尽唯一令牌
    limiter.notify_rate_limited(0.0)  # 冻结 60s，预约会折算约 60 令牌债务

    task = asyncio.create_task(limiter.acquire_async(timeout=4000.0))
    await asyncio.sleep(0.05)
    assert not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with limiter._lock:
        after = limiter._tokens
    # 冻结债务必须随预约一起冲销；未回滚则残留约 -60
    assert after == pytest.approx(0.05, abs=0.1)


async def test_acquire_async_cancel_rolls_back_wait_loop_freeze_extension_debt():
    """等待循环中 429 冻结延长追加的债务，取消时也要冲销"""
    limiter = RateLimiter(rate=100.0, burst=1)

    await limiter.acquire_async()  # 耗尽唯一令牌

    task = asyncio.create_task(limiter.acquire_async(timeout=4000.0))
    await asyncio.sleep(0.005)  # 首轮预约约 0.01s
    limiter.notify_rate_limited(0.0)  # 冻结 60s，下一轮 _remaining_wait 折算债务
    await asyncio.sleep(0.05)  # 等首轮结束、进入冻结等待轮
    assert not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with limiter._lock:
        after = limiter._tokens
    # 桶容量即上限：回滚不得把令牌推过 burst
    assert after <= limiter.burst + 1e-9
    # 回滚有效：全部债务（含循环追加）被冲销，未残留透支
    assert after >= 0.0


async def test_acquire_async_cancel_clamps_tokens_to_burst_after_freeze():
    """复现 edge：rate=100/burst=1，令牌占满后等待期间收到 429 冻结并取消

    首轮预约把桶扣减为负；等待循环里的 ``_refill`` 会在负桶上补令牌，取消时
    直接把净债务加回会把 ``_tokens`` 推过桶容量（实测约 1.09）。桶容量即上限，
    回滚必须夹紧到 ``burst``，且后续 acquire 不被永久拖累、不超发。
    """
    limiter = RateLimiter(rate=100.0, burst=1)

    await limiter.acquire_async()  # 耗尽唯一令牌
    task = asyncio.create_task(limiter.acquire_async(timeout=4000.0))
    await asyncio.sleep(0.005)  # 首轮预约约 0.01s
    limiter.notify_rate_limited(0.0)  # 冻结 60s，下一轮折算债务
    await asyncio.sleep(0.05)  # 等首轮结束、进入冻结等待轮
    assert not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with limiter._lock:
        after = limiter._tokens
    assert after <= limiter.burst + 1e-9  # 桶容量即上限，不得超发
    assert after >= 0.0  # 回滚有效：未残留透支（不被永久拖累）

    # 冻结仍在内存中（60s），测试直接清除以便验证后续行为
    with limiter._lock:
        limiter._frozen_until = 0.0

    # 不被永久拖累：令牌可用，首个请求立即放行
    assert await limiter.acquire_async(timeout=0.001) == pytest.approx(0.0)
    # 不超发：容量仅 1，紧随其后的请求必须按 1/rate 排队（约 0.01s > timeout）
    with pytest.raises(RateLimitTimeoutError):
        await limiter.acquire_async(timeout=0.001)


def test_remaining_wait_logs_freeze_extension_debt():
    """等待期间冻结延长时，记录 debug 日志（延长秒数与折算债务）"""
    clock = FakeClock()
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock)

    limiter.notify_rate_limited(60.0)  # t=1000 冻结至 1060
    reserved_act, _ = limiter._reserve(timeout=4000.0)

    limiter.notify_rate_limited(120.0)  # 第二次冻结延长至 1120
    with patch("app.utils.bangumi_api.rate_limit.logger") as mock_logger:
        remaining, new_act, _reason, extra_debt = limiter._remaining_wait(reserved_act)

    assert new_act == pytest.approx(1120.0)
    assert extra_debt == pytest.approx(60.0)
    assert remaining == pytest.approx(120.0)
    mock_logger.debug.assert_called()
    msgs = [str(c.args[0]) for c in mock_logger.debug.call_args_list if c.args]
    assert any("60" in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# 场景 2：多实例共享同一令牌桶
# ---------------------------------------------------------------------------


def test_get_bgm_rate_limiter_returns_same_instance():
    reset_bgm_rate_limiter(None)

    assert get_bgm_rate_limiter() is get_bgm_rate_limiter()


def test_two_bangumi_api_instances_share_same_bucket():
    """两个 BangumiApi 实例共用同一令牌桶：burst=1 时第二个实例必须等待"""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    shared = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=sleeper)
    reset_bgm_rate_limiter(shared)

    mock_resp = _mock_response(200)
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.request.return_value = mock_resp
        api1 = BangumiApi(access_token="t")
        api2 = BangumiApi(access_token="t")

        api1.get("me")
        assert sleeper.calls == []

        api2.get("me")

    assert len(sleeper.calls) == 1
    assert sleeper.calls[0] == pytest.approx(1.0)


def test_req_not_auth_session_shares_same_bucket():
    """同一实例的 self.req 与 _req_not_auth 共用同一令牌桶"""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    shared = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=sleeper)
    reset_bgm_rate_limiter(shared)

    mock_resp = _mock_response(200)
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.request.return_value = mock_resp
        api = BangumiApi(access_token="t")

        api.get("me")  # 经 self.req 消耗唯一令牌
        assert sleeper.calls == []

        # _req_not_auth 链式返回基类 HttpClientBase，实际为 SyncHttpClient；cast 以传入
        api._request_with_retry(
            "GET", cast(Any, api._req_not_auth), "https://api.bgm.tv/v0/me"
        )

    assert len(sleeper.calls) == 1
    assert sleeper.calls[0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 场景 3：直连回退路径同样限速
# ---------------------------------------------------------------------------


def test_direct_connection_waits_when_tokens_exhausted():
    """直连路径同样先 acquire 令牌"""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=sleeper)
    _install_limiter(limiter)
    limiter.acquire()  # 耗尽唯一令牌

    api = BangumiApi(access_token="t")
    mock_resp = _mock_response(200)
    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.request.return_value = mock_resp
        result = api._try_direct_connection("GET", "https://api.bgm.tv/v0/me")

    assert result == mock_resp
    assert len(sleeper.calls) == 1
    assert sleeper.calls[0] == pytest.approx(1.0)


def test_direct_connection_acquires_before_sending_request():
    """acquire 必须发生在实际发送直连请求之前"""
    events: list[str] = []
    limiter = RecordingLimiter(events)
    _install_limiter(limiter)

    api = BangumiApi(access_token="t")
    mock_resp = _mock_response(200)

    def _do_request(*args, **kwargs):
        events.append("request")
        return mock_resp

    with patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.request.side_effect = _do_request
        api._try_direct_connection("GET", "https://api.bgm.tv/v0/me")

    assert events == ["acquire", "request"]


def test_request_with_retry_acquires_before_sending_request():
    """主路径 acquire 必须发生在 session.request 之前"""
    events: list[str] = []
    limiter = RecordingLimiter(events)
    _install_limiter(limiter)

    api = BangumiApi(access_token="t")
    mock_resp = _mock_response(200)
    mock_session = MagicMock()

    def _do_request(*args, **kwargs):
        events.append("request")
        return mock_resp

    mock_session.request.side_effect = _do_request
    api._request_with_retry("GET", mock_session, "https://api.bgm.tv/v0/me")

    assert events == ["acquire", "request"]


def test_direct_connection_error_warning_includes_status_code():
    """直连回退遇 >=400 时 warning 带 status_code，便于排查"""
    limiter = RecordingLimiter()
    _install_limiter(limiter)

    api = BangumiApi(access_token="t")
    mock_resp = _mock_response(503)

    with (
        patch("app.utils.bangumi_api.httpx.Client") as mock_client_cls,
        patch("app.utils.bangumi_api.http_layer.logger") as mock_http_logger,
    ):
        mock_client_cls.return_value.request.return_value = mock_resp
        result = api._try_direct_connection("GET", "https://api.bgm.tv/v0/me")

    assert result is None
    warnings = [
        str(c.args[0]) for c in mock_http_logger.warning.call_args_list if c.args
    ]
    assert any("503" in msg for msg in warnings), warnings


# ---------------------------------------------------------------------------
# 场景 4：429 自适应冷却
# ---------------------------------------------------------------------------


def _make_limiter():
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = RateLimiter(rate=1.0, burst=3, clock=clock, sleep=sleeper)
    return limiter, sleeper


def test_notify_rate_limited_with_retry_after_freezes_bucket():
    """收到 Retry-After: 60，令牌桶冻结至少 60 秒"""
    limiter, sleeper = _make_limiter()

    limiter.notify_rate_limited(60.0)
    limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert sleeper.calls == [pytest.approx(60.0)]


def test_notify_rate_limited_prefers_retry_after_over_default():
    """有 Retry-After 时优先使用服务端时间，不被默认 60 秒覆盖"""
    limiter, sleeper = _make_limiter()

    limiter.notify_rate_limited(5.0)
    limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert sleeper.calls == [pytest.approx(5.0)]


def test_consecutive_retry_after_does_not_escalate():
    """连续 429 且都带 Retry-After 时，每次都以服务端时间为准"""
    limiter, _ = _make_limiter()

    assert limiter.notify_rate_limited(5.0) == pytest.approx(5.0)
    assert limiter.notify_rate_limited(5.0) == pytest.approx(5.0)


def test_notify_rate_limited_without_retry_after_defaults_to_60():
    """无 Retry-After 头时默认冻结 60 秒"""
    limiter, sleeper = _make_limiter()

    limiter.notify_rate_limited(None)
    limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert sleeper.calls == [pytest.approx(60.0)]


def test_consecutive_rate_limits_escalate_exponentially():
    """连续命中 429 时按 60→120→240 指数延长"""
    limiter, sleeper = _make_limiter()

    for _ in range(3):
        limiter.notify_rate_limited(None)
        limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert sleeper.calls == [
        pytest.approx(60.0),
        pytest.approx(120.0),
        pytest.approx(240.0),
    ]


def test_consecutive_rate_limits_capped_at_3600():
    """连续命中 429 的冷却时长上限为 3600 秒"""
    limiter, sleeper = _make_limiter()

    for _ in range(8):
        limiter.notify_rate_limited(None)
        limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert max(sleeper.calls) == pytest.approx(3600.0)
    assert all(w <= 3600.0 for w in sleeper.calls)


def test_notify_success_resets_escalation():
    """成功请求后重置指数升级，再次 429 从 60 秒重新开始"""
    limiter, sleeper = _make_limiter()

    limiter.notify_rate_limited(None)
    limiter.acquire(timeout=_FREEZE_TIMEOUT)
    limiter.notify_success()

    limiter.notify_rate_limited(None)
    limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert sleeper.calls == [pytest.approx(60.0), pytest.approx(60.0)]


def test_rate_limit_hint_capped_at_max_cooldown():
    """Retry-After 超过上限时冻结封顶 3600s（不会无限冻结）"""
    limiter, sleeper = _make_limiter()

    limiter.notify_rate_limited(100000)
    limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert sleeper.calls == [pytest.approx(3600.0)]


def test_notify_success_not_called_for_5xx():
    """500 响应不重置 429 升级计数：429→500→429 的冻结应为 120s 而非 60s"""
    limiter, sleeper = _make_limiter()
    _install_limiter(limiter)
    api = BangumiApi(access_token="t")

    api._apply_rate_limit_notification(_mock_response(429, {}))
    limiter.acquire(timeout=_FREEZE_TIMEOUT)

    api._apply_rate_limit_notification(_mock_response(500, {}))
    api._apply_rate_limit_notification(_mock_response(429, {}))

    limiter.acquire(timeout=_FREEZE_TIMEOUT)

    assert sleeper.calls == [pytest.approx(60.0), pytest.approx(120.0)]


# ---------------------------------------------------------------------------
# 场景 4（http_layer 挂载）：429 通知与 Retry-After 解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("60", 60.0),
        ("  120  ", 120.0),
        ("0", 0.0),
    ],
)
def test_parse_retry_after_seconds(header, expected):
    res = _mock_response(429, {"Retry-After": header})

    assert _parse_retry_after(res) == pytest.approx(expected)


def test_parse_retry_after_http_date():
    res = _mock_response(
        429,
        {
            "Retry-After": format_datetime(
                datetime.now(timezone.utc) + timedelta(seconds=90), usegmt=True
            )
        },
    )

    parsed = _parse_retry_after(res)

    assert parsed is not None
    assert 80.0 <= parsed <= 91.0


def test_parse_retry_after_http_date_logs_seconds_fallback_warning():
    """Retry-After 为 HTTP-date 时，纯秒数解析失败分支须记录 warning 并继续解析 date。"""
    raw = "Wed, 21 Oct 2015 07:28:00 GMT"
    res = _mock_response(429, {"Retry-After": raw})

    with patch("app.utils.bangumi_api.http_layer.logger") as mock_logger:
        parsed = _parse_retry_after(res)

    # date 分支解析成功（过去时间 → 0.0），证明降级后继续解析
    assert parsed == 0.0
    mock_logger.warning.assert_called_once()
    msg = str(mock_logger.warning.call_args.args[0])
    assert raw in msg
    mock_logger.error.assert_not_called()


def test_parse_retry_after_invalid_returns_none():
    res = _mock_response(429, {"Retry-After": "not-a-valid-value"})

    assert _parse_retry_after(res) is None


def test_parse_retry_after_absent_returns_none():
    res = _mock_response(429, {})

    assert _parse_retry_after(res) is None


def test_request_with_retry_notifies_limiter_on_429():
    """主路径收到 429 时把 Retry-After 交给令牌桶"""
    limiter = RecordingLimiter()
    _install_limiter(limiter)

    api = BangumiApi(access_token="t")
    mock_session = MagicMock()
    mock_session._max_retries = 3
    mock_session.request.return_value = _mock_response(429, {"Retry-After": "60"})

    with (
        patch("app.services.notification_service.notification_service"),
        pytest.raises(httpx.HTTPStatusError),
    ):
        api._request_with_retry("GET", mock_session, "https://api.bgm.tv/v0/me")

    assert limiter.notified == [pytest.approx(60.0)]


def test_request_with_retry_notifies_limiter_on_429_without_header():
    """无 Retry-After 头时传 None，由令牌桶走默认冷却"""
    limiter = RecordingLimiter()
    _install_limiter(limiter)

    api = BangumiApi(access_token="t")
    mock_session = MagicMock()
    mock_session._max_retries = 3
    mock_session.request.return_value = _mock_response(429, {})

    with (
        patch("app.services.notification_service.notification_service"),
        pytest.raises(httpx.HTTPStatusError),
    ):
        api._request_with_retry("GET", mock_session, "https://api.bgm.tv/v0/me")

    assert limiter.notified == [None]


def test_request_with_retry_notifies_success_on_ok():
    """非 429 响应通知令牌桶重置升级计数"""
    limiter = RecordingLimiter()
    _install_limiter(limiter)

    api = BangumiApi(access_token="t")
    mock_session = MagicMock()
    mock_session.request.return_value = _mock_response(200)

    api._request_with_retry("GET", mock_session, "https://api.bgm.tv/v0/me")

    assert limiter.success_calls == 1
    assert limiter.notified == []


# ---------------------------------------------------------------------------
# 场景 5：配置解析
# ---------------------------------------------------------------------------


def _patch_config(monkeypatch, values: dict) -> None:
    def fake_get(section, key, fallback=None):
        assert section == "bangumi"
        return values.get(key, fallback)

    monkeypatch.setattr(config_manager, "get", fake_get)


def test_config_values_applied(monkeypatch):
    """[bangumi] api_rate_limit=2、api_rate_burst=5 时按 2/s、burst=5 生效"""
    reset_bgm_rate_limiter(None)
    _patch_config(monkeypatch, {"api_rate_limit": "2", "api_rate_burst": "5"})

    limiter = get_bgm_rate_limiter()

    assert limiter.rate == pytest.approx(2.0)
    assert limiter.burst == 5


def test_config_rate_above_upper_bound_is_clamped(monkeypatch):
    """api_rate_limit 超过上界时截断到 MAX_CONFIGURABLE_RATE，避免极端 rate 债务"""
    reset_bgm_rate_limiter(None)
    _patch_config(monkeypatch, {"api_rate_limit": "1000000", "api_rate_burst": "5"})

    limiter = get_bgm_rate_limiter()

    assert limiter.rate == pytest.approx(MAX_CONFIGURABLE_RATE)
    assert limiter.burst == 5


def test_config_rate_at_upper_bound_is_kept(monkeypatch):
    """恰好等于上界的 api_rate_limit 原样生效（边界不误伤）"""
    reset_bgm_rate_limiter(None)
    _patch_config(monkeypatch, {"api_rate_limit": str(MAX_CONFIGURABLE_RATE)})

    limiter = get_bgm_rate_limiter()

    assert limiter.rate == pytest.approx(MAX_CONFIGURABLE_RATE)


def test_config_rate_clamp_logs_warning_with_truncated_value(monkeypatch):
    """截断超限 rate 时记 warning，并说明被截断的原始值"""
    reset_bgm_rate_limiter(None)
    _patch_config(monkeypatch, {"api_rate_limit": "1000000"})

    with patch("app.utils.bangumi_api.rate_limit.logger") as mock_logger:
        get_bgm_rate_limiter()

    warnings = [str(c.args[0]) for c in mock_logger.warning.call_args_list if c.args]
    assert any("1000000" in msg for msg in warnings), warnings


@pytest.mark.parametrize(
    ("rate", "burst"),
    [
        ("abc", "xyz"),
        ("", ""),
        ("0", "0"),
        ("-1", "-3"),
        ("1", "-3"),
    ],
)
def test_invalid_config_falls_back_to_defaults(monkeypatch, rate, burst):
    """非法配置回退默认 1/s、burst=3"""
    reset_bgm_rate_limiter(None)
    _patch_config(monkeypatch, {"api_rate_limit": rate, "api_rate_burst": burst})

    limiter = get_bgm_rate_limiter()

    assert limiter.rate == pytest.approx(DEFAULT_RATE)
    assert limiter.burst == DEFAULT_BURST


def test_missing_config_falls_back_to_defaults(monkeypatch):
    """缺失配置回退默认 1/s、burst=3"""
    reset_bgm_rate_limiter(None)
    monkeypatch.setattr(
        config_manager, "get", lambda section, key, fallback=None: fallback
    )

    limiter = get_bgm_rate_limiter()

    assert limiter.rate == pytest.approx(DEFAULT_RATE)
    assert limiter.burst == DEFAULT_BURST


def test_reset_bgm_rate_limiter_installs_given_instance():
    """reset 接口可注入测试实例，供下游/测试加速使用"""
    custom = RateLimiter(rate=5.0, burst=7)

    reset_bgm_rate_limiter(custom)

    assert get_bgm_rate_limiter() is custom
    assert rate_limit.get_bgm_rate_limiter() is custom


# ---------------------------------------------------------------------------
# 场景 9（等待中冻结延长）：累计等待超过 timeout → 放弃预约并回滚
#
# ``_reserve`` 只校验"预约时刻"的等待；等待期间收到 429 冻结会把放行时刻
# 推后（``_remaining_wait`` 折算债务）。若不复查上限，调用方传给 ``timeout``
# 的"总等待预算"会被穿透。本组用例锁定：累计等待将超过 timeout → 回滚 + 抛。
# ---------------------------------------------------------------------------


class _FreezeOnceSleeper(FakeSleeper):
    """首段 sleep 时注入一次 429 冻结（模拟"等待中收到 429"）。"""

    def __init__(self, clock: FakeClock, retry_after: float = 0.0) -> None:
        super().__init__(clock)
        self._retry_after = retry_after
        self.limiter: RateLimiter | None = None  # 延迟绑定，避免构造环

    def __call__(self, seconds: float) -> None:
        if len(self.calls) == 0 and self.limiter is not None:
            self.limiter.notify_rate_limited(self._retry_after)
        super().__call__(seconds)


def test_acquire_aborts_when_wait_loop_freeze_extends_beyond_timeout():
    """等待中冻结延长使累计等待超 timeout → 抛 RateLimitTimeoutError 并回滚。"""
    clock = FakeClock()
    sleeper = _FreezeOnceSleeper(clock)
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=sleeper)
    sleeper.limiter = limiter

    limiter.acquire()  # 耗尽唯一令牌

    with pytest.raises(RateLimitTimeoutError):
        limiter.acquire(timeout=30.0)

    with limiter._lock:
        after = limiter._tokens
    # 回滚前：-1（预约） + 1（等待 1s 的自然补充） - 59（冻结折算） = -59；
    # 冲销预约扣减（1 + 59）后夹紧到桶容量（不回滚则残留约 -59）
    assert after == pytest.approx(1.0)


def test_acquire_waits_when_freeze_extension_stays_within_timeout():
    """冻结延长后累计等待仍在 timeout 内 → 正常等待放行（不误伤）。"""
    clock = FakeClock()
    sleeper = _FreezeOnceSleeper(clock)
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=sleeper)
    sleeper.limiter = limiter

    limiter.acquire()  # 耗尽唯一令牌

    # 冻结 60s；预约约 1s + 延长约 59s ≈ 60s ≤ timeout=100
    assert limiter.acquire(timeout=100.0) == pytest.approx(60.0)


def test_acquire_boundary_exact_timeout_still_passes():
    """边界：累计等待恰好等于 timeout → 放行（严格大于才放弃）。"""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock, sleep=sleeper)

    limiter.acquire()  # 耗尽唯一令牌

    assert limiter.acquire(timeout=1.0) == pytest.approx(1.0)


async def test_acquire_async_aborts_when_wait_loop_freeze_extends_beyond_timeout():
    """async 同构：等待中冻结延长超 timeout → 抛错并回滚（不留透支）。"""
    limiter = RateLimiter(rate=100.0, burst=1)

    await limiter.acquire_async()  # 耗尽唯一令牌

    task = asyncio.create_task(limiter.acquire_async(timeout=30.0))
    await asyncio.sleep(0.005)  # 首轮预约约 0.01s
    limiter.notify_rate_limited(0.0)  # 冻结 60s → 累计等待将超 30s

    # 实现前会继续睡满冻结（60s+）；用外层 wait_for 兜底避免测试挂起
    with pytest.raises(RateLimitTimeoutError):
        await asyncio.wait_for(task, timeout=1.0)

    with limiter._lock:
        after = limiter._tokens
    # 桶容量即上限；回滚不得残留透支（未回滚约 -6000）
    assert 0.0 <= after <= limiter.burst + 1e-9
