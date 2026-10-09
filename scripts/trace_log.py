"""trace_log.py · 结构化 Trace（JSONL，字段版本间稳定）。

落盘：`.goldcard/traces/<session>.jsonl`，每行一个 JSON 事件。
字段（**不可改名**，测评基线依赖）：`ts, session, stage, event, tool, args_digest,
cache_hit, ok, code, elapsed_ms, reason, cost{...}`。

设计要点（docs/02-design.md §9）：
- 字段名与结构跨版本稳定 → 测评可做「过程对比」与「基线回归」；
- `args_digest` 只落**入参 canonical JSON 的 sha256**，不落明文敏感值；
- 每次执行同时可采集成本画像（token / 工具次数 / 延迟 / 失败重试率）。

零第三方依赖。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

TRACE_FIELDS = (
    "ts", "session", "stage", "event", "tool", "args_digest",
    "cache_hit", "ok", "code", "elapsed_ms", "reason", "cost",
)

# 事件类型枚举（供测评分类统计）
EVENT_TYPES = ("tool_call", "tool_result", "decision", "degrade", "redline")

_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _ROOT / ".goldcard" / "traces"

# 会话缓冲（emit 先入内存，flush 落盘）
_buffers: Dict[str, List[Dict[str, Any]]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def canonical_digest(args: Dict[str, Any]) -> str:
    """归一化入参 → sha256（与 rate_limiter.cache_key 同构）。"""
    payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coerce(event: Dict[str, Any]) -> Dict[str, Any]:
    """补齐缺失字段为 None，保证字段集在版本间稳定。"""
    row: Dict[str, Any] = {}
    for f in TRACE_FIELDS:
        row[f] = event.get(f)
    if row["ts"] is None:
        row["ts"] = _now_iso()
    if row["session"] is None:
        row["session"] = "unknown"
    if row["cost"] is None:
        row["cost"] = {}
    # 除标准字段外，保留 `extra`（不参与稳定契约，测评可忽略）
    if "extra" in event:
        row["extra"] = event["extra"]
    return row


def emit(event: Dict[str, Any]) -> Dict[str, Any]:
    """写入一条事件到会话缓冲；返回补齐后的行（便于断言）。"""
    row = _coerce(event or {})
    _buffers.setdefault(row["session"], []).append(row)
    return row


def events(session: str) -> List[Dict[str, Any]]:
    return list(_buffers.get(session, []))


def flush(session: str) -> Optional[Path]:
    """落盘并清空当前会话缓冲，返回文件路径（无事件则不落盘）。"""
    rows = _buffers.pop(session, [])
    if not rows:
        return None
    _TRACES_DIR.mkdir(parents=True, exist_ok=True)
    path = _TRACES_DIR / f"{session}.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def snapshot(path: str | Path) -> List[Dict[str, Any]]:
    """读取一份 trace JSONL 为事件列表（供基线对比）。"""
    out: List[Dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return out
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def cost_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从事件流汇总成本画像（token / 工具次数 / 延迟 / 失败重试率）。"""
    tool_calls = [r for r in rows if r.get("event") == "tool_call"]
    results = [r for r in rows if r.get("event") == "tool_result"]
    degrades = [r for r in rows if r.get("event") == "degrade"]

    prompt = sum(int((r.get("cost") or {}).get("prompt_tokens") or 0) for r in rows)
    completion = sum(int((r.get("cost") or {}).get("completion_tokens") or 0) for r in rows)
    latencies = sorted(int(r.get("elapsed_ms") or 0) for r in results if r.get("elapsed_ms") is not None)

    def _pct(vals: List[int], q: float) -> int:
        if not vals:
            return 0
        idx = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
        return vals[idx]

    total_calls = len(tool_calls) or 1
    return {
        "tokens": {"prompt": prompt, "completion": completion, "total": prompt + completion},
        "tool_calls": len(tool_calls),
        "tool_calls_by_name": _count_by(tool_calls, "tool"),
        "latency_ms": {"p50": _pct(latencies, 0.50), "p95": _pct(latencies, 0.95), "p99": _pct(latencies, 0.99)},
        "degrade_events": len(degrades),
        "failure_retry_rate": round(len(degrades) / total_calls, 4),
    }


def _count_by(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        k = str(r.get(key))
        out[k] = out.get(k, 0) + 1
    return out


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("trace_log.py self-test")
    sess = "_selftest_trace"
    _buffers.pop(sess, None)

    row = emit({"session": sess, "stage": "S5·多路径试算", "event": "tool_call",
                "tool": "calculate-price", "args_digest": canonical_digest({"items": [1]}),
                "ok": True, "elapsed_ms": 42, "reason": "试算路径 A"})
    check(set(TRACE_FIELDS).issubset(row.keys()), "事件含全部稳定字段")
    check(row["ts"] is not None and row["cost"] == {}, "缺省字段被补齐（ts/cost）")

    emit({"session": sess, "stage": "S5·多路径试算", "event": "tool_result",
          "tool": "calculate-price", "ok": True, "elapsed_ms": 60,
          "cost": {"prompt_tokens": 100, "completion_tokens": 20}})
    emit({"session": sess, "stage": "S5·多路径试算", "event": "degrade", "reason": "429 → L4"})

    p = flush(sess)
    check(p is not None and Path(p).exists(), "flush 落盘 trace JSONL")
    rows = snapshot(p)
    check(len(rows) == 3, "快照可回读 3 条事件")
    check(all(set(TRACE_FIELDS).issubset(r.keys()) for r in rows), "回读事件字段稳定")

    summary = cost_summary(rows)
    check(summary["tool_calls"] == 1 and summary["tokens"]["total"] == 120, "成本画像：工具次数/Token 汇总正确")
    check(summary["degrade_events"] == 1, "成本画像：降级事件计数正确")

    Path(p).unlink(missing_ok=True)
    print("trace_log.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
