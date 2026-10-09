"""guards.py · 入参守卫（把红线落成**数据层确定性断言**，非提示词）。

覆盖（04-eval §10.4 待扩项 2 要求的 4 条）：
- **RL-09**：查可用券**不传** `reservationDate`（传了返回空）；
- **RL-10**：门店定位 `searchType=2` + `city` + `keyword`（`searchType=1` 查收藏，新账号 600050；缺 city/keyword 报 600058）；
- **RL-12**：`beCode` 规则 —— 到店自取 `beType=1` **不传**；得来速 `5` / 麦乐送 `2` / 团餐 `6` **必传**；
- **RL-22**：打烊门店预筛（依据 `businessStatus`/`businessEndTime`，规避 `600057`）。

零第三方依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# beType 语义（实测 schema）
BE_TYPE_SELF_TAKE = 1      # 到店自取
BE_TYPE_DELIVERY = 2       # 麦乐送
BE_TYPE_DRIVE_THRU = 5     # 得来速
BE_TYPE_PARTY = 6          # 团餐
_BE_TYPE_NEEDS_BECODE = frozenset({BE_TYPE_DELIVERY, BE_TYPE_DRIVE_THRU, BE_TYPE_PARTY})

SEARCH_TYPE_KEYWORD = 2    # 城市+关键词 检索
SEARCH_TYPE_FAVORITE = 1   # 收藏（新账号 600050）


class RedlineViolation(ValueError):
    """入参违反红线。携带红线编号，供报告/ Trace 归因。"""

    def __init__(self, message: str, redline: str = ""):
        super().__init__(message)
        self.redline = redline


# --------------------------------------------------------------------------- RL-09
def assert_no_reservation_date(params: Dict[str, Any]) -> None:
    """RL-09：查可用券不得携带 `reservationDate`（非空即违规）。"""
    v = (params or {}).get("reservationDate")
    if v not in (None, ""):
        raise RedlineViolation(
            "RL-09：查可用券不传 reservationDate（传了会返回空）；请移除该参数。", redline="RL-09"
        )


def store_coupons_params(store_code: Any, order_type: Any, be_type: Any,
                         *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构建 `query-store-coupons` 入参（RL-09：强制剔除 `reservationDate`）。"""
    params: Dict[str, Any] = {
        "storeCode": str(store_code),
        "orderType": int(order_type),
        "beType": int(be_type),
    }
    for k, v in (extra or {}).items():
        if k == "reservationDate":
            # 不静默丢弃 → 显式抛错（RL-09）
            assert_no_reservation_date({"reservationDate": v})
        if v not in (None, ""):
            params[k] = v
    if params.get("beCode") in (None, ""):
        params.pop("beCode", None)
    assert_no_reservation_date(params)
    return params


# --------------------------------------------------------------------------- RL-10
def nearby_stores_params(be_type: Any, city: Optional[str] = None, keyword: Optional[str] = None,
                         *, search_type: int = SEARCH_TYPE_KEYWORD) -> Dict[str, Any]:
    """构建 `query-nearby-stores` 入参（RL-10：searchType=2 + city + keyword）。"""
    if int(search_type) == SEARCH_TYPE_FAVORITE:
        raise RedlineViolation(
            "RL-10：门店定位用 searchType=2 + 城市 + 关键词；searchType=1 查收藏（新账号 600050）。",
            redline="RL-10",
        )
    if int(search_type) != SEARCH_TYPE_KEYWORD:
        raise RedlineViolation(f"RL-10：searchType 只接受 2（实际传入 {search_type}）。", redline="RL-10")
    if not city or not str(city).strip() or not keyword or not str(keyword).strip():
        raise RedlineViolation(
            "RL-10/600058：searchType=2 需同时提供 city 与 keyword；缺失时应向用户追问，不得猜测。",
            redline="RL-10",
        )
    return {"beType": int(be_type), "searchType": SEARCH_TYPE_KEYWORD,
            "city": str(city).strip(), "keyword": str(keyword).strip()}


