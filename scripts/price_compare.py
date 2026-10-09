"""price_compare.py · 多路径比价与硬校验（🔴RL-04 / 🔴RL-08）。

职责：
1. 多路径算价结果排序取最低（实付升序）；
2. **分列**输出「商品自身 discount」与「券后省额」两个口径（不混加、不互相顶替）；
3. 省钱额口径硬校验：只取 `calculate-price.discount` / `enjoyed`，**菜单划线价不参与**；
4. 剔除随单购后的真实到手价（`withOrder` 默认不传，🔴RL-04）；
5. `enjoyable` / `balance` 是营销门槛提示 → **不得**计入到手价（🔴RL-20）；
6. 同价时择"更少操作 / 不牺牲用户想吃的"；
7. **🔴RL-23**：`enjoyed` 语义双关 —— 若它是**身份折扣**（员工卡 / 内部 / 家属卡 /
   亲情卡 / staff / employee / identity），**不得**当券省额计入，且须声明
   「与麦金卡不叠加，无法判断两条路径相对优劣」，该路径**不给券省额数字**。

零第三方依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:  # 兼容「包导入」与「脚本直跑」两种方式
    from money import UnitMismatch, UNIT_CENT, UNIT_YUAN_FLOAT, to_cents, to_yuan_str, is_marketing_hint
    from identity_guard import coupon_saving_cents as _enjoyed_saving, REDLINE as RL_IDENTITY
except ImportError:  # pragma: no cover
    from .money import UnitMismatch, UNIT_CENT, UNIT_YUAN_FLOAT, to_cents, to_yuan_str, is_marketing_hint
    from .identity_guard import coupon_saving_cents as _enjoyed_saving, REDLINE as RL_IDENTITY

# 路径来源标签
SOURCE_GOLDCARD = "goldcard"
SOURCE_COUPON = "coupon"
SOURCE_PLAIN = "plain"


class SavingSourceViolation(ValueError):
    """省钱额来源非法（🔴RL-08）。触发 RL-08 报告话术，**不产出数字**。"""


# --------------------------------------------------------------------------
# 1) 原始 calculate-price 结果 → 规范化记录
# --------------------------------------------------------------------------
def _coupon_saving_cents(data: Dict[str, Any], path_id: str) -> int:
    """券后省额（分）：只认 `enjoyed`（已享受），**绝不**认 `enjoyable`（🔴RL-20）。

    ⚠️ 🔴RL-23：`enjoyed` 语义双关。若它代表**身份折扣**（员工卡等，与麦金卡
    **替换而非叠加**），则**恒返回 0**（不得计入券省额），由 `from_calculate_price`
    标记 `identity_conflict` 并给出降级话术。详见 `identity_guard.py`。
    """
    saving = _enjoyed_saving(data, path_id=path_id)
    return int(saving["cents"])


def strip_bundle(price: Dict[str, Any]) -> Dict[str, Any]:
    """剔除随单购附加项，返回真实到手价（🔴RL-04）。

    - `withOrder` 为空/None → 原样返回；
    - `withOrder` 存在且附带 `withOrderCents`（附加付费额，分）→ 从实付中扣除后返回；
    - `withOrder` 存在但**无法拆分金额** → 标记 `excluded=True`（不产出可能误导的数字）。
    """
    rec = dict(price)
    with_order = rec.get("withOrder") or rec.get("with_order")
    if not with_order:
        rec.setdefault("excluded", False)
        rec.setdefault("bundle_stripped", False)
        return rec

    rec["bundle_seen"] = True
    woc = rec.get("withOrderCents", rec.get("with_order_cents"))
    if isinstance(woc, int) and not isinstance(woc, bool) and woc > 0:
        if rec.get("payable_cents") is not None:
            rec["payable_cents"] = int(rec["payable_cents"]) - woc
        rec["withOrder"] = None
        rec["bundle_stripped"] = True
        rec["excluded"] = False
        rec["note"] = (rec.get("note") or "") + f"；已剔除随单购附加付费 {to_yuan_str(woc)} 元（RL-04）"
        return rec

    # 无法拆分 → 排除，不给数字
    rec["excluded"] = True
    rec["bundle_stripped"] = False
    rec["exclude_reason"] = "RL-04：算价含随单购附加付费项且无法拆分，已排除（不报可能误导的低价）"
    return rec


def from_calculate_price(path_id: str, label: str, data: Dict[str, Any], *,
                         source: str = SOURCE_PLAIN,
                         ops_count: int = 1,
                         items: Optional[List[str]] = None) -> Dict[str, Any]:
    """把 `calculate-price` 的 data（单位：主价=分）映射为规范化比价记录。"""
    if not isinstance(data, dict):
        raise ValueError(f"[{path_id}] data 必须是对象")

    payable = data.get("price", data.get("productPrice"))
    original = data.get("originalPrice", data.get("productOriginalPrice"))
    goods_discount = data.get("discount", 0)

    rec: Dict[str, Any] = {
        "path_id": path_id,
        "label": label,
        "source": source,
        "success": bool(data.get("success", True)),
        "ops_count": ops_count,
        "items": list(items or data.get("items") or []),
        "withOrder": data.get("withOrder"),
        "withOrderCents": data.get("withOrderCents"),
        "saving_source": "calculate-price.discount / enjoyed",  # 唯一合法口径（RL-08）
        "uses_menu_original_for_saving": False,
    }
    try:
        rec["payable_cents"] = to_cents(payable, UNIT_CENT, tag=f"{path_id}.price") if payable is not None else None
        rec["original_cents"] = to_cents(original, UNIT_CENT, tag=f"{path_id}.originalPrice") if original is not None else None
        rec["goods_discount_cents"] = to_cents(goods_discount, UNIT_CENT, tag=f"{path_id}.discount")
        # 🔴RL-23：`enjoyed` 双关 → 先过身份折扣守卫，再决定能否计入券省额
        _saving = _enjoyed_saving(data, path_id=path_id)
        rec["identity_guard"] = _saving["guard"]
        rec["identity_conflict"] = bool(_saving["conflict"])
        rec["coupon_saving_cents"] = int(_saving["cents"])
    except UnitMismatch as e:  # 单位不符 → 记录错误，绝不出数字
        rec.update({
            "success": False,
            "error": str(e),
            "payable_cents": None,
            "goods_discount_cents": None,
            "coupon_saving_cents": None,
            "excluded": True,
            "exclude_reason": f"RL-02 金额单位不符：{e}",
        })
        return rec

    # 🔴RL-23：身份折扣（员工卡等）与麦金卡「替换」而非「叠加」→ 不计入券省额、不给省额数字
    guard = rec.get("identity_guard") or {}
    if guard.get("conflict"):
        rec["identity_conflict"] = True
        rec["redline"] = RL_IDENTITY
        rec["coupon_saving_cents"] = 0            # 身份折扣**不得**当券省额
        rec["identity_discount_ignored_cents"] = guard.get("ignored_cents")
        rec["saving_incomplete"] = True           # 省额口径不完整 → 报告不给数字
        rec["note"] = (rec.get("note") or "") + f"；{guard.get('reason') or RL_IDENTITY}"
        rec["identity_reason"] = (guard.get("reason")
                                  or "检测到其他身份折扣，与麦金卡不叠加，无法判断两条路径相对优劣")
    elif guard.get("indeterminate"):
        rec["identity_indeterminate"] = True
        rec["note"] = (rec.get("note") or "") + (
            "；enjoyed 类型不可辨（券/身份折扣），按券省额计入并标记待复核（RL-23 · 假设 H8）")

    rec["total_saving_cents"] = (rec["goods_discount_cents"] or 0) + (rec["coupon_saving_cents"] or 0)
    rec.setdefault("excluded", False)
    return strip_bundle(rec)


# --------------------------------------------------------------------------
# 2) 硬校验
# --------------------------------------------------------------------------
def assert_saving_source(price: Dict[str, Any]) -> None:
    """省额必须来自 `discount` / `enjoyed`；出现划线价参与即抛（🔴RL-08）。"""
    if price.get("uses_menu_original_for_saving") is True:
        raise SavingSourceViolation(
            "省额被标记为使用菜单划线价计算（🔴RL-08）；省钱额仅取 calculate-price 的 discount。"
        )
    src = str(price.get("saving_source") or "")
    if "menu" in src.lower() or "划线价" in src:
        raise SavingSourceViolation(f"省额来源非法：{src!r}（🔴RL-08）")
    if price.get("goods_discount_cents") is None and price.get("coupon_saving_cents") is None:
        raise SavingSourceViolation("缺少 discount 口径的省额字段（🔴RL-08）")


def assert_no_marketing_hint(price: Dict[str, Any]) -> None:
    """确认 `enjoyable` / `balance` 未被计入到手价（🔴RL-20）。"""
    for k in price.keys():
        if is_marketing_hint(k) and price.get(k) not in (None, 0, {}, []):
            raise SavingSourceViolation(f"检测到门槛提示字段 {k} 被写入比价记录（🔴RL-20）")


# --------------------------------------------------------------------------
# 3) 排序取最低
# --------------------------------------------------------------------------
def _sort_key(rec: Dict[str, Any]):
    # 实付升序为主键；同价时"操作更少"优先（少一步凑单/拆单）
    payable = rec.get("payable_cents")
    payable = payable if isinstance(payable, int) else 1 << 60
    ops = rec.get("ops_count")
    ops = ops if isinstance(ops, int) else 99
    return (payable, ops)


def rank(paths: List[Dict[str, Any]]) -> Dict[str, Any]:
    """出参：`{ranked, cheapest, excluded, notes, tie_break_note}`。

    - 只对 `success=True` 且 `excluded=False` 的记录排序；
    - 每条记录先过硬校验（单位/省额口径/门槛提示）。
    """
    ranked: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    notes: List[str] = []

    for rec in paths or []:
        assert_saving_source(rec)      # 🔴RL-08
        assert_no_marketing_hint(rec)  # 🔴RL-20
        if rec.get("excluded") or not rec.get("success"):
            excluded.append(rec)
            continue
        if rec.get("payable_cents") is None:
            excluded.append({**rec, "excluded": True, "exclude_reason": "无实付金额"})
            continue
        ranked.append(rec)

    ranked.sort(key=_sort_key)

    tie_break_note = ""
    if len(ranked) >= 2 and _sort_key(ranked[0])[0] == _sort_key(ranked[1])[0]:
        tie = [r for r in ranked if _sort_key(r)[0] == _sort_key(ranked[0])[0]]
        tie_break_note = ("多条路径实付相同，已择『操作更少 / 不牺牲用户想吃的』者："
                          + "、".join(r["path_id"] for r in tie))

    bundle_seen = any(r.get("bundle_seen") for r in excluded + ranked)
    if bundle_seen:
        notes.append("RL-04：检测到随单购（附加付费项），已剔除/排除，以下为不含附加项的真实到手价。")

    # 🔴RL-23：任一路徑命中身份折扣 → 全局标记，报告侧不再给「省额 / 优劣结论」
    identity_conflict = any(r.get("identity_conflict") for r in excluded + ranked)
    identity_reasons: List[str] = []
    if identity_conflict:
        for r in ranked + excluded:
            if r.get("identity_conflict"):
                reason = (r.get("identity_reason")
                          or "检测到其他身份折扣，与麦金卡不叠加，无法判断两条路径相对优劣")
                if reason not in identity_reasons:
                    identity_reasons.append(reason)
        for reason in identity_reasons:
            notes.append(f"RL-23：{reason}")

    indeterminate = any(r.get("identity_indeterminate") for r in ranked + excluded)

    # 🔴RL-23（裁定口径 B 第 3 条）：两条路径**不可比** → 不存在「最省」
    # 冲突时**不得**把任一路径标为 cheapest（否则等于下了优劣结论）。
    cheapest: Optional[Dict[str, Any]] = None if identity_conflict else (ranked[0] if ranked else None)

    return {
        "ranked": ranked,
        "cheapest": cheapest,
        # 冲突时 cheapest 被显式压制（供报告/Trace 归因，防"静默无结果"）
        "cheapest_suppressed": identity_conflict,
        "cheapest_suppressed_reason": (
            "RL-23：身份折扣与麦金卡为『替换』而非『叠加』关系，两条路径实付不可直接比较，"
            "故不指定最省方案" if identity_conflict else None),
        "excluded": excluded,
        "notes": notes,
        "bundle_seen": bundle_seen,
        "tie_break_note": tie_break_note,
        "identity_conflict": identity_conflict,
        "identity_reasons": identity_reasons,
        "identity_indeterminate": indeterminate,
        "comparison_unreliable": identity_conflict,  # 无法判断两条路径相对优劣
    }


def best_of(records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """逐张券 / 逐路径取最低（H1：一单一券 → 逐张单独算价取最低）。"""
    return rank(records)["cheapest"]


# --------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("price_compare.py self-test")

    # 卡专享 SKU：5800→2800，discount=3000（真机实测形态）
    a = from_calculate_price("pathA_goldcard", "路径A·卡专享", {
        "success": True, "price": 2800, "productPrice": 2800, "originalPrice": 5800,
        "discount": 3000, "withOrder": None,
    }, source=SOURCE_GOLDCARD)
    check(a["payable_cents"] == 2800 and a["goods_discount_cents"] == 3000 and a["coupon_saving_cents"] == 0,
          "商品自身 discount 与券后省额分列（A: 3000 / 0）")

    # 券路径：enjoyed.realDiscount（元）→ 分；商品自身 discount=0
    b = from_calculate_price("pathB_coupon", "路径B·门店券", {
        "success": True, "price": 2100, "originalPrice": 2600, "discount": 0,
        "enjoyed": {"realDiscount": 5.0},
    }, source=SOURCE_COUPON)
    check(b["coupon_saving_cents"] == 500 and b["goods_discount_cents"] == 0,
          "券后省额来自 enjoyed（元→分 500），商品 discount 单列 0")

    # 随单购陷阱（无附加金额）→ 排除
    trap = from_calculate_price("pathB_bundle", "路径B·含随单购(陷阱)", {
        "success": True, "price": 4700, "originalPrice": 2600, "discount": 0,
        "withOrder": {"cardId": "GOLDCARD_MONTHLY"},
    }, source=SOURCE_COUPON)
    check(trap["excluded"] is True and "RL-04" in trap["exclude_reason"], "随单购无金额可拆 → 排除（RL-04）")

    plain = from_calculate_price("pathB_plain", "路径B·原价", {
        "success": True, "price": 3500, "originalPrice": 3500, "discount": 0,
    }, source=SOURCE_PLAIN)

    res = rank([a, plain, b, trap])
    check(res["cheapest"]["path_id"] == "pathB_coupon", "取最低 = 券路径（2100）")
    check([r["path_id"] for r in res["ranked"]] == ["pathB_coupon", "pathA_goldcard", "pathB_plain"],
          "实付升序排序正确")
    check(any(r["path_id"] == "pathB_bundle" for r in res["excluded"]), "随单购路径进入 excluded")
    check(any("RL-04" in n for n in res["notes"]), "输出 RL-04 提示")

    # 🔴RL-08：划线价算省额 → 抛错
    bad = dict(a, money_source_used="menu", saving_source="menu.originalPrice", uses_menu_original_for_saving=True)
    try:
        assert_saving_source(bad)
        check(False, "RL-08 硬校验（未抛错）")
    except SavingSourceViolation:
        check(True, "RL-08：划线价算省额被拦截")

    # 🔴RL-20：enjoyable 被计入到手价 → 抛错
    bad2 = dict(a, enjoyable={"balance": 1100})
    try:
        assert_no_marketing_hint(bad2)
        check(False, "RL-20 硬校验（未抛错）")
    except SavingSourceViolation:
        check(True, "RL-20：enjoyable/balance 计入被拦截")

    # 单位不符 → 记录失败，不出数字
    c = from_calculate_price("pathX", "单位错", {"success": True, "price": "28", "discount": 0})
    check(c["success"] is False and c["payable_cents"] is None, "算价金额为字符串元 → RL-02 拒绝出数")

    # ---- 🔴RL-23：enjoyed 是身份折扣（员工卡）时不得计入券省额 ----
    ident = from_calculate_price("pathX_identity", "路径X·员工卡", {
        "success": True, "price": 1950, "originalPrice": 2600, "discount": 650,
        "enjoyed": {"promotionName": "员工卡优惠", "realDiscount": 6.5},
    }, source=SOURCE_PLAIN)
    check(ident["identity_conflict"] is True and ident["redline"] == "RL-23",
          "员工卡 enjoyed → 路径标 identity_conflict / RL-23")
    check(ident["coupon_saving_cents"] == 0, "身份折扣**未**计入券后省额（恒 0）")
    check(ident["saving_incomplete"] is True, "省额口径不完整 → saving_incomplete=True（报告不给数字）")
    check(ident["identity_discount_ignored_cents"] == 650, "被拦下的 650 分仅作留痕")
    check("不叠加" in (ident.get("identity_reason") or ""),
          "给出『与麦金卡不叠加，无法判断两条路径相对优劣』话术")

    # 券类 enjoyed 仍走券省额通道（不得误伤）
    coup = from_calculate_price("pathB_coupon2", "路径B·立减券", {
        "success": True, "price": 2100, "originalPrice": 2600, "discount": 0,
        "enjoyed": {"promotionName": "巨无霸立减券", "realDiscount": 5.0},
    }, source=SOURCE_COUPON)
    check(coup["coupon_saving_cents"] == 500 and coup["identity_conflict"] is False,
          "券类 enjoyed → 券省额 500 分正常计入（通道保留）")

    res_id = rank([ident, coup])
    check(res_id["identity_conflict"] is True and res_id["comparison_unreliable"] is True,
          "rank 汇总 identity_conflict 且标记 comparison_unreliable")
    check(any("RL-23" in n for n in res_id["notes"]), "rank 输出 RL-23 提示")

    print("price_compare.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
