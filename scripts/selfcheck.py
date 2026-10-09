"""selfcheck.py · 会话入场自检 + 提交前校验（公理Ⅳ）。

入场自检（`run`）：MCP 可用 / schema 快照 hash / 假设台账待复核 / 白名单完整性 /
金额单位自测 / 限流预算参数 / 运行期目录可写 / Token 经环境变量。

提交前校验（`--pre-commit`）：无明文 token / references 无未闭合待验证(Critical 路径) /
红线编号连续 / 脚本零依赖可导入 / H1 已删 candidate_pruner。

零第三方依赖。
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
_REFERENCES = _ROOT / "references"
_ASSUMPTIONS = _REFERENCES / "assumptions.md"
_RED_LINES = _REFERENCES / "red-lines.md"
_TOOL_CONTRACT = _REFERENCES / "tool-contract.md"

CHECKLIST = [
    "1. MCP 可用 & mcd-mcp 已信任（tools/list 成功）",
    "2. 工具 schema 快照 hash 匹配",
    "3. 假设台账 H1–H4 状态复查（待验证→保守分支）",
    "4. 白名单完整性（写操作枚举未被移除）",
    "5. 金额单位自测通过（6450→'64.50'）",
    "6. 算价预算与限流参数就绪（120ms / 12 次）",
    "7. 运行期目录可写（.goldcard/{traces,cache,snapshots}）",
    "8. Token 经环境变量注入（无明文）",
]

# 工具数实测值（真机 tools/list，probe §2）——契约漂移基线
EXPECTED_TOOL_COUNT = 35
_H_IDS = ("H1", "H2", "H3", "H4", "H5", "H6", "H7")


def _ensure_scripts_on_path() -> None:
    p = str(_SCRIPTS)
    if p not in sys.path:
        sys.path.insert(0, p)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def load_feature_flags() -> Dict[str, Any]:
    """从 `references/assumptions.md` 读取 H1–H4 状态 → 会话 feature_flags（分叉单一驱动）。"""
    flags: Dict[str, Any] = {}
    text = _read(_ASSUMPTIONS)
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        head = cells[0].replace("*", "").strip()
        if head in _H_IDS:
            status_raw = cells[2] if len(cells) > 2 else ""
            branch_raw = cells[4] if len(cells) > 4 else ""
            flags[head] = {
                "status": _status_token(status_raw),
                "raw_status": status_raw,
                "branch": branch_raw,
            }
    # 关键分叉开关（H1 已定论禁组合枚举；H2 决定卡权益是否走结构化 SKU 判定）
    h1 = flags.get("H1", {})
    h2 = flags.get("H2", {})
    flags["_switches"] = {
        "combo_enumeration_allowed": False if h1.get("status") == "verified_false" else None,
        "split_order_enabled": h1.get("status") == "verified_false",
        "goldcard_sku_only": h2.get("status") in ("verified", "partial"),  # 由 assumptions H2 派生
        "goldcard_switch_source": f"H2={h2.get('status')}",
    }
    return flags


def _status_token(status_raw: str) -> str:
    s = status_raw
    if "待验证" in s and "已" not in s.replace("待验证", ""):
        return "pending"
    if "部分" in s:
        return "partial"
    if "不成立" in s:
        return "verified_false"
    if "已验证" in s or "✅" in s:
        return "verified"
    if "待验证" in s:
        return "pending"
    return "unknown"


# ---------------------------------------------------------------------------
def run() -> Dict[str, Any]:
    """出参：`{checks: [{id, ok, detail, skipped}], feature_flags: {...}, ok: bool}`。"""
    _ensure_scripts_on_path()
    import os

    checks: List[Dict[str, Any]] = []

    import money
    import rate_limiter
    import whitelist

    # 1) MCP 可用（在线探测无法在脚本内做 → 有 token 则视为就绪，否则跳过）
    token = os.environ.get("MCD_MCP_TOKEN") or os.environ.get("MCD_MCP_TOKEN_PLACEHOLDER")
    checks.append({
        "id": 1, "skipped": not bool(token), "ok": True,
        "detail": "检测到 MCD_MCP_TOKEN（运行时再由 Agent 探测 tools/list）"
        if token else "离线模式：未注入 MCD_MCP_TOKEN（demo 仍可运行）",
    })

    # 2) schema 快照 hash（记录工具数基线；运行时逐条 diff 由 Agent 侧完成）
    contract = _read(_TOOL_CONTRACT)
    recorded = re.findall(r"工具数.*?(\d+)", contract)
    recorded_count = int(recorded[0]) if recorded else None
    checks.append({
        "id": 2, "skipped": True, "ok": True,
        "detail": f"契约记录工具数={recorded_count or '未记录'}，实测基线={EXPECTED_TOOL_COUNT}；"
                  "运行时以 tools/list 为准（不一致→置 '待复核'）",
    })

    # 3) 假设台账 H1–H4
    flags = load_feature_flags()
    pending = [h for h in ("H1", "H2", "H3", "H4")
               if flags.get(h, {}).get("status") == "pending"]
    checks.append({
        "id": 3, "skipped": False, "ok": not pending,
        "detail": ("H1–H4 均非『待验证』：" + "，".join(
            f"{h}={flags.get(h, {}).get('status')}" for h in ("H1", "H2", "H3", "H4")))
        if not pending else f"存在待验证项 {pending} → 走保守分支",
    })

    # 4) 白名单完整性
    issues = whitelist.self_consistency_issues()
    checks.append({
        "id": 4, "skipped": False, "ok": not issues,
        "detail": "写操作枚举完整且未混入只读白名单" if not issues else "；".join(issues),
    })

    # 5) 金额单位自测
    money_ok = all(money.to_yuan_str(c) == want for c, want in money.SELF_TEST_CASES)
    money_ok = money_ok and money.to_cents("28", money.UNIT_YUAN) == 2800
    checks.append({
        "id": 5, "skipped": False, "ok": money_ok,
        "detail": "6450→'64.50' 等自测通过" if money_ok else "金额单位自测失败（🔴RL-02）",
    })

    # 6) 限流 / 预算参数
    params_ok = (rate_limiter.MIN_INTERVAL_MS == 120 and rate_limiter.PRICE_BUDGET_DEFAULT == 12)
    checks.append({
        "id": 6, "skipped": False, "ok": params_ok,
        "detail": f"interval={rate_limiter.MIN_INTERVAL_MS}ms budget={rate_limiter.PRICE_BUDGET_DEFAULT}次",
    })

    # 7) 运行期目录可写
    writable = True
    detail7 = ".goldcard/{traces,cache,snapshots} 可写"
    try:
        for sub in ("traces", "cache", "snapshots"):
            d = _ROOT / ".goldcard" / sub
            d.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=d, delete=True):
                pass
    except Exception as e:  # pragma: no cover
        writable = False
        detail7 = f"运行期目录不可写：{e}"
    checks.append({"id": 7, "skipped": False, "ok": writable, "detail": detail7})

    # 8) Token 经环境变量（无明文落盘）
    plaintext = _scan_plaintext_tokens()
    checks.append({
        "id": 8, "skipped": False, "ok": not plaintext,
        "detail": "未发现明文 token（仅占位符）" if not plaintext else f"发现疑似明文 token：{plaintext}",
    })

    overall = all(c["ok"] for c in checks)
    return {"checks": checks, "feature_flags": flags, "ok": overall}


# ---------------------------------------------------------------------------
_TOKEN_PATTERNS = [
    re.compile(r"Bearer\s+(?!<|\$\{|\.\.\.)[A-Za-z0-9\-_\.]{24,}"),
    re.compile(r"eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}"),  # JWT
]
_SKIP_DIRS = {".git", "__pycache__", ".goldcard", "node_modules"}


def _scan_plaintext_tokens() -> List[str]:
    hits: List[str] = []
    for path in _ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in (".json", ".md", ".py", ".txt", ".yml", ".yaml", ".env"):
            continue
        text = _read(path)
        for pat in _TOKEN_PATTERNS:
            if pat.search(text):
                hits.append(str(path.relative_to(_ROOT)))
                break
    return hits


def pre_commit() -> List[str]:
    """出参：问题清单（空 = 通过）。"""
    _ensure_scripts_on_path()
    problems: List[str] = []

    # a) 无明文 token
    plaintext = _scan_plaintext_tokens()
    if plaintext:
        problems.append(f"发现疑似明文 token（🔴RL-11）：{plaintext}")

    # b) references Critical 路径无未闭合【待验证】（仅查表体行，图例/说明行不计）
    for name in ("red-lines.md", "L3-semantic-bridge.md", "tool-contract.md"):
        text = _read(_REFERENCES / name)
        offending = [ln.strip() for ln in text.splitlines()
                     if ln.lstrip().startswith("|") and "【待验证】" in ln]
        if offending:
            problems.append(f"{name} 表体含未闭合『【待验证】』：{offending[:2]}"
                            "（Critical 路径须先探测再定稿）")

    # c) 红线编号连续无缺号
    text = _read(_RED_LINES)
    nums = sorted({int(m) for m in re.findall(r"RL-(\d+)", text)})
    if nums:
        expected = set(range(1, max(nums) + 1))
        missing = sorted(expected - set(nums))
        if missing:
            problems.append(f"红线编号缺号：RL-{missing}")

    # d) 脚本零依赖可导入
    _ensure_scripts_on_path()
    for py in sorted(_SCRIPTS.glob("*.py")):
        if py.name == "__init__.py":
            continue
        mod = py.stem
        try:
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
            else:
                importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{py.name} 导入失败（检查零依赖）：{e}")

    # e) H1 定论：candidate_pruner 必须已删除
    if (_SCRIPTS / "candidate_pruner.py").exists():
        problems.append("candidate_pruner.py 仍存在（H1 已定论禁组合枚举，应删除并改用 split_order.py）")
    if not (_SCRIPTS / "split_order.py").exists():
        problems.append("split_order.py 缺失（H1 拆单能力为核心卖点）")

    # f) 红线数据层断言覆盖（RL-09/10/12/22 须由 guards.py 提供，非仅提示词）
    try:
        import guards as _guards
        for fn in ("store_coupons_params", "nearby_stores_params", "order_params", "filter_open_stores"):
            if not callable(getattr(_guards, fn, None)):
                problems.append(f"guards.py 缺少 {fn}（RL-09/10/12/22 数据层断言缺口）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"guards.py 不可导入：{e}")

    # g) v1.1.0 新增守卫的数据层断言（RL-23 身份折扣 / 空文案 / 券有效期 / 券型归一化）
    try:
        import identity_guard as _ig
        for fn in ("detect_identity_discount", "coupon_saving_cents", "classify_enjoyed"):
            if not callable(getattr(_ig, fn, None)):
                problems.append(f"identity_guard.py 缺少 {fn}（RL-23 数据层断言缺口）")
        if getattr(_ig, "REDLINE", None) != "RL-23":
            problems.append("identity_guard.REDLINE 应为 RL-23")
    except Exception as e:  # noqa: BLE001
        problems.append(f"identity_guard.py 不可导入：{e}")

    try:
        import envelope as _env
        if not callable(getattr(_env, "looks_empty_text", None)):
            problems.append("envelope.py 缺少 looks_empty_text（空数据文案识别缺口）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"envelope.py 不可导入：{e}")

    try:
        import coupon_filter as _cf
        for fn in ("parse_validity", "normalize_coupon_type", "expiring_coupons"):
            if not callable(getattr(_cf, fn, None)):
                problems.append(f"coupon_filter.py 缺少 {fn}（券有效期/券型归一化缺口）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"coupon_filter.py 不可导入：{e}")

    # h) v1.1.0：门店三级兜底第 3 级 —— order-list 必须可用（只读）且**不在**越界集合
    try:
        import whitelist as _wl
        if not _wl.is_readonly("order-list"):
            problems.append("whitelist：order-list 未放行（S0 门店三级兜底需要，见 README）")
        if _wl.is_out_of_scope("order-list") or _wl.is_write_tool("order-list"):
            problems.append("whitelist：order-list 不得留在越界/写操作集合（语义冲突）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"whitelist.py 不可导入：{e}")

    return problems


def _print_report(result: Dict[str, Any]) -> None:
    print("=== 会话入场自检 ===")
    for c in result["checks"]:
        tag = "SKIP" if c.get("skipped") else ("OK  " if c["ok"] else "FAIL")
        print(f"[{tag}] {CHECKLIST[c['id'] - 1]}")
        print(f"        └─ {c['detail']}")
    flags = result.get("feature_flags", {})
    sw = flags.get("_switches", {})
    print(f"分叉开关：{sw}")
    print(f"总体：{'PASS' if result['ok'] else 'FAIL'}")


def _print_precommit(problems: List[str]) -> None:
    print("=== 提交前校验 ===")
    if not problems:
        print("PASS：无问题")
    else:
        for p in problems:
            print(f"FAIL：{p}")


if __name__ == "__main__":
    if "--pre-commit" in sys.argv:
        probs = pre_commit()
        _print_precommit(probs)
        raise SystemExit(0 if not probs else 1)
    if "--feature-flags" in sys.argv:
        import json
        print(json.dumps(load_feature_flags(), ensure_ascii=False, indent=2))
        raise SystemExit(0)
    res = run()
    _print_report(res)
    raise SystemExit(0 if res["ok"] else 1)
