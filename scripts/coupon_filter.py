"""coupon_filter.py · 券候选集构建（🔴RL-03）。

职责（**仅**作用于 query-store-coupons 的返回，绝不消费 query-my-coupons）：
1. 按 couponId / couponCode 去重；
2. 门店 / 时段 / 渠道可用性过滤（早餐券在非早餐时段剔除）；
3. 构建「券 - 凭证商品码」映射（券须绑定专属 productCode，否则报 600012）；
4. **券有效期**：解析 `validTo`/`endTime`/`expireDate`/`expire`/`endDate` …
   → 过期剔除；**临期（≤1 天）标 `expiring_soon`**；格式不可解析 → **保守保留**并标
   `validity_unknown`（🔴RL-07：不臆造）；
5. **券型归一化**：实际券型 ≥7 种（含积分商城 `bundle_price`），不止满减 →
   归一为规范 code；**未知券型保守保留**并标 `type_unknown`，且**只能靠
   `calculate-price` 实算得出金额，禁止本地臆造抵扣算法**。

硬约束：
- 🔴RL-03：券候选**只来自 `query-store-coupons`**；`query-my-coupons` 仅作展示。
- 🔴RL-09：查可用券**不传** `reservationDate`（本模块不涉及该参数，仅在其上游约束）。
- 🟡RL-21：`available-coupons`（可领券）不含 couponId/couponCode，**不进候选**。
- 🔴RL-07：有效期/券型**认不出就标 unknown 并保守保留**，绝不补造数值。

零第三方依赖。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

_BREAKFAST_ALIASES = {"breakfast", "早餐", "早餐券", "morning"}
_REGULAR_ALIASES = {"lunch", "regular", "正餐", "午餐", "dinner", "lunch_dinner", "晚餐"}
_ALL_ALIASES = {"all", "any", "全天", "整日", "all_day", "none", ""}
_CLOSED_STATUS = {"expired", "used", "invalid", "disabled", "unavailable",
                  "已过期", "已使用", "不可用", "失效", "作废"}

# ---------------------------------------------------------------------------
# 券有效期（🟡 外部调研 #34：9 张券里 2 张当日到期 → 临期须置顶告警）
# 【待验证假设】H9：字段名与格式未见官方文档，按常见别名穷举；认不出即标 unknown。
# ---------------------------------------------------------------------------
_VALIDITY_FIELDS: tuple = (
    "validTo", "validEnd", "validEndTime", "endTime", "endDate", "expireDate",
    "expireTime", "expire", "expiryDate", "expireAt", "endTimestamp",
)
EXPIRING_SOON_SECONDS = 86400  # ≤ 1 天视为「临期」

_DATE_FORMATS: tuple = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
    "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d%H%M%S", "%Y%m%d",
)

# ---------------------------------------------------------------------------
# 券型归一化（🟡 外部调研 #9/#127：实际至少 7 种券型，含积分商城 bundle_price）
# 【待验证假设】H10：以下别名表为推断，未逐型真机取证。
# 硬规则：无论识别为何种券型，**抵扣金额一律由 calculate-price 实算**，
#        本地**禁止**实现任何「满减/折扣」计算（no_local_deduction=True）。
# ---------------------------------------------------------------------------
_COUPON_TYPE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("full_reduce", ("满减", "满立减", "满额减", "full_reduce", "fullreduce", "manjian", "threshold")),
    ("discount_rate", ("折扣", "打折", "discount_rate", "discountrate", "rate", "percent", "zhekou")),
    ("direct_reduce", ("立减", "直减", "直降", "instant", "direct", "cash", "reduce")),
    ("item_special", ("单品特价", "特价", "item_special", "special_price", "sku_price")),
    ("exchange", ("兑换", "换购", "redeem", "exchange", "swap")),
    ("free_delivery", ("免配送费", "免运费", "免外送费", "free_delivery", "free_shipping", "shipping")),
    ("bundle_price", ("bundle_price", "组合价", "套餐价", "积分商城", "超值套", "combo", "set_price")),
    ("gift", ("赠品", "赠送", "gift", "freebie", "present")),
    ("nth_discount", ("第二份", "第2份", "second", "nth", "buy_one_get")),
)
_COUPON_TYPE_FIELDS: tuple = (
    "couponType", "couponTypeName", "type", "promotionType", "discountType",
    "couponCategory", "subType", "benefitType",
)


def _norm(x: Any) -> str:
    return str(x).strip().lower() if x is not None else ""


def _channel_of_ctx(ctx: Dict[str, Any]) -> str:
    if ctx.get("channel"):
        return _norm(ctx.get("channel"))
    be_type = ctx.get("beType")
    order_type = ctx.get("orderType")
    if be_type in (2, 6) or order_type == 2:
        return "delivery"
    return "instore"


def _dedup_key(coupon: Dict[str, Any]) -> Optional[tuple]:
    cid = coupon.get("couponId")
    ccode = coupon.get("couponCode")
    if cid is None and ccode is None:
        return None
    return (str(cid or ""), str(ccode or ""))


def _unusable_reason(coupon: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[str]:
    status = _norm(coupon.get("status") or coupon.get("couponStatus") or coupon.get("state"))
    if status in _CLOSED_STATUS:
        return f"券状态不可用（status={status}）"

    if coupon.get("usable") is False or coupon.get("available") is False:
        return "接口标记该券不可用"

    # 时段
    tw = _norm(coupon.get("timeWindow") or coupon.get("timeSlot") or coupon.get("period") or "all")
    if tw not in _ALL_ALIASES:
        ctx_slot = _norm(ctx.get("timeSlot"))
        if tw in _BREAKFAST_ALIASES and ctx_slot != "breakfast":
            return "早餐券在当前非早餐时段剔除"
        if tw in _REGULAR_ALIASES and ctx_slot == "breakfast":
            return "正餐券在早餐时段剔除"

    # 渠道
    ch = _norm(coupon.get("channel") or "all")
    if ch and ch not in _ALL_ALIASES:
        want = _channel_of_ctx(ctx)
        if ch in ("instore", "delivery", "到店", "外送") and ch != want:
            return f"渠道不符（券={ch}, 场景={want}）"

    # 门店范围
    stores = coupon.get("storeCodes") or coupon.get("stores")
    if stores:
        store_code = ctx.get("storeCode")
        if store_code is not None and str(store_code) not in {str(s) for s in stores}:
            return "门店不在该券适用范围"
    # 单店券

    store_scope = _norm(coupon.get("storeCode"))
    if store_scope and ctx.get("storeCode") is not None and store_scope != _norm(ctx.get("storeCode")):
        return "该券限定门店与当前门店不一致"

    return None


# ---------------------------------------------------------------------------
# 券有效期
# ---------------------------------------------------------------------------
def _coerce_now(now: Any) -> datetime:
    """把 ctx['now']（datetime / ISO 字符串 / None）归一为 datetime。"""
    if isinstance(now, datetime):
        return now
    if isinstance(now, str) and now.strip():
        s = now.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            for fmt in _DATE_FORMATS:
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
    return datetime.now()


def _parse_datetime(value: Any) -> Optional[datetime]:
    """尽力解析有效期；认不出 → None（**绝不猜**，交回上层标 unknown）。"""
    if value is None:
        return None
    # 时间戳：秒 / 毫秒
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        if v > 1e12:      # 毫秒
            v /= 1000.0
        if v > 1e8:       # 合理范围（1973 年之后）
            try:
                return datetime.fromtimestamp(v)
            except (OverflowError, OSError, ValueError):
                return None
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # 纯数字串时间戳
    if s.isdigit():
        return _parse_datetime(int(s))
    s2 = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s2)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_validity(coupon: Dict[str, Any], *, now: Any = None) -> Dict[str, Any]:
    """解析券有效期。

    出参：`{field, raw, valid_to, expired, expiring_soon, seconds_left,
             validity_unknown, reason}`。

    - 无字段 / 解析失败 → `validity_unknown=True`，**保守保留**（不剔除、不臆造，🔴RL-07）；
    - 已过期 → `expired=True`（由 `filter_coupons` 剔除并给 reason）；
    - 未过期且剩余 ≤ 1 天 → `expiring_soon=True`（报告置顶告警）。
    """
    base: Dict[str, Any] = {
        "field": None, "raw": None, "valid_to": None, "expired": False,
        "expiring_soon": False, "seconds_left": None,
        "validity_unknown": True, "reason": None,
    }
    if not isinstance(coupon, dict):
        return base

    field = next((f for f in _VALIDITY_FIELDS if coupon.get(f) not in (None, "")), None)
    if field is None:
        base["reason"] = "券未携带可识别的有效期字段 → 保守保留并标 validity_unknown（RL-07 不臆造）"
        return base

    raw = coupon.get(field)
    dt = _parse_datetime(raw)
    base.update({"field": field, "raw": raw})
    if dt is None:
        base["reason"] = f"有效期字段 {field}={raw!r} 格式无法解析 → 保守保留并标 validity_unknown（RL-07）"
        return base

    now_dt = _coerce_now(now)
    # 无时区的裸日期 → 按本地时间比较（与 now 保持同一基准）
    if dt.tzinfo is not None and now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=dt.tzinfo)
    elif dt.tzinfo is None and now_dt.tzinfo is not None:
        dt = dt.replace(tzinfo=now_dt.tzinfo)

    left = (dt - now_dt).total_seconds()
    base.update({
        "validity_unknown": False,
        "valid_to": dt.isoformat(timespec="seconds"),
        "seconds_left": left,
        "expired": left < 0,
        "expiring_soon": 0 <= left <= EXPIRING_SOON_SECONDS,
    })
    if left < 0:
        base["reason"] = f"券已过期（{field}={raw}）"
    elif left <= EXPIRING_SOON_SECONDS:
        base["reason"] = f"券临期（{field}={raw}，不足 1 天到期）→ 建议优先使用"
    return base


# ---------------------------------------------------------------------------
# 券型归一化
# ---------------------------------------------------------------------------
def normalize_coupon_type(coupon: Dict[str, Any]) -> Dict[str, Any]:
    """券型归一化。未知券型 → **保守保留**并标 `type_unknown=True`。

    硬规则（🔴RL-07 + H10）：无论识别为何型，`no_local_deduction=True` —— 抵扣金额
    **只能**由 `calculate-price` 实算得出，本地**禁止**实现满减/折扣算法。

    出参：`{code, raw, type_unknown, requires_calculate, no_local_deduction}`。
    """
    out: Dict[str, Any] = {
        "code": "unknown", "raw": None, "type_unknown": True,
        "requires_calculate": True, "no_local_deduction": True,
    }
    if not isinstance(coupon, dict):
        return out

    raws: List[str] = []
    for f in _COUPON_TYPE_FIELDS:
        v = coupon.get(f)
        if v not in (None, ""):
            raws.append(str(v))
    name = coupon.get("name") or coupon.get("couponName") or coupon.get("title")
    if name:
        raws.append(str(name))
    if not raws:
        out["raw"] = None
        return out

    out["raw"] = raws[0]
    hay = " | ".join(_norm(r) for r in raws)
    for code, keys in _COUPON_TYPE_RULES:
        if any(k in hay for k in keys):
            out.update({"code": code, "type_unknown": False})
            return out
    # 认不出 → 保守保留（不剔除），后续只能靠实算
    return out


# ---------------------------------------------------------------------------
def filter_coupons(store_coupons: List[Dict[str, Any]], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """入参：门店券列表（`query-store-coupons` 的 data）+ 上下文。

    ctx 支持键：`storeCode` / `timeSlot`（breakfast|regular）/ `beType` / `orderType` /
    `channel` / `now`（datetime 或 ISO 字符串，用于有效期判定；缺省取当前时间）。

    出参：可用券列表（已去重、已按门店/时段/渠道/**有效期**过滤）。**券为 0 时返回空列表**，
    由调用方落盘空数组 + reason（不吞掉）。
    """
    return filter_coupons_with_report(store_coupons, ctx)[0]


def filter_coupons_with_report(store_coupons: List[Dict[str, Any]],
                               ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """同 `filter_coupons`，额外返回被剔除券清单 `[{couponId, name, reason}]`。

    成功判据可落盘：剔除原因逐条可查，**不静默丢弃**。
    """
    ctx = ctx or {}
    now = ctx.get("now")
    seen: set = set()
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, str]] = []
    for coupon in store_coupons or []:
        if not isinstance(coupon, dict):
            continue
        key = _dedup_key(coupon)
        if key is None:
            # 无 couponId/couponCode → 不可试算（可能来自 available-coupons 形态，🟡RL-21）
            dropped.append({"couponId": None, "name": coupon.get("name"),
                            "reason": "券缺 couponId/couponCode，不可试算（可能来自 available-coupons 形态，RL-21）"})
            continue
        if key in seen:
            dropped.append({"couponId": coupon.get("couponId"), "name": coupon.get("name"),
                            "reason": "与前列券重复（couponId+couponCode 去重）"})
            continue
        seen.add(key)

        reason = _unusable_reason(coupon, ctx)
        if reason is not None:
            dropped.append({"couponId": coupon.get("couponId"), "name": coupon.get("name"),
                            "reason": reason})
            continue

        # 有效期：过期剔除；临期与未知均**保留**（临期由报告置顶告警）
        validity = parse_validity(coupon, now=now)
        if validity.get("expired"):
            dropped.append({"couponId": coupon.get("couponId"), "name": coupon.get("name"),
                            "reason": validity.get("reason") or "券已过期"})
            continue

        entry = dict(coupon)
        entry["_validity"] = validity
        entry["_type"] = normalize_coupon_type(coupon)
        kept.append(entry)
    return kept, dropped


def map_voucher_codes(coupon: Dict[str, Any], meals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """券 → 绑定凭证商品码映射；找不到凭证码 → 标记 `usable=False`（防 600012）。

    出参：`{couponId, couponCode, productCode, usable, reason}`。
    """
    menu_codes = {str(m.get("code")) for m in (meals or []) if isinstance(m, dict) and m.get("code")}
    vcode = (coupon.get("voucherProductCode")
             or coupon.get("boundProductCode")
             or coupon.get("voucherProduct")
             or coupon.get("productCode"))
    base = {
        "couponId": coupon.get("couponId"),
        "couponCode": coupon.get("couponCode"),
        "productCode": None,
        "usable": False,
        "reason": None,
    }
    if vcode in (None, ""):
        base["reason"] = "券未携带绑定凭证商品码 → 直接试算将报 600012（🔴RL-07 / 错误码表）"
        return base
    vcode = str(vcode)
    base["productCode"] = vcode
    if menu_codes and vcode not in menu_codes:
        base["reason"] = f"绑定凭证码 {vcode} 不在当前门店菜单，无法入单试算（防 600012）"
        return base
    base["usable"] = True
    return base


def build_candidates(store_coupons: List[Dict[str, Any]], meals: List[Dict[str, Any]],
                     ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一步到位：过滤 + 凭证码映射。出参为候选券条目列表（含 `_voucher` / `_validity` / `_type`）。

    - `_validity`：有效期判定（过期不会到这里；`expiring_soon` 供报告置顶告警）；
    - `_type`：券型归一化（未知型保守保留，抵扣额**只能实算**）。
    """
    candidates: List[Dict[str, Any]] = []
    for coupon in filter_coupons(store_coupons, ctx):
        entry = dict(coupon)
        entry["_voucher"] = map_voucher_codes(coupon, meals)
        entry.setdefault("_validity", parse_validity(coupon, now=ctx.get("now")))
        entry.setdefault("_type", normalize_coupon_type(coupon))
        candidates.append(entry)
    return candidates


def expiring_coupons(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """取出临期券（`expiring_soon=True`），供报告置顶告警。"""
    out = []
    for c in candidates or []:
        v = c.get("_validity") or {}
        if v.get("expiring_soon"):
            out.append({
                "couponId": c.get("couponId"), "couponCode": c.get("couponCode"),
                "name": c.get("name"), "valid_to": v.get("valid_to"),
                "raw": v.get("raw"), "reason": v.get("reason"),
            })
    return out


def unknown_type_coupons(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """取出券型未知的券（保守保留，但**只能实算**）。"""
    out = []
    for c in candidates or []:
        t = c.get("_type") or {}
        if t.get("type_unknown"):
            out.append({"couponId": c.get("couponId"), "name": c.get("name"),
                        "raw": t.get("raw")})
    return out


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    meals = [{"code": "1100"}, {"code": "9900000001"}]
    ctx = {"storeCode": "1950591", "timeSlot": "lunch", "beType": 1, "orderType": 1}
    coupons = [
        {"couponId": "C1", "couponCode": "A1", "voucherProductCode": "1100", "timeWindow": "all"},
        {"couponId": "C1", "couponCode": "A1", "voucherProductCode": "1100", "timeWindow": "all"},  # 重复
        {"couponId": "C2", "couponCode": "A2", "voucherProductCode": "1100", "timeWindow": "breakfast"},
        {"couponId": "C3", "couponCode": "A3"},                                                    # 无凭证码
        {"couponId": "C4", "couponCode": "A4", "voucherProductCode": "1100", "channel": "delivery"},
    ]

    print("coupon_filter.py self-test")
    kept = filter_coupons(coupons, ctx)
    ids = [(c["couponId"], c["couponCode"]) for c in kept]
    # C1 保留；C1 重复项去重；C2 早餐券在正餐时段剔除；C4 渠道不符剔除；
    # C3 无凭证码 → 通过可用性过滤（凭证码校验归 map_voucher_codes），故保留
    check(ids == [("C1", "A1"), ("C3", "A3")], f"去重+时段+渠道过滤（实得 {ids}）")

    cands = build_candidates(coupons, meals, ctx)
    check(cands[0]["_voucher"]["usable"] is True, "C1 凭证码映射 usable=True")
    check(cands[1]["_voucher"]["usable"] is False, "C3 无凭证码 → 候选标记不可用（防 600012）")

    m = map_voucher_codes({"couponId": "C3", "couponCode": "A3"}, meals)
    check(m["usable"] is False and m["productCode"] is None, "无凭证码 → usable=False（防 600012）")

    m2 = map_voucher_codes({"couponId": "C9", "couponCode": "A9", "voucherProductCode": "8888"}, meals)
    check(m2["usable"] is False, "凭证码不在菜单 → usable=False")

    check(filter_coupons([], ctx) == [], "空券集 → 空列表（不吞掉）")

    # ---- 券有效期（🟡 #34：临期券须置顶告警 / 过期券剔除）----
    now_dt = datetime(2026, 10, 9, 22, 0, 0)
    ctx_now = dict(ctx, now=now_dt)
    exp_c = {"couponId": "E1", "couponCode": "X1", "validTo": "2026-10-08 23:59:59", "timeWindow": "all"}
    soon_c = {"couponId": "E2", "couponCode": "X2", "validTo": "2026-10-10 21:00:00", "timeWindow": "all"}
    ok_c = {"couponId": "E3", "couponCode": "X3", "endTime": "2026-11-30 23:59:59", "timeWindow": "all"}
    weird_c = {"couponId": "E4", "couponCode": "X4", "expireDate": "有效期见券面", "timeWindow": "all"}
    none_c = {"couponId": "E5", "couponCode": "X5", "timeWindow": "all"}
    kept2, dropped2 = filter_coupons_with_report([exp_c, soon_c, ok_c, weird_c, none_c], ctx_now)
    ids2 = [c["couponId"] for c in kept2]
    check("E1" not in ids2 and any(d["couponId"] == "E1" for d in dropped2),
          "过期券（validTo 已过）→ 剔除并给 reason")
    check(sorted(ids2) == ["E2", "E3", "E4", "E5"], f"临期/正常/未知格式券均保留（实得 {ids2}）")
    v_soon = next(c for c in kept2 if c["couponId"] == "E2")["_validity"]
    check(v_soon["expiring_soon"] is True and v_soon["expired"] is False, "临期券（<1 天）→ expiring_soon=True")
    v_weird = next(c for c in kept2 if c["couponId"] == "E4")["_validity"]
    check(v_weird["validity_unknown"] is True and v_weird["expired"] is False,
          "格式无法解析 → validity_unknown=True 且保守保留（RL-07 不臆造）")
    v_none = next(c for c in kept2 if c["couponId"] == "E5")["_validity"]
    check(v_none["validity_unknown"] is True, "无有效期字段 → validity_unknown=True（不臆造）")
    check([d["couponId"] for d in dropped2 if d["couponId"] == "E1"] == ["E1"]
          and "过期" in dropped2[0]["reason"], "剔除清单带 reason（不静默丢弃）")
    # 次日到期但 > 24h 不算临期
    v_ok = next(c for c in kept2 if c["couponId"] == "E3")["_validity"]
    check(v_ok["expiring_soon"] is False, "剩余 >1 天 → 不标临期")
    # 时间戳形态（毫秒）
    ms = int(datetime(2026, 10, 9, 23, 30, 0).timestamp() * 1000)
    v_ms = parse_validity({"expireAt": ms}, now=now_dt)
    check(v_ms["expiring_soon"] is True and v_ms["validity_unknown"] is False, "毫秒时间戳也可解析")

    # ---- 券型归一化（🟡 #9/#127：≥7 种券型，含积分商城 bundle_price）----
    check(normalize_coupon_type({"couponType": "满减"})["code"] == "full_reduce", "券型归一：满减 → full_reduce")
    check(normalize_coupon_type({"couponType": "discount_rate"})["code"] == "discount_rate", "券型归一：discount_rate")
    check(normalize_coupon_type({"couponType": "立减"})["code"] == "direct_reduce", "券型归一：立减 → direct_reduce")
    check(normalize_coupon_type({"couponType": "单品特价"})["code"] == "item_special", "券型归一：单品特价 → item_special")
    check(normalize_coupon_type({"couponType": "特价"})["code"] == "item_special", "券型归一：特价 → item_special")
    check(normalize_coupon_type({"couponType": "兑换券"})["code"] == "exchange", "券型归一：兑换 → exchange")
    check(normalize_coupon_type({"couponType": "免配送费"})["code"] == "free_delivery", "券型归一：免配送费")
    check(normalize_coupon_type({"couponType": "bundle_price"})["code"] == "bundle_price",
          "券型归一：积分商城 bundle_price")
    check(normalize_coupon_type({"couponType": "赠品"})["code"] == "gift", "券型归一：赠品 → gift")
    check(normalize_coupon_type({"couponType": "第二份半价"})["code"] == "nth_discount", "券型归一：第二份半价")
    unk = normalize_coupon_type({"couponType": "某新型权益"})
    check(unk["type_unknown"] is True and unk["code"] == "unknown", "未知券型 → type_unknown=True（保守保留）")
    check(unk["requires_calculate"] is True and unk["no_local_deduction"] is True,
          "未知券型只能靠 calculate-price 实算（禁止本地臆造抵扣算法）")
    check(normalize_coupon_type({"couponType": "满减"})["no_local_deduction"] is True,
          "已知券型同样禁止本地臆造抵扣额（一律实算）")
    # 名称兜底
    check(normalize_coupon_type({"name": "超值三件套组合价"})["code"] == "bundle_price", "券型可由 name 兜底识别")

    # 临期券抽取（供报告置顶）
    check([c["couponId"] for c in expiring_coupons(kept2)] == ["E2"], "expiring_coupons 取回临期券 E2")
    known = build_candidates([{"couponId": "K1", "couponCode": "KK1", "couponType": "满减",
                               "voucherProductCode": "1100"}], meals, ctx)
    check([c["couponId"] for c in unknown_type_coupons(known)] == [],
          "已知券型不进『未知券型』清单")
    check(len(unknown_type_coupons(build_candidates([none_c], meals, ctx))) == 1,
          "无券型字段 → 进『未知券型』清单（保守保留待实算）")

    print("coupon_filter.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
