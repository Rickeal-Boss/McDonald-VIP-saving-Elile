"""whitelist.py · 只读工具白名单闸门（🔴RL-01）。

⚠️ **诚实定位（勿夸大）**：
本模块是「**调用前策略辅助 + 违规检测**」，**不是架构级强制拦截**。
技能脚本运行在 Agent 之外，**无法拦截 Agent 直接发起的 MCP 工具调用**。
因此真正的只读强制依赖：
  1. `SKILL.md` 的硬规则（🔴RL-01，启动即加载）；
  2. 如平台支持，Hook / 连接器权限层（在 MCP 通道出入口拦截）。
本模块的价值：在**技能自身的脚本链路内**做前置守卫 —— 任何经由本技能脚本发起的工具调用，
写操作在数据层**直接拒绝**；并对越权调用做**违规检测与留痕**（写入 Trace `redline` 事件）。

零第三方依赖。
"""
from __future__ import annotations

from typing import FrozenSet, List

# 只读白名单（本技能实际放行；可增；写操作永不在其中）
READONLY_TOOLS: FrozenSet[str] = frozenset({
    "now-time-info", "query-nearby-stores", "delivery-query-stores",
    "delivery-query-addresses", "query-meals", "query-meal-detail",
    "query-store-coupons", "query-my-coupons", "calculate-price",
    "query-my-account", "campaign-calendar",
    # v1.1.0：门店定位**三级兜底**的第 3 级 —— 由历史订单反推 storeCode（只读）。
    # ⚠️ 限制（srv#13）：order-list **只有最近 ~10 单、无分页**，仅作兜底，不作主路径。
    "order-list",
})

# 明令禁止的**写操作 / 有副作用**工具（显式枚举，供自检断言未被移出）
FORBIDDEN_WRITE_TOOLS: FrozenSet[str] = frozenset({
    "create-order", "auto-bind-coupons", "draw-lottery",
    "mall-create-order", "party-order-create", "cancel-order",
    "delivery-create-address",   # 新建外送地址，有副作用
})

# **只读但超出本技能职责范围**的工具（保守拒绝；语义上不是"写操作"）
OUT_OF_SCOPE_TOOLS: FrozenSet[str] = frozenset({
    "available-coupons", "list-nutrition-foods", "query-meal-assistance",
    "mall-order-list", "mall-order-detail", "mall-points-products", "mall-product-detail",
    "query-lottery-info", "query-my-prizes", "query-order",
    "query-party-city", "query-party-store", "query-party-store-date",
    "query-party-store-session", "query-promotions", "query-survey-coupon",
})


class ReadOnlyViolation(PermissionError):
    """尝试调用写操作 / 越界 / 非白名单工具。触发 RL-01 报告话术。"""


def is_readonly(tool_name: str) -> bool:
    return tool_name in READONLY_TOOLS


def is_write_tool(tool_name: str) -> bool:
    """是否为**写操作 / 有副作用**工具（语义准确：不含只读越界工具）。"""
    return tool_name in FORBIDDEN_WRITE_TOOLS


def is_out_of_scope(tool_name: str) -> bool:
    """是否**只读但超出本技能职责**（保守拒绝，但不是写操作）。"""
    return tool_name in OUT_OF_SCOPE_TOOLS


