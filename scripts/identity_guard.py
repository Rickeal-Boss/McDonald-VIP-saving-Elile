"""identity_guard.py · 身份折扣守卫（🔴RL-23）。

背景（**外部证据，非测试账号真机样本**）
--------------------------------------
`calculate-price` 响应的 `data.enjoyed`（含 `promotionName` / `realDiscount` /
`discountCents`）在语义上**双关**：

- 形态 ①：**券 / 活动**生效 → `enjoyed` = 本单已享受的券省额 → **可以**计入券后省额；
- 形态 ②：**身份 / 会员**折扣（员工卡 7.5 折、员工餐、家属卡、亲情卡…）→
  表示账号自带的**身份折扣**，且**与麦金卡权益是「替换」而非「叠加」**
  （带卡价反而可能更高）→ **绝不可**当作「券省额」计入，否则会凭空造出省额并掩盖互斥事实。

⚠️ **诚实声明（必读）**
----------------------
测试账号真机三次试算中 `enjoyed` **恒缺省**，因此**本模块没有任何测试账号真机样本**。
判定规则来自外部调研（大赛 #60「麦回本 mc-breakeven」实测）与字段语义推断，
属 **【待验证假设】**（见 `references/assumptions.md` H8）。
代码一律走**保守降级**：宁可不计数（少报省额），也不误计身份折扣（虚报省额）。
**严禁**在文档 / 报告中把本规则表述为「已验证事实」。

设计取向（保守优先）
--------------------
- 命中身份关键词 / 非券类权益类型 → `conflict=True` → 调用方**不得**计入券省额；
- **券类词与身份类词同时命中**（如「员工卡专享券」）→ 按**身份折扣**处理（少报 > 虚报）；
- `enjoyed` 存在但**类型不可辨识**（无任何名称/类型字段，或有字段但既不像券也不像身份）
  → `indeterminate=True`：按券通道计入**并**在 Trace / 报告中留「待复核」痕（**不静默**）。

> ⚠️ **枚举单一来源（NE-03）**：菜单侧 `meal['discountType']` 与算价侧 `enjoyed.*`
> **共用**本模块的 `is_identity_discount_type()` / `MENU_IDENTITY_DISCOUNT_TYPES`，
> 避免「同一折扣在菜单链路判为身份折扣、在算价链路却被当券省额计入」的相反结论。
> `scripts/goldcard_rules.py` **不再**自行维护该枚举。

零第三方依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from money import UNIT_CENT, UNIT_YUAN_FLOAT, to_cents
except ImportError:  # pragma: no cover
    from .money import UNIT_CENT, UNIT_YUAN_FLOAT, to_cents

REDLINE = "RL-23"

# ---------------------------------------------------------------------------
# 关键词表：命中即判定为「身份 / 会员类折扣」而非「券」。
# 说明：按**子串**匹配，故勿放入过宽的词（如「会员」「优惠」会误伤券类）。
# ---------------------------------------------------------------------------
IDENTITY_KEYWORDS: tuple = (
    # 中文
    "员工卡", "员工餐", "员工价", "员工优惠", "内部", "内购", "职工", "家属卡",
    "亲情卡", "家属", "身份", "工牌", "伙伴", "在职",
    # 英文 / 字段名
    "staff", "employee", "identity", "internal", "crew", "team_member",
    "staffmeal", "staff_meal", "partner", "partnercard",
)

# ---------------------------------------------------------------------------
# **菜单侧**已知的身份折扣取值（`meal['discountType']`）。
# 与算价侧共用同一判定入口 `is_identity_discount_type()`（NE-03：两个入口不得结论相反）。
# 说明：`partner优惠` 无法被上面的通用关键词命中（不是「伙伴」也不是「partnercard」），
# 故在此**显式登记**；切勿在 `goldcard_rules.py` 另建一份枚举。
# ---------------------------------------------------------------------------
MENU_IDENTITY_DISCOUNT_TYPES: frozenset = frozenset({
    "员工卡优惠", "员工餐优惠", "员工优惠", "内部优惠", "内部员工价",
    "partner优惠", "家属卡优惠", "亲情卡优惠",
})

# 明确指向「券 / 活动」的词：出现则可**排除**身份折扣判定（避免误报）
COUPON_KEYWORDS: tuple = (
    "券", "coupon", "promo", "promotion", "voucher", "ticket", "card_coupon",
    "活动", "红包", "直减", "满减",
)

# 参与拼接检索的字段（enjoyed 内）
_TEXT_FIELDS: tuple = (
    "promotionName", "name", "title", "description", "desc", "promotionTitle",
    "activityName", "benefitName", "remark",
)
# 参与拼接检索的类型字段（enjoyed 内）
_TYPE_FIELDS: tuple = (
    "type", "promotionType", "discountType", "cardType", "benefitType",
    "source", "category", "subType",
)


def _norm(v: Any) -> str:
    return str(v).strip().lower() if v is not None else ""


def _iter_enjoyed(enjoyed: Any) -> List[Dict[str, Any]]:
    """`enjoyed` 可能是 dict / list[dict] / None → 统一为 dict 列表。"""
    if isinstance(enjoyed, dict):
        return [enjoyed]
    if isinstance(enjoyed, list):
        return [x for x in enjoyed if isinstance(x, dict)]
    return []


def is_identity_discount_type(value: Any) -> bool:
    """**唯一的**「折扣类型名 → 是否身份折扣」判定入口（菜单侧与算价侧共用，NE-03）。

    命中条件（任一）：
    1. 精确属于 `MENU_IDENTITY_DISCOUNT_TYPES`（如 `partner优惠`）；
    2. 文本命中 `IDENTITY_KEYWORDS`（如 `员工卡优惠` / `staff` / `identity`）。

    菜单侧调用：`goldcard_rules._self_identity_discount()`；
    算价侧调用：`classify_enjoyed()` 对 `type/promotionType/discountType/...` 逐字段检查。
    """
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    if s in MENU_IDENTITY_DISCOUNT_TYPES:
        return True
    low = s.lower()
    return any(k in low for k in IDENTITY_KEYWORDS)


def _haystack(entry: Dict[str, Any]) -> str:
    parts: List[str] = []
    for f in _TEXT_FIELDS + _TYPE_FIELDS:
        v = entry.get(f)
        if v is not None:
            parts.append(_norm(v))
    return " | ".join(parts)


def _pick_data(resp: Any) -> Any:
    """接受 `calculate-price` 的完整响应（`{success, data, ...}`）或其中的 data。"""
    if isinstance(resp, dict) and "data" in resp and ("success" in resp or "code" in resp or "msg" in resp):
        return resp.get("data")
    if isinstance(resp, dict) and "data" in resp and isinstance(resp.get("data"), dict):
        return resp.get("data")
    return resp


def amount_cents(entry: Dict[str, Any]) -> Optional[int]:
    """取 `enjoyed` 条目上的金额（分）。仅用于**留痕**，是否被计入由调用方决定。

    `discountCents` = 分整数；`realDiscount` = 元(number)（真机实测形态）。
    任一取值失败 → 返回 None（不臆造）。
    """
    if not isinstance(entry, dict):
        return None
    try:
        if entry.get("discountCents") is not None:
            return to_cents(entry["discountCents"], UNIT_CENT, tag="enjoyed.discountCents")
        if entry.get("realDiscount") is not None:
            return to_cents(entry["realDiscount"], UNIT_YUAN_FLOAT, tag="enjoyed.realDiscount")
    except Exception:  # noqa: BLE001 —— 单位不符只留痕，不外抛（降级到「无金额」）
        return None
    return None


def classify_enjoyed(entry: Dict[str, Any]) -> Dict[str, Any]:
    """判定单条 `enjoyed` 的性质。

    出参：`{is_identity: bool, is_coupon: bool, indeterminate: bool,
             matched: str|None, haystack: str, amount_cents: int|None}`
    """
    if not isinstance(entry, dict):
        return {"is_identity": False, "is_coupon": False, "indeterminate": True,
                "matched": None, "haystack": "", "amount_cents": None}

    hay = _haystack(entry)
    # 1) 先按**类型字段**逐一精确判定（与菜单侧共用 `is_identity_discount_type`，NE-03）
    for f in _TYPE_FIELDS:
        v = entry.get(f)
        if v is not None and is_identity_discount_type(v):
            return {"is_identity": True, "is_coupon": False, "indeterminate": False,
                    "matched": f"{f}={v}", "haystack": hay, "amount_cents": amount_cents(entry)}
    # 2) 再按整体文本关键词判定（券类与身份类同时命中 → 按身份，少报 > 虚报）
    identity_hit = next((k for k in IDENTITY_KEYWORDS if k in hay), None)
    coupon_hit = next((k for k in COUPON_KEYWORDS if k in hay), None)

    # 同时命中（如「员工卡专享券」）→ 保守按**身份折扣**处理（少报 > 虚报）
    if identity_hit:
        return {"is_identity": True, "is_coupon": False, "indeterminate": False,
                "matched": identity_hit, "haystack": hay, "amount_cents": amount_cents(entry)}
    if coupon_hit:
        return {"is_identity": False, "is_coupon": True, "indeterminate": False,
                "matched": coupon_hit, "haystack": hay, "amount_cents": amount_cents(entry)}

    # 无名称/类型可辨：有金额但说不清来源 → 无法定类（保守留痕）
    if not hay.strip():
        return {"is_identity": False, "is_coupon": False, "indeterminate": True,
                "matched": None, "haystack": hay, "amount_cents": amount_cents(entry)}

    # 有文本但既不像身份也不像券 → 无法定类
    return {"is_identity": False, "is_coupon": False, "indeterminate": True,
            "matched": None, "haystack": hay, "amount_cents": amount_cents(entry)}


def detect_identity_discount(calc_resp_or_data: Any) -> Dict[str, Any]:
    """从**算价响应**的 `data.enjoyed` 检测身份折扣（🔴RL-23）。

    入参：`calculate-price` 的完整响应（`{success, data, ...}`）或其 `data` 本体。

    出参：
    ```
    {
      "conflict": bool,          # True → 调用方**不得**把 enjoyed 计入券后省额
      "redline": "RL-23"|None,
      "reason": str|None,        # 用户可理解的降级说明（报告话术）
      "indeterminate": bool,     # enjoyed 存在但类型不可辨 → 需留痕复核
      "ignored_cents": int|None, # 被拦下、未计入券省额的金额（仅留痕，不展示）
      "entries": [ {...} ],      # 逐条判定明细（Trace 留痕）
    }
    """
    data = _pick_data(calc_resp_or_data)
    entries_raw = _iter_enjoyed((data or {}).get("enjoyed") if isinstance(data, dict) else None)

    base = {"conflict": False, "redline": None, "reason": None,
            "indeterminate": False, "ignored_cents": None, "entries": []}

    if not entries_raw:
        return base

    judged = [dict(classify_enjoyed(e), raw_keys=sorted(e.keys())) for e in entries_raw]
    base["entries"] = judged

    identity = [j for j in judged if j.get("is_identity")]
    if identity:
        amt: Optional[int] = None
        for j in identity:
            if j.get("amount_cents") is not None:
                amt = (amt or 0) + int(j["amount_cents"])
        base.update({
            "conflict": True,
            "redline": REDLINE,
            "reason": ("检测到其他身份折扣（" + "、".join(sorted({str(j['matched']) for j in identity}))
                       + "），与麦金卡不叠加，无法判断两条路径相对优劣；"
                         "该折扣未计入券后省额（🔴RL-23）"),
            "ignored_cents": amt,
        })
        return base

    if all(j.get("indeterminate") for j in judged):
        base["indeterminate"] = True
        base["reason"] = ("enjoyed 存在但无法辨识其类型（券 / 身份折扣），按券省额通道计入并标记待复核"
                          "（🔴RL-23 · 【待验证假设】H8）")
    return base


def coupon_saving_cents(data: Dict[str, Any], *, path_id: str = "?") -> Dict[str, Any]:
    """**唯一的**「enjoyed → 券后省额」入口（`price_compare` 必须走这里）。

    出参：`{cents: int, conflict: bool, guard: dict}`。
    - `conflict=True` → `cents` 恒为 **0**（身份折扣不计入券省额，🔴RL-23）；
    - `conflict=False` → 按券通道取 `enjoyed.discountCents` / `enjoyed.realDiscount`。
    """
    guard = detect_identity_discount(data)
    if guard["conflict"]:
        return {"cents": 0, "conflict": True, "guard": guard}

    data = data if isinstance(data, dict) else {}
    if data.get("couponSavingCents") is not None:
        return {"cents": to_cents(data["couponSavingCents"], UNIT_CENT,
                                  tag=f"{path_id}.couponSavingCents"),
                "conflict": False, "guard": guard}

    for entry in _iter_enjoyed(data.get("enjoyed")):
        amt = amount_cents(entry)
        if amt is not None:
            return {"cents": int(amt), "conflict": False, "guard": guard}
    # 券未生效（测试账号真机实测 enjoyed 恒缺省）
    return {"cents": 0, "conflict": False, "guard": guard}


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("identity_guard.py self-test")

    # 1) 身份折扣（员工卡）→ conflict
    g = detect_identity_discount({"success": True, "data": {
        "price": 1950, "discount": 650,
        "enjoyed": {"promotionName": "员工卡优惠", "realDiscount": 6.5}}})
    check(g["conflict"] is True and g["redline"] == "RL-23", "员工卡 enjoyed → RL-23 冲突")
    check(g["ignored_cents"] == 650, "被拦下的金额 650 分仅留痕（ignored_cents）")
    check("不叠加" in (g["reason"] or ""), "给出『与麦金卡不叠加』话术")

    # 2) 各类身份关键词
    for kw in ("员工餐", "内部员工价", "家属卡", "亲情卡", "staff", "employee", "identity", "身份折扣"):
        gi = detect_identity_discount({"data": {"enjoyed": {"promotionName": kw, "discountCents": 100}}})
        check(gi["conflict"] is True, f"身份关键词命中：{kw}")

    # 3) type 字段指向身份类
    g3 = detect_identity_discount({"data": {"enjoyed": {"name": "打折", "type": "identity",
                                                        "realDiscount": 3.0}}})
    check(g3["conflict"] is True, "enjoyed.type=identity → 冲突（不看名称）")

    # 4) 券类 → 不冲突，可计入券省额
    g4 = detect_identity_discount({"data": {"enjoyed": {"promotionName": "巨无霸立减券",
                                                        "realDiscount": 5.0}}})
    check(g4["conflict"] is False, "券类 promotionName → 不冲突")
    r4 = coupon_saving_cents({"enjoyed": {"promotionName": "巨无霸立减券", "realDiscount": 5.0}})
    check(r4["cents"] == 500 and r4["conflict"] is False, "券类 enjoyed → 券省额 500 分正常计入")

    # 5) 身份折扣 → 券省额恒 0（不得计入）
    r5 = coupon_saving_cents({"enjoyed": {"promotionName": "员工卡优惠", "realDiscount": 6.5}})
    check(r5["cents"] == 0 and r5["conflict"] is True, "身份折扣 → coupon_saving_cents 恒 0（RL-23）")

    # 6) 类型不可辨 → indeterminate（留痕，不误判）
    g6 = detect_identity_discount({"data": {"enjoyed": {"discountCents": 200}}})
    check(g6["conflict"] is False and g6["indeterminate"] is True, "无名称/类型 → indeterminate 留痕")
    r6 = coupon_saving_cents({"enjoyed": {"discountCents": 200}})
    check(r6["cents"] == 200 and r6["guard"]["indeterminate"] is True, "不可辨类型按券通道计入并标待复核")

    # 7) enjoyed 缺省（测试账号真机恒缺省）→ 无冲突、省额 0
    g7 = detect_identity_discount({"success": True, "data": {"price": 2600, "discount": 0}})
    check(g7["conflict"] is False and g7["entries"] == [], "enjoyed 缺省 → 无冲突")
    check(coupon_saving_cents({"price": 2600})["cents"] == 0, "无 enjoyed → 券省额 0")

    # 8) enjoyed 为列表
    g8 = detect_identity_discount({"data": {"enjoyed": [
        {"promotionName": "满减券", "realDiscount": 2.0},
        {"promotionName": "员工卡", "realDiscount": 6.5}]}})
    check(g8["conflict"] is True, "enjoyed 列表含身份折扣 → 冲突")

    # 9) 同时命中券与身份词 → 保守按身份
    g9 = detect_identity_discount({"data": {"enjoyed": {"promotionName": "员工卡专享券"}}})
    check(g9["conflict"] is True, "券/身份词同时命中 → 保守按身份折扣（少报 > 虚报）")

    # 10) 直接传 data 本体（非完整响应）也能工作
    g10 = detect_identity_discount({"enjoyed": {"promotionName": "内部优惠", "discountCents": 300}})
    check(g10["conflict"] is True, "直接传 data 本体同样生效")

    # 11) NE-03：菜单侧与算价侧**不得给出相反结论**（共用同一枚举）
    for name in ("员工卡优惠", "内部优惠", "partner优惠", "员工优惠", "内购优惠", "家属卡", "亲情卡"):
        check(is_identity_discount_type(name) is True, f"类型名判为身份折扣（共用枚举）：{name}")
        j = classify_enjoyed({"promotionName": name, "realDiscount": 5.0})
        check(j.get("is_identity") is True, f"算价侧同样判身份：{name}")
        s = coupon_saving_cents({"enjoyed": {"promotionName": name, "realDiscount": 5.0}})
        check(s["cents"] == 0 and s["conflict"] is True, f"算价侧不计入券省额：{name}")
    # 反例：麦金卡 / 早餐卡等**不得**被误判为身份折扣
    for name in ("麦金卡优惠", "随单购麦金卡优惠", "早餐卡优惠", "普通商品"):
        check(is_identity_discount_type(name) is False, f"非身份折扣不误判：{name}")

    print("identity_guard.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