# --------------------------------------------------------------------------- RL-12
def order_params(be_type: Any, order_type: Any, *, be_code: Optional[str] = None) -> Dict[str, Any]:
    """构建点餐类入参（RL-12：`beCode` 规则）。"""
    bt, ot = int(be_type), int(order_type)
    needs = bt in _BE_TYPE_NEEDS_BECODE
    if needs and not (be_code and str(be_code).strip()):
        raise RedlineViolation(
            f"RL-12：beType={bt}（得来速/外送/团餐）必传 beCode（来自门店/地址查询）。", redline="RL-12"
        )
    if (not needs) and be_code and str(be_code).strip():
        raise RedlineViolation(
            f"RL-12：到店自取（beType={bt}）不传 beCode，实际传入 {be_code!r}。", redline="RL-12"
        )
    params: Dict[str, Any] = {"beType": bt, "orderType": ot}
    if needs:
        params["beCode"] = str(be_code).strip()
    return params


# --------------------------------------------------------------------------- RL-22
def is_open(store: Dict[str, Any], *, now_hhmm: Optional[str] = None) -> bool:
    """门店是否营业中。`businessStatus is False` → 打烊；可选按 `businessEndTime` 复核。"""
    if not isinstance(store, dict):
        return False
    if store.get("businessStatus") is False:
        return False
    end = store.get("businessEndTime")
    if now_hhmm and isinstance(end, str) and len(end) >= 5:
        try:
            return now_hhmm[:5] < end[:5]
        except Exception:  # pragma: no cover
            return True
    return True  # 状态未知 → 不预筛掉（保守放行，交由接口返回 600057 再处理）


def filter_open_stores(stores: List[Dict[str, Any]], *, now_hhmm: Optional[str] = None) -> List[Dict[str, Any]]:
    """RL-22：预筛非打烊门店，规避 `600057 门店可能已关闭`。"""
    return [s for s in (stores or []) if is_open(s, now_hhmm=now_hhmm)]


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("guards.py self-test")

    # RL-09
    p = store_coupons_params("1950591", 1, 1)
    check("reservationDate" not in p and p["storeCode"] == "1950591", "RL-09：查券入参无 reservationDate")
    try:
        store_coupons_params("1950591", 1, 1, extra={"reservationDate": "2026-10-10"})
        check(False, "RL-09：传 reservationDate 应抛错")
    except RedlineViolation as e:
        check(e.redline == "RL-09", "RL-09：传 reservationDate 抛 RedlineViolation")

    # RL-10
    q = nearby_stores_params(1, "北京", "杜杨南街")
    check(q["searchType"] == 2 and q["city"] == "北京" and q["keyword"] == "杜杨南街", "RL-10：searchType=2+city+keyword")
    for label, fn in (
        ("RL-10：searchType=1 → 抛错", lambda: nearby_stores_params(1, "北京", "x", search_type=1)),
        ("RL-10：缺 keyword → 抛错", lambda: nearby_stores_params(1, "北京", None)),
        ("RL-10：缺 city → 抛错", lambda: nearby_stores_params(1, None, "x")),
    ):
        try:
            fn()
            check(False, label)
        except RedlineViolation as e:
            check(e.redline == "RL-10", label)

    # RL-12
    check("beCode" not in order_params(1, 1), "RL-12：到店自取不传 beCode")
    check(order_params(5, 1, be_code="DT001")["beCode"] == "DT001", "RL-12：得来速必传 beCode")
    for label, fn in (
        ("RL-12：得来速缺 beCode → 抛错", lambda: order_params(5, 1)),
        ("RL-12：外送缺 beCode → 抛错", lambda: order_params(2, 2)),
        ("RL-12：自取传 beCode → 抛错", lambda: order_params(1, 1, be_code="X")),
    ):
        try:
            fn()
            check(False, label)
        except RedlineViolation as e:
            check(e.redline == "RL-12", label)

    # RL-22
    stores = [{"storeCode": "A", "businessStatus": True, "businessEndTime": "23:00"},
              {"storeCode": "B", "businessStatus": False, "businessEndTime": "22:00"},
              {"storeCode": "C", "businessStatus": True, "businessEndTime": "21:00"}]
    kept = [s["storeCode"] for s in filter_open_stores(stores, now_hhmm="22:04")]
    check(kept == ["A"], f"RL-22：预筛打烊/超时门店（实得 {kept}）")
    check([s["storeCode"] for s in filter_open_stores(stores)] == ["A", "C"],
          "RL-22：状态未知/未知时刻时不误筛（仅按 businessStatus）")

    print("guards.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
