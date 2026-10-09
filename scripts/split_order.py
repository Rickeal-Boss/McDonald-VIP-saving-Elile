"""split_order.py · 拆单器（H1 定论后替代 candidate_pruner）。

背景（真机压测 H1 已定论，`docs/05-probe-report.md` §3）：
同一购物车**最多允许 1 张券**——传 2 张券直接报 `600022 暂不支持多张券使用`
（后端在券数量校验阶段即拒绝，早于券有效性校验）。
因此原 `candidate_pruner.py` 的"券组合枚举 / top-K 剪枝"**物理不可行**且与只读红线冲突，
已删除本脚本改为**拆单**：

    把一车拆为多单 → 每单各用 1 张券 → 逐单试算 → 汇总总成本。

拆单边界（必须向用户明示，见 `explain_boundaries()`）：
1. **每单仅 1 券**（H1 硬约束），券数与可拆单数上限共同决定能用的券数；
2. **最多拆 N 单**（默认 3，可配），并非无限拆——避免操作成本与运费/包装费反噬；
3. **跨单不可共享门槛 / 满减**：满减、免配送费、满额福利金等均按【单】计算，
   拆单会**重置门槛**（例如"满 39 减 5"拆成两单各 20 元则都不触发，反而不划算）；
4. 每单运费 / 包装费可能单独计算（外送场景尤甚），拆单不保证总价更低；
5. 券须绑定其专属凭证商品码（否则 600012），故券只能落到含该凭证商品的单里。

零第三方依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MAX_ORDERS_DEFAULT = 3


def explain_boundaries(max_orders: int = MAX_ORDERS_DEFAULT) -> List[str]:
    """返回拆单边界的机器可读说明（写入报告脚注 / Trace）。"""
    return [
        f"H1 硬约束：同一订单最多 1 张券（传 2 张报 600022），故每单仅用 1 券。",
        f"最多拆 {max_orders} 单（可配）；超出上限的券无法使用，将在 dropped_coupons 列出。",
        "跨单不共享门槛/满减：满减、免配送费、满额福利金等均按【单】计算，拆单会重置门槛，可能反而更贵。",
        "外送场景每单运费/包装费可能单独计算，拆单不保证总价更低——须逐单 calculate-price 精算后比较。",
        "券须绑定其专属凭证商品码（RL-07/错误码表 600012），故券只能落到含该凭证商品的单里。",
    ]


def _usable_candidates(coupons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """仅保留凭证码可用（`_voucher.usable=True`）的候选券。"""
    out = []
    for c in coupons or []:
        v = c.get("_voucher")
        if isinstance(v, dict) and v.get("usable"):
            out.append(c)
        elif v is None and (c.get("voucherProductCode") or c.get("boundProductCode")):
            # 未做映射但自带凭证码 → 视为可用（宽松）
            out.append(c)
    return out


def _voucher_code(coupon: Dict[str, Any]) -> Optional[str]:
    v = coupon.get("_voucher") or {}
    return (v.get("productCode")
            or coupon.get("voucherProductCode")
            or coupon.get("boundProductCode"))


def split_cart(items: List[Dict[str, Any]], coupons: List[Dict[str, Any]], *,
               max_orders: int = MAX_ORDERS_DEFAULT) -> Dict[str, Any]:
    """把一车拆为多单，每单各用一张券。

    - `items`：`[{"code","name","quantity"}]`（用户想点的商品）
    - `coupons`：`coupon_filter.build_candidates` 的输出（含 `_voucher`）

    出参：
    ```
    {
      "orders": [{"order_index", "coupon", "voucherProductCode", "items", "note"}],
      "base_order": {...无券单，承载剩余商品...},
      "dropped_coupons": [...超出 max_orders 上限未能使用的券...],
      "boundaries": [...边界声明...],
      "total_orders": int,
      "needs_price_check": True,
    }
    ```
    注意：本函数只给**结构方案**，真实成本须逐单 `calculate-price` 后汇总（H1 禁止组合枚举）。
    """
    usable = _usable_candidates(coupons)
    orders: List[Dict[str, Any]] = []
    assigned_codes = set()

    for i, coupon in enumerate(usable):
        if len(orders) >= max_orders:
            break
        vcode = _voucher_code(coupon)
        # 券只落到含其凭证商品的单；凭证商品不在车里也允许（券自带商品）
        order_items = [it for it in (items or []) if str(it.get("code")) == str(vcode)]
        if not order_items and vcode:
            order_items = [{"code": str(vcode), "name": coupon.get("name") or "券凭证商品",
                            "quantity": 1, "synthetic": True}]
        for it in order_items:
            assigned_codes.add(str(it.get("code")))
        orders.append({
            "order_index": len(orders) + 1,
            "coupon": {"couponId": coupon.get("couponId"), "couponCode": coupon.get("couponCode"),
                       "name": coupon.get("name")},
            "voucherProductCode": vcode,
            "items": order_items,
            "note": "每单 1 券（H1）；门槛/满减按本单独立计算",
        })

    dropped = [{"couponId": c.get("couponId"), "couponCode": c.get("couponCode"),
                "name": c.get("name"), "reason": f"超出拆单上限 max_orders={max_orders}"}
               for c in usable[len(orders):]]

    remaining = [it for it in (items or []) if str(it.get("code")) not in assigned_codes]
    base_order = None
    if remaining:
        base_order = {"order_index": len(orders) + 1, "coupon": None,
                      "items": remaining, "note": "未配送/无匹配券的商品，单列为一单（无券）"}

    return {
        "orders": orders,
        "base_order": base_order,
        "dropped_coupons": dropped,
        "boundaries": explain_boundaries(max_orders),
        "total_orders": len(orders) + (1 if base_order else 0),
        "needs_price_check": True,
    }


def summarize_split(plan: Dict[str, Any]) -> str:
    """把拆单方案渲染为一行摘要（供报告/日志）。"""
    n = plan.get("total_orders", 0)
    used = [o.get("coupon", {}).get("name") or o.get("voucherProductCode") for o in plan.get("orders", [])]
    dropped = plan.get("dropped_coupons", [])
    parts = [f"拆为 {n} 单（每单 1 券，H1）"]
    if used:
        parts.append("用券：" + "、".join(str(u) for u in used))
    if dropped:
        parts.append(f"未用券 {len(dropped)} 张（超上限）")
    return "；".join(parts) + "。跨单不共享门槛/满减，须逐单精算。"


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("split_order.py self-test")
    items = [{"code": "1100", "name": "巨无霸", "quantity": 1},
             {"code": "1440", "name": "薯条", "quantity": 1}]
    coupons = [
        {"couponId": "C1", "couponCode": "A1", "name": "巨无霸券", "_voucher": {"usable": True, "productCode": "1100"}},
        {"couponId": "C2", "couponCode": "A2", "name": "薯条券", "_voucher": {"usable": True, "productCode": "1440"}},
        {"couponId": "C3", "couponCode": "A3", "name": "无凭证券", "_voucher": {"usable": False}},
        {"couponId": "C4", "couponCode": "A4", "name": "第四张券", "_voucher": {"usable": True, "productCode": "1100"}},
    ]
    plan = split_cart(items, coupons, max_orders=3)
    check(plan["total_orders"] == 3, f"3 张可用券(max_orders=3) → 3 单（实得 {plan['total_orders']}）")
    check(plan["base_order"] is None, "所有商品都被券单覆盖 → 无 base_order")
    check(len(plan["dropped_coupons"]) == 0, "未超上限 → 无 dropped_coupons")
    check(any("跨单不共享门槛" in b for b in plan["boundaries"]), "边界声明含『跨单不共享门槛』")

    # max_orders=2 → 第 3 张券超上限
    plan_drop = split_cart(items, coupons, max_orders=2)
    check(plan_drop["total_orders"] == 2, "max_orders=2 → 2 单")
    check(len(plan_drop["dropped_coupons"]) == 1 and plan_drop["dropped_coupons"][0]["couponId"] == "C4",
          "超出上限的券进入 dropped_coupons (C4)")

    # max_orders=1 → 1 券单 + 剩余商品 base_order
    plan2 = split_cart(items, coupons, max_orders=1)
    check(plan2["total_orders"] == 2 and plan2["base_order"] is not None,
          "max_orders=1 → 1 券单 + 剩余商品 base_order")

    check("每单 1 券" in summarize_split(plan), "摘要含『每单 1 券』")

    print("split_order.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