def guard(tool_name: str) -> None:
    """调用任何 MCP 工具前必须先过 guard()。

    - 写操作枚举中 → 抛 `ReadOnlyViolation`（🔴RL-01）；
    - 只读但越界 → 抛 `ReadOnlyViolation`（保守拒绝，非写操作）；
    - 不在只读白名单（含未知工具）→ 抛 `ReadOnlyViolation`（保守拒绝）；
    - 只读白名单内 → 返回 None。
    """
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ReadOnlyViolation("工具名为空，拒绝调用（🔴RL-01 保守失败）")
    name = tool_name.strip()
    if is_write_tool(name):
        raise ReadOnlyViolation(
            f"『{name}』是写操作，本技能全程只读（🔴RL-01）："
            "止步于方案与官方支付链接；领券/下单/抽奖请您自行在官方渠道完成。"
        )
    if is_out_of_scope(name):
        raise ReadOnlyViolation(
            f"『{name}』只读但超出本技能职责范围，保守拒绝（🔴RL-01）。"
        )
    if name not in READONLY_TOOLS:
        raise ReadOnlyViolation(
            f"『{name}』不在只读白名单内，保守拒绝（🔴RL-01）。"
            "如属新增只读工具，请先更新 whitelist.READONLY_TOOLS 并记录 CHANGELOG。"
        )
    return None


def audit(calls: List[str]) -> List[dict]:
    """对一串待调用工具做批量审计，返回违规清单（供 Trace `redline` / 报告留痕）。"""
    violations = []
    for name in calls or []:
        try:
            guard(name)
        except ReadOnlyViolation as e:
            violations.append({"tool": name, "redline": "RL-01", "reason": str(e)})
    return violations


def self_consistency_issues() -> List[str]:
    """白名单自洽性检查（selfcheck 调用）：写操作不得混入只读白名单等。"""
    issues: List[str] = []
    overlap = READONLY_TOOLS & (FORBIDDEN_WRITE_TOOLS | OUT_OF_SCOPE_TOOLS)
    if overlap:
        issues.append(f"写操作/越界工具混入只读白名单：{sorted(overlap)}")
    if FORBIDDEN_WRITE_TOOLS & OUT_OF_SCOPE_TOOLS:
        issues.append(f"写操作与越界集合重叠（语义混淆）：{sorted(FORBIDDEN_WRITE_TOOLS & OUT_OF_SCOPE_TOOLS)}")
    if not FORBIDDEN_WRITE_TOOLS:
        issues.append("写操作枚举为空（RL-01 红线被掏空）")
    for must in ("create-order", "auto-bind-coupons", "draw-lottery"):
        if must not in FORBIDDEN_WRITE_TOOLS:
            issues.append(f"关键写操作 {must} 未在禁止清单中")
    return issues


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("whitelist.py self-test")
    for t in ("query-meals", "calculate-price", "query-store-coupons", "order-list"):
        try:
            guard(t)
            check(True, f"只读工具放行：{t}")
        except ReadOnlyViolation:
            check(False, f"只读工具被误拦：{t}")
    # v1.1.0：order-list 归「只读白名单」（门店三级兜底），**不是**写操作、也不是越界
    check(is_readonly("order-list") is True and is_out_of_scope("order-list") is False,
          "order-list 归只读白名单（S0 门店三级兜底），非越界")

    for t in ("create-order", "auto-bind-coupons", "draw-lottery", "mall-create-order",
              "party-order-create", "cancel-order", "delivery-create-address"):
        try:
            guard(t)
            check(False, f"写操作未拦截：{t}")
        except ReadOnlyViolation:
            check(True, f"写操作被拦截：{t}")

    # 只读但越界：拒绝，但语义上不是"写操作"
    try:
        guard("mall-order-list")
        check(False, "越界只读工具未拦截")
    except ReadOnlyViolation:
        check(True, "越界只读工具保守拒绝：mall-order-list")
    check(is_write_tool("mall-order-list") is False and is_out_of_scope("mall-order-list") is True,
          "语义准确：mall-order-list 归『越界只读』而非『写操作』")

    try:
        guard("some-unknown-tool")
        check(False, "未知工具未拦截")
    except ReadOnlyViolation:
        check(True, "未知工具保守拒绝")

    v = audit(["query-meals", "create-order", "auto-bind-coupons"])
    check(len(v) == 2 and all(x["redline"] == "RL-01" for x in v), "批量审计返回 2 条违规")

    check(self_consistency_issues() == [], "白名单自洽性检查通过")

    print("whitelist.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
