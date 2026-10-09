"""goldcard_rules.py · 卡权益门槛规则表（路径 A）。

职责：把卡权益建模为可判定规则，逐条给出 `{name, available, reason, threshold_cents}`。
覆盖：麦金卡专享 SKU（四件套 / 6折自由搭 / 9.9 麦咖啡）/ 早餐 6 折 / 免配送费 /
      满额福利金（到店满 39 · 麦乐送满 60 减 5）。

判定依据（真机实测 H2/H3，ADR-11）：
- **以单品 `discountType` 为主判定**：`discountType ∈ {麦金卡优惠, 随单购麦金卡优惠}`；
- 分类名 `麦金卡专享` 仅作**补充信号**（防跨分类漏判）；
- **必须能捕获「分类=人气热卖但 discountType=麦金卡优惠」的 SKU**（如 9900000002）；
- `tags[]` 为自由文本，**不作**判定依据（🟡RL-15）。

规则可能随活动/门店变化 → 结果须标注查询时点并声明"以官方页面为准"。
零第三方依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 门槛常量（可配；以官方页面为准；内部单位为分）
THRESHOLDS = {
    "free_delivery_breakfast": 1900,   # 满 19 元(早) → 分
    "free_delivery_regular": 3900,     # 满 39 元(正餐) → 分
    "welfare_instore": 3900,           # 到店满 39 减 5 → 分
    "welfare_delivery": 6000,          # 麦乐送满 60 减 5 → 分
}
BREAKFAST_WINDOW = ("05:00", "10:29")

# 真机实测枚举（单品级 discountType）
GOLDCARD_DISCOUNT_TYPES = frozenset({"麦金卡优惠", "随单购麦金卡优惠"})
# 随单购类（附加付费项，默认不勾选；CL-A05 / 🔴RL-04）
WITH_ORDER_DISCOUNT_TYPES = frozenset({"随单购麦金卡优惠", "随单购早餐卡优惠"})
GOLDCARD_CATEGORY_NAME = "麦金卡专享"  # 补充信号（防跨分类漏判）


def _category_name(meal: Dict[str, Any]) -> str:
    """取单品所属分类名（调用方把分类名注入到 meal['category'] / meal['_category']）。"""
    return str(meal.get("category") or meal.get("_category") or "").strip()


def is_goldcard_sku(meal: Dict[str, Any]) -> bool:
    """结构化判定（H3 已验证）。

    主判定：`meal['discountType'] ∈ GOLDCARD_DISCOUNT_TYPES`；
    补充：分类名 == `麦金卡专享`。
    `tags[]` 不作依据。跨分类 SKU（分类=人气热卖但 discountType=麦金卡优惠）以**单品字段**命中。
    （注：`随单购麦金卡优惠` 也算卡权益类型，但属**随单购**，见 `is_with_order_sku`。）
    """
    if not isinstance(meal, dict):
        return False
    if meal.get("discountType") in GOLDCARD_DISCOUNT_TYPES:
        return True
    if _category_name(meal) == GOLDCARD_CATEGORY_NAME:
        return True
    return False


def is_with_order_sku(meal: Dict[str, Any]) -> bool:
    """是否随单购（附加付费项）：discountType 以「随单购」开头，或 canWithOrder=True。

    随单购项**默认不勾选、不计入到手价**（🔴RL-04，CL-A05）。
    """
    if not isinstance(meal, dict):
        return False
    dt = meal.get("discountType")
    if isinstance(dt, str) and (dt in WITH_ORDER_DISCOUNT_TYPES or dt.startswith("随单购")):
        return True
    return meal.get("canWithOrder") is True


def goldcard_skus(meals: List[Dict[str, Any]], *, include_with_order: bool = True) -> List[Dict[str, Any]]:
    """卡专享 SKU 列表。`include_with_order=False` 时剔除随单购附加项。"""
    out = []
    for m in meals or []:
        if not is_goldcard_sku(m):
            continue
        if not include_with_order and is_with_order_sku(m):
            continue
        out.append(m)
    return out


def _is_breakfast(ctx: Dict[str, Any]) -> bool:
    slot = str(ctx.get("timeSlot") or "").lower()
    if slot:
        return slot in ("breakfast", "早餐")
    t = ctx.get("time")  # "HH:MM"
    if isinstance(t, str) and len(t) >= 5:
        return BREAKFAST_WINDOW[0] <= t[:5] <= BREAKFAST_WINDOW[1]
    return False


def _channel(ctx: Dict[str, Any]) -> str:
    if ctx.get("channel"):
        return str(ctx["channel"]).lower()
    if ctx.get("beType") in (2, 6) or ctx.get("orderType") == 2:
        return "delivery"
    return "instore"


def _has_name(meals: List[Dict[str, Any]], keyword: str) -> Optional[Dict[str, Any]]:
    for m in meals or []:
        if keyword in str(m.get("name") or ""):
            return m
    return None


def _self_identity_discount(meals: List[Dict[str, Any]]) -> bool:
    """检测员工卡等**非麦金卡**身份折扣（与麦金卡不叠加）→ 🔴RL-17。

    ⚠️ 本函数**只扫菜单 meals**（`discountType`），**不扫算价响应**。
    算价响应里的 `enjoyed` 身份折扣由 `identity_guard.py` 负责（🔴RL-23）；
    两者在 `detect_identity_conflict()` 中合并。

    **枚举不再本地维护**（NE-03）：判定统一走 `identity_guard.is_identity_discount_type()`，
    与算价侧**共用同一常量**（`MENU_IDENTITY_DISCOUNT_TYPES` + `IDENTITY_KEYWORDS`），
    否则会出现「菜单侧判身份、算价侧当券省额计入」的相反结论。
    """
    try:
        from identity_guard import is_identity_discount_type
    except ImportError:  # pragma: no cover
        from .identity_guard import is_identity_discount_type  # type: ignore[no-redef]
    for m in meals or []:
        if is_identity_discount_type(m.get("discountType") if isinstance(m, dict) else None):
            return True
    return False


def detect_identity_conflict(meals: Optional[List[Dict[str, Any]]] = None,
                             calc_responses: Optional[List[Any]] = None) -> Dict[str, Any]:
    """合并**两类**身份折扣信号（🔴RL-17 + 🔴RL-23）。

    - 菜单侧：`meal['discountType'] ∈ {员工卡优惠, 内部优惠, partner优惠}` → RL-17；
    - 算价侧：`calculate-price` 响应 `data.enjoyed` 命中身份关键词 / 非券类权益 → RL-23。

    出参：`{conflict, redlines:[...], redline, reasons:[...], sources:[...]}`。
    """
    try:
        from identity_guard import detect_identity_discount
    except ImportError:  # pragma: no cover
        from .identity_guard import detect_identity_discount

    redlines: List[str] = []
    reasons: List[str] = []
    sources: List[str] = []

    if _self_identity_discount(meals or []):
        redlines.append("RL-17")
        sources.append("menu.discountType")
        reasons.append("菜单中存在员工卡/内部优惠等非麦金卡身份折扣（RL-17）")

    for resp in calc_responses or []:
        g = detect_identity_discount(resp)
        if g.get("conflict"):
            if "RL-23" not in redlines:
                redlines.append("RL-23")
                sources.append("calculate-price.enjoyed")
            r = g.get("reason")
            if r and r not in reasons:
                reasons.append(r)

    return {
        "conflict": bool(redlines),
        "redlines": redlines,
        # 优先暴露算价侧（RL-23）：它直接决定「券省额能不能报数」
        "redline": "RL-23" if "RL-23" in redlines else (redlines[0] if redlines else None),
        "reasons": reasons,
        "sources": sources,
        "reason": "；".join(reasons) or None,
    }


def evaluate(meals: List[Dict[str, Any]], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """出参：`[{name, available: bool, reason: str, threshold_cents: int|None}, ...]`。

    末项恒为「本单无卡专属优惠」的显式结论（无卡专享 SKU 时 available=False）。
    """
    ctx = ctx or {}
    meals = meals or []
    benefits: List[Dict[str, Any]] = []

    all_skus = goldcard_skus(meals, include_with_order=True)
    skus = goldcard_skus(meals, include_with_order=False)   # 可选中的卡专享 SKU（剔除随单购附加项）
    with_order_skus = [m for m in all_skus if is_with_order_sku(m)]
    has_card_sku = len(skus) > 0
    breakfast = _is_breakfast(ctx)
    channel = _channel(ctx)
    cart_cents = int(ctx.get("cartTotalCents") or 0)

    # 1) 麦金卡专享 SKU 权益（H3 结构化判定）
    if has_card_sku:
        names = "；".join(f"{m.get('code')}·{m.get('name')}" for m in skus)
        benefits.append({
            "name": "麦金卡专享 SKU 权益",
            "available": True,
            "reason": f"命中卡专享 SKU（以单品 discountType 判定）：{names}。"
                      f"省额来自卡专享 SKU 价差，下单该 SKU 时自动出 discount（H2）。",
            "threshold_cents": None,
        })
    else:
        benefits.append({
            "name": "麦金卡专享 SKU 权益",
            "available": False,
            "reason": "菜单中未发现 discountType∈{麦金卡优惠,随单购麦金卡优惠} 的卡专享 SKU；"
                      "普通商品不自动套卡（H2）。",
            "threshold_cents": None,
        })

    # 2) 超值四件套（指定 SKU；卡专享为前提）
    combo = _has_name(skus, "四件套")
    benefits.append({
        "name": "超值四件套",
        "available": bool(combo),
        "reason": (f"命中卡专享四件套 SKU {combo.get('code')}·{combo.get('name')}"
                   if combo else "本单未选卡专享四件套 SKU"),
        "threshold_cents": None,
    })

    # 3) 早餐 6 折（时段约束）
    benefits.append({
        "name": "早餐 6 折",
        "available": bool(breakfast and has_card_sku),
        "reason": (f"时段={'早餐(05:00–10:29)' if breakfast else '非早餐'}"
                   + ("，且存在卡专享 SKU" if has_card_sku else "，但无卡专享 SKU")),
        "threshold_cents": None,
    })

    # 4) 免配送费（门槛 + 外送）
    fd_threshold = THRESHOLDS["free_delivery_breakfast" if breakfast else "free_delivery_regular"]
    fd_available = channel == "delivery" and cart_cents >= fd_threshold
    benefits.append({
        "name": "免配送费",
        "available": fd_available,
        "reason": (f"渠道={channel}，购物车 {cart_cents} 分，门槛 {fd_threshold} 分"
                   + ("，" if fd_available else "，未达门槛或非外送场景")),
        "threshold_cents": fd_threshold,
    })

    # 5) 满额福利金（到店满 39 / 麦乐送满 60 减 5）
    wf_threshold = THRESHOLDS["welfare_delivery" if channel == "delivery" else "welfare_instore"]
    wf_available = cart_cents >= wf_threshold
    benefits.append({
        "name": "满额福利金",
        "available": wf_available,
        "reason": (f"渠道={channel}，购物车 {cart_cents} 分，门槛 {wf_threshold} 分"
                   + ("，已达门槛" if wf_available else "，未达门槛")),
        "threshold_cents": wf_threshold,
    })

    # 6) 9.9 麦咖啡
    coffee = _has_name(skus, "麦咖啡")
    benefits.append({
        "name": "9.9 麦咖啡",
        "available": bool(coffee),
        "reason": (f"命中卡专享麦咖啡 SKU {coffee.get('code')}·{coffee.get('name')}"
                   if coffee else "本单未选卡专享麦咖啡 SKU"),
        "threshold_cents": None,
    })

    # 7) 随单购麦金卡（附加付费项）：默认不勾选（🔴RL-04 / CL-A05）
    if with_order_skus:
        wo_names = "；".join(f"{m.get('code')}·{m.get('name')}" for m in with_order_skus)
        benefits.append({
            "name": "随单购麦金卡（附加付费，默认不勾选）",
            "available": False,
            "reason": f"检测到随单购项：{wo_names}。默认**不勾选**、**不计入到手价**（🔴RL-04）。",
            "threshold_cents": None,
        })

    # 末项：显式「本单无卡专属优惠」结论
    if not has_card_sku:
        benefits.append({
            "name": "本单无卡专属优惠",
            "available": False,
            "reason": "未命中任何卡专享 SKU；若为持卡人可考虑改单（换卡专享 SKU 激活权益）。",
            "threshold_cents": None,
        })

    return benefits


def evaluate_with_guards(meals: List[Dict[str, Any]], ctx: Dict[str, Any], *,
                         calc_responses: Optional[List[Any]] = None) -> Dict[str, Any]:
    """在 evaluate 之上叠加身份折扣守卫（RL-17 菜单侧 + RL-23 算价侧），供 S3 直用。

    `calc_responses`：可选，`calculate-price` 的响应（或其 data）列表 —— 传入后会一并
    检查 `data.enjoyed` 是否为**身份折扣**（🔴RL-23，见 `identity_guard.py`）。
    """
    det = detect_identity_conflict(meals, calc_responses)
    return {
        "benefits": evaluate(meals, ctx),
        "identity_conflict": det["conflict"],
        "redline": det["redline"],
        "redlines": det["redlines"],
        "sources": det["sources"],
        "reasons": det["reasons"],
        "reason": det["reason"] or (
            "检测到其他身份折扣，与麦金卡不叠加，无法判断两条路径相对优劣"
            if det["conflict"] else None),
    }


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("goldcard_rules.py self-test")

    # 跨分类 SKU：分类=人气热卖，但 discountType=麦金卡优惠（9900000002）
    cross = {"code": "9900000002", "name": "龙焰美味四件套", "discountType": "麦金卡优惠",
             "category": "人气热卖", "currentPrice": "29", "originalPrice": "58"}
    check(is_goldcard_sku(cross) is True, "跨分类 SKU（人气热卖+麦金卡优惠）被正确命中")
    check(is_goldcard_sku({"code": "x", "name": "巨无霸", "discountType": None, "category": "单品"}) is False,
          "普通商品不被误判为卡专享")
    check(is_goldcard_sku({"code": "y", "name": "X", "category": "麦金卡专享"}) is True,
          "分类名补充信号也命中")
    # tags 不作依据
    check(is_goldcard_sku({"code": "z", "name": "X", "discountType": None, "tags": ["麦金卡"]}) is False,
          "仅 tags 含『麦金卡』不命中（tags 不作依据 🟡RL-15）")
    # 早餐卡优惠 ≠ 麦金卡
    check(is_goldcard_sku({"code": "b", "name": "早餐套餐", "discountType": "早餐卡优惠"}) is False,
          "discountType=早餐卡优惠 不判为麦金卡（CL-A06）")
    # 随单购麦金卡优惠 → 属卡权益类型但归随单购
    wo = {"code": "9900010999", "name": "麦金卡月卡", "discountType": "随单购麦金卡优惠", "canWithOrder": True}
    check(is_goldcard_sku(wo) is True and is_with_order_sku(wo) is True,
          "随单购麦金卡优惠 属卡权益类型且标记为随单购（CL-A05）")
    check(goldcard_skus([wo], include_with_order=False) == [],
          "随单购 SKU 被排除出『可选卡专享 SKU』")

    meals = [
        {"code": "9900000001", "name": "人气超值四件套随心选", "discountType": "麦金卡优惠", "category": "麦金卡专享"},
        cross,
        {"code": "1100", "name": "巨无霸", "discountType": None, "category": "单品"},
    ]
    ctx = {"timeSlot": "lunch", "beType": 1, "orderType": 1, "cartTotalCents": 8400}
    res = evaluate(meals, ctx)
    names = [b["name"] for b in res]
    combo = next(b for b in res if b["name"] == "超值四件套")
    check(combo["available"] is True, "四件套判定 available=True")
    check("麦金卡专享 SKU 权益" in names, "含『麦金卡专享 SKU 权益』条目")

    # 无卡专享 SKU → 显式结论
    res2 = evaluate([{"code": "1100", "name": "巨无霸", "discountType": None, "category": "单品"}],
                    {"timeSlot": "lunch", "cartTotalCents": 2000})
    check(any(b["name"] == "本单无卡专属优惠" and not b["available"] for b in res2),
          "无卡专享 SKU → 输出显式『本单无卡专属优惠』结论")

    # 随单购条目在 evaluate 中显式给出（默认不勾选）
    res3 = evaluate([wo], {"timeSlot": "lunch", "cartTotalCents": 1000})
    check(any("随单购" in b["name"] and b["available"] is False for b in res3),
          "evaluate 输出『随单购』条目且 available=False（RL-04）")

    # RL-17 身份折扣守卫（菜单侧）
    guard = evaluate_with_guards([{"code": "s", "name": "员工餐", "discountType": "员工卡优惠"}], ctx)
    check(guard["identity_conflict"] is True and guard["redline"] == "RL-17", "员工卡冲突 → RL-17")

    # RL-23 身份折扣守卫（算价响应侧 · enjoyed）
    guard2 = evaluate_with_guards(meals, ctx, calc_responses=[
        {"success": True, "data": {"price": 1950, "discount": 650,
                                   "enjoyed": {"promotionName": "员工卡优惠", "realDiscount": 6.5}}}])
    check(guard2["identity_conflict"] is True and guard2["redline"] == "RL-23",
          "算价响应 enjoyed=员工卡优惠 → RL-23（覆盖菜单侧优先级）")
    check("RL-23" in guard2["redlines"] and "calculate-price.enjoyed" in guard2["sources"],
          "RL-23 来源标注为 calculate-price.enjoyed")
    # 券类 enjoyed → 不误报
    guard3 = evaluate_with_guards(meals, ctx, calc_responses=[
        {"success": True, "data": {"price": 2100, "discount": 500,
                                   "enjoyed": {"promotionName": "巨无霸立减券", "realDiscount": 5.0}}}])
    check(guard3["identity_conflict"] is False, "券类 enjoyed → 不误报身份冲突")
    # NE-03：菜单侧与算价侧共用同一枚举 → 结论一致（不得相反）
    for name in ("员工卡优惠", "内部优惠", "partner优惠", "员工优惠", "内购优惠", "家属卡", "亲情卡"):
        check(_self_identity_discount([{"discountType": name}]) is True,
              f"菜单侧判身份折扣（共用枚举）：{name}")
    for name in ("麦金卡优惠", "随单购麦金卡优惠", "早餐卡优惠"):
        check(_self_identity_discount([{"discountType": name}]) is False,
              f"菜单侧不误判为身份折扣：{name}")
    # 无信号 → 无冲突
    guard4 = evaluate_with_guards(meals, ctx)
    check(guard4["identity_conflict"] is False and guard4["redline"] is None, "无身份折扣信号 → 无冲突")

    print("goldcard_rules.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
