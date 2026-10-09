"""rate_limiter.py · 限流串行队列 + 缓存 + 算价预算（🟡RL-13）。

职责：
- 串行队列，最小间隔 `MIN_INTERVAL_MS`(默认 120ms)，约 500 次/分钟，远低于 600 次/分钟限流；
- 结果缓存：键 = 工具名 + 归一化入参 canonical JSON 的 sha256（幂等 + 防重复打接口，
  同一逻辑请求只打一次，既防限流又保证重复执行结果一致）；
- 算价调用预算上限（默认 12 次/会话），超预算即停止并触发 L4 降级；
- 仅对 429 与网络类瞬时错误重试（max=2, base=1.5s，指数退避）；**业务错误不重试**。

零第三方依赖。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Callable, Dict, Optional

MIN_INTERVAL_MS = 120
PRICE_BUDGET_DEFAULT = 12
MAX_RETRIES = 2
RETRY_BASE_S = 1.5
CALL_TIMEOUT_S = 15

# 可重试的错误码（限流 / 网络瞬时）；业务错误码不在此列，不重试
RETRYABLE_CODES = frozenset({429, "429", 500, "500", 502, "502", 503, "503", 504, "504"})

_LOCK = threading.Lock()
_last_call_ts = 0.0
_cache: Dict[str, Any] = {}
_budget = {"used": 0, "limit": PRICE_BUDGET_DEFAULT}


class BudgetExceeded(RuntimeError):
    """算价预算耗尽 → 触发 L4 降级（原价 + 提示）。"""


class CallError(RuntimeError):
    """工具调用失败（业务或重试耗尽）。携带错误码，供 envelope/报告归因。"""

    def __init__(self, message: str, code: Any = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def cache_key(tool: str, args: Dict[str, Any]) -> str:
    """工具 + 归一化入参 → 稳定缓存键（sha256，字段排序、无空白差异）。"""
    payload = json.dumps({"tool": tool, "args": args or {}},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_args(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if k not in ("tool",)}


def configure_budget(limit: int) -> None:
    with _LOCK:
        _budget["limit"] = int(limit)
        _budget["used"] = 0


def budget_left() -> int:
    """剩余算价预算次数。"""
    with _LOCK:
        return max(0, _budget["limit"] - _budget["used"])


def budget_used() -> int:
    with _LOCK:
        return _budget["used"]


def reset() -> None:
    """清空缓存与预算计数（测试/新会话用）。"""
    global _last_call_ts
    with _LOCK:
        _cache.clear()
        _budget["used"] = 0
        _last_call_ts = 0.0


def _extract_code(resp: Any) -> Any:
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return resp.get("code")
        code = resp.get("code")
        return code
    return None


def _is_transient_error(exc: Exception) -> bool:
    """仅 429 与网络类瞬时错误可重试；业务错误不重试。"""
    code = getattr(exc, "code", None)
    if code is not None and str(code) in {str(c) for c in RETRYABLE_CODES}:
        return True
    # 网络类异常（timeout / connection）按名称判定，避免引入第三方依赖
    name = type(exc).__name__.lower()
    return any(tok in name for tok in ("timeout", "connection", "network", "temporarily"))


def call(fn: Callable[..., Any], *, is_price_call: bool = False, tool: Optional[str] = None,
         **kwargs) -> Any:
    """串行 + 间隔 + 缓存 + （算价时）预算闸门；失败抛 `CallError`。

    - `tool` 未传时取 `fn.__name__` 作工具名；
    - `is_price_call=True` 时计入算价预算，超预算抛 `BudgetExceeded`（→ L4）。
    """
    global _last_call_ts
    tool_name = tool or getattr(fn, "__name__", "unknown")
    key = cache_key(tool_name, _normalize_args(kwargs))

    with _LOCK:
        if key in _cache:
            return _cache[key]
        if is_price_call:
            if _budget["used"] >= _budget["limit"]:
                raise BudgetExceeded(
                    f"算价预算耗尽（limit={_budget['limit']}）→ L4 降级到原价 + 明确提示"
                )
            _budget["used"] += 1
        # 串行节流：距上次调用不足 MIN_INTERVAL_MS 则补足
        now = time.monotonic()
        wait = MIN_INTERVAL_MS / 1000.0 - (now - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.monotonic()

    attempt = 0
    while True:
        try:
            resp = fn(**kwargs)
        except Exception as e:  # noqa: BLE001 —— 统一转 CallError，不静默
            if _is_transient_error(e) and attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_S * (2 ** attempt))
                attempt += 1
                continue
            raise CallError(f"{tool_name} 调用失败：{e}", code=getattr(e, "code", None),
                            retryable=_is_transient_error(e)) from e

        code = _extract_code(resp)
        if isinstance(resp, dict) and resp.get("success") is False:
            if str(code) in {str(c) for c in RETRYABLE_CODES} and attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_S * (2 ** attempt))
                attempt += 1
                continue
            raise CallError(f"{tool_name} 业务失败：code={code} msg={resp.get('message') or resp.get('msg')}",
                            code=code, retryable=False)
        break

    with _LOCK:
        _cache[key] = resp
    return resp


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("rate_limiter.py self-test")
    reset()
    calls = {"n": 0}

    def fake(**kw):
        calls["n"] += 1
        return {"success": True, "data": {"echo": kw.get("storeCode")}}

    r1 = call(fake, tool="query-meals", storeCode="1950591")
    r2 = call(fake, tool="query-meals", storeCode="1950591")
    check(r1 == r2 and calls["n"] == 1, "相同入参命中缓存（只打一次接口）")
    call(fake, tool="query-meals", storeCode="1950564")
    check(calls["n"] == 2, "不同入参未命中缓存")

    check(cache_key("t", {"a": 1, "b": 2}) == cache_key("t", {"b": 2, "a": 1}), "缓存键对入参顺序不敏感")

    # 预算闸门
    configure_budget(2)
    check(budget_left() == 2, "预算初始化 = 2")

    def price(**kw):
        return {"success": True, "data": {"price": 2800}}

    call(price, is_price_call=True, tool="calculate-price", items=[1])
    call(price, is_price_call=True, tool="calculate-price", items=[2])
    check(budget_left() == 0, "两次算价后预算耗尽")
    try:
        call(price, is_price_call=True, tool="calculate-price", items=[3])
        check(False, "超预算应抛 BudgetExceeded")
    except BudgetExceeded:
        check(True, "超预算抛 BudgetExceeded（→ L4）")

    # 业务错误不重试
    reset()
    attempts = {"n": 0}

    def biz_err(**kw):
        attempts["n"] += 1
        return {"success": False, "code": 600022, "message": "暂不支持多张券使用"}

    try:
        call(biz_err, tool="calculate-price", items=[1, 2])
        check(False, "业务错误应抛 CallError")
    except CallError as e:
        check(str(e.code) == "600022" and attempts["n"] == 1, "业务错误 600022 不重试（仅 1 次）")

    # 429 重试
    reset()
    att = {"n": 0}

    def flaky(**kw):
        att["n"] += 1
        if att["n"] < 2:
            return {"success": False, "code": 429, "message": "too many requests"}
        return {"success": True, "data": {"ok": True}}

    resp = call(flaky, tool="calculate-price", items=[9])
    check(resp.get("success") and att["n"] == 2, "429 → 重试一次后成功")

    reset()
    print("rate_limiter.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
