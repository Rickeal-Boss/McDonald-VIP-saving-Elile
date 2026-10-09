"""demo.py · 离线 demo 入口（ADR-10：离线 demo 为一等公民）。

用法：
    python scripts/demo.py --demo [--out .goldcard/snapshots/demo]

不接任何 MCP、不需 token：仅用 `scripts/demo_data/` 的三份**样例** JSON 跑通全链路
（S0 上下文 → S2 菜单 → S3 卡权益 → S4 券候选 → S5 试算 → S6 比价 → S7 报告），
并把每阶段产出**落盘**（`stage_*.json` + `sentinel_done`），报告显式标注「样例」（RL-19）。

零第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import coupon_filter  # noqa: E402
import goldcard_rules  # noqa: E402
import identity_guard  # noqa: E402
import price_compare  # noqa: E402
import report as report_mod  # noqa: E402
import split_order  # noqa: E402
import trace_log  # noqa: E402

_ROOT = _HERE.parent
_DEMO_DATA = _HERE / "demo_data"
_TAKE_WAY = "到店自取"


def _load(name: str) -> dict:
    with open(_DEMO_DATA / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _flatten_meals(meals_sample: dict):
    """把 categories[] 拍平为 meals[]，并把分类名注入到每个 meal['category']。"""
    meals = []
    for cat in meals_sample.get("categories", []):
        cname = cat.get("name")
        for m in cat.get("meals", []):
            mm = dict(m)
            mm["category"] = cname
            meals.append(mm)
    return meals


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


_RELATIVE_RE = re.compile(r"^@now([+-])(\d+)([dh])$")


def _resolve_relative_validity(coupons: list, now: datetime) -> list:
    """把样例券里的 `@now±Nd/Nh` 相对标记解析为绝对时间（保证 demo 可重复跑通）。

    真实接口返回的是绝对时间字符串；这里只是**样例数据**的表达方式（RL-19）。
    无法解析的标记原样保留（→ 由 `coupon_filter` 走 `validity_unknown` 保守分支）。
    """
    out = []
    for c in coupons or []:
        cc = dict(c)
        raw = cc.get("validTo")
        m = _RELATIVE_RE.match(str(raw).strip()) if raw is not None else None
        if m:
            sign, num, unit = m.group(1), int(m.group(2)), m.group(3)
            delta = timedelta(days=num) if unit == "d" else timedelta(hours=num)
            cc["validTo"] = (now + delta if sign == "+" else now - delta).isoformat(timespec="seconds")
            cc["_validTo_placeholder"] = raw
        out.append(cc)
    return out


def check_consistency(price_sample: dict) -> list:
    """核验样例算价结果的自洽三式（04-eval §9.1 缺陷 2 / §12②）。

    ① Σ(productList[].subtotal) == price
    ② Σ(originalSubtotal − subtotal) == discount
    ③ price(带随单购) − price(不带同车) == withOrderCents
    """
    results = []
    bare = price_sample.get("cart_bare", {})
    bare_price = bare.get("price")
    for p in price_sample.get("paths", []):
        data = p.get("data", {})
        plist = data.get("productList", []) or []
        sum_sub = sum(int(x.get("subtotal", 0)) for x in plist)
        sum_orig = sum(int(x.get("originalSubtotal", 0)) for x in plist)
        price = data.get("price")
        discount = data.get("discount")
        ok1 = (sum_sub == price)
        ok2 = ((sum_orig - sum_sub) == discount)
        row = {"path_id": p["path_id"], "f1": ok1, "f2": ok2}

        if data.get("withOrder"):
            woc = data.get("withOrderCents")
            ok3 = (price is not None and bare_price is not None and (price - bare_price) == woc)
            row["f3"] = ok3
        results.append(row)
    return results


def run_demo(out_dir: Path) -> dict:
    session = "demo"
    now_dt = datetime.now().astimezone()
    now = now_dt.isoformat(timespec="seconds")

    meals_sample = _load("meals_sample.json")
    coupons_sample = _load("coupons_sample.json")
    price_sample = _load("price_sample.json")

    meals = _flatten_meals(meals_sample)
    store_code = str(meals_sample.get("storeCode"))

    # ---- S0 上下文装配 ----
    ctx = {
        "storeCode": store_code,
        "beType": meals_sample.get("beType", 1),
        "orderType": meals_sample.get("orderType", 1),
        "timeSlot": meals_sample.get("timeSlot", "lunch"),
        "channel": "instore" if meals_sample.get("beType") != 2 else "delivery",
        "cartTotalCents": 2600,
        "now": now,                     # 券有效期判定基准（🔴RL-07：不臆造，认不出即标 unknown）
    }
    stage_s0 = {
        "stage": "S0·上下文装配", "is_demo": True,
        "storeCode": store_code, "beType": ctx["beType"], "orderType": ctx["orderType"],
        "timeSlot": ctx["timeSlot"], "channel": ctx["channel"],
        "exit_ok": all(k in ctx for k in ("storeCode", "beType", "orderType", "timeSlot")),
    }
    _write_json(out_dir / "stage_s0_context.json", stage_s0)

    # ---- S1 持卡确认（H4：无法自证）----
    stage_s1 = {
        "stage": "S1·持卡确认", "is_demo": True,
        "membership_confirmed": False,
        "note": "query-my-account 无卡状态字段（H4），本 demo 采用『用户自述 + 运行时推断』；不宣称已确认持卡。",
        "exit_ok": True,
    }
    _write_json(out_dir / "stage_s1_membership.json", stage_s1)

    # ---- S2 菜单建模（元字符串）----
    stage_s2 = {
        "stage": "S2·菜单建模", "is_demo": True,
        "meal_count": len(meals),
        "meals": [{"code": m["code"], "name": m["name"], "category": m["category"],
                   "currentPrice": m.get("currentPrice"), "originalPrice": m.get("originalPrice"),
                   "discountType": m.get("discountType")} for m in meals],
        "exit_ok": len(meals) > 0,
    }
    _write_json(out_dir / "stage_s2_meals.json", stage_s2)

    # ---- S3 卡权益判定（路径 A）----
    gc = goldcard_rules.evaluate_with_guards(meals, ctx)
    stage_s3 = {"stage": "S3·卡权益判定（路径 A）", "is_demo": True, **gc, "exit_ok": True}
    _write_json(out_dir / "stage_s3_goldcard.json", stage_s3)

    # ---- S4 券候选集（路径 B；仅 query-store-coupons 形态）----
    raw_coupons = _resolve_relative_validity(coupons_sample.get("coupons", []), now_dt)
    kept, dropped = coupon_filter.filter_coupons_with_report(raw_coupons, ctx)
    candidates = []
    for c in kept:                            # kept 已带 `_validity` / `_type`
        entry = dict(c)
        entry["_voucher"] = coupon_filter.map_voucher_codes(c, meals)
        candidates.append(entry)
    expiring = coupon_filter.expiring_coupons(candidates)
    unknown_types = coupon_filter.unknown_type_coupons(candidates)
    stage_s4 = {
        "stage": "S4·券候选集（路径 B）", "is_demo": True,
        "source": "query-store-coupons（样例）",
        "candidate_count": len(candidates),
        "candidates": [{
            "couponId": c.get("couponId"), "couponCode": c.get("couponCode"),
            "name": c.get("name"), "voucher": c.get("_voucher"),
            "validity": c.get("_validity"), "type": c.get("_type"),
        } for c in candidates],
        "dropped": dropped,                       # 剔除原因逐条可查（不静默丢弃）
        "expiring_coupons": expiring,              # 临期券（≤1 天）→ 报告置顶告警
        "unknown_type_coupons": unknown_types,     # 未知券型 → 只能实算
        "exit_ok": True,
    }
    _write_json(out_dir / "stage_s4_coupons.json", stage_s4)

    # ---- S5 多路径试算（样例算价结果）----
    paths = []
    for p in price_sample.get("paths", []):
        rec = price_compare.from_calculate_price(
            p["path_id"], p["label"], p["data"],
            source=p.get("source", price_compare.SOURCE_PLAIN),
            ops_count=p.get("ops_count", 1),
            items=[it.get("name") for it in p.get("items", [])] or p.get("items"),
        )
        paths.append({**rec, "_raw": p["data"]})
    stage_s5 = {"stage": "S5·多路径试算", "is_demo": True,
                "path_count": len(paths), "paths": paths, "exit_ok": len(paths) > 0}
    _write_json(out_dir / "stage_s5_prices.json", stage_s5)

    # 样例自洽性核验（04-eval §9.1 缺陷 2）
    consistency = check_consistency(price_sample)
    _write_json(out_dir / "stage_s5_consistency.json",
                {"stage": "S5·自洽性核验", "is_demo": True, "checks": consistency})

    # ---- S5x 身份折扣守卫（🔴RL-23 专项分支，独立场景，不污染主比价）----
    ident_sample = _load("price_sample_identity.json")
    ident_consistency = check_consistency(ident_sample)
    ident_stage = {"stage": "S5x·身份折扣守卫（RL-23）", "is_demo": True,
                   "note": "构造样例：测试账号真机三次试算 enjoyed 恒缺省，无真机样本（假设 H8）",
                   "paths": [], "checks": ident_consistency}
    for p in ident_sample.get("paths", []):
        rec = price_compare.from_calculate_price(
            p["path_id"], p["label"], p["data"],
            source=p.get("source", price_compare.SOURCE_PLAIN),
            ops_count=p.get("ops_count", 1),
            items=[it.get("name") for it in p.get("items", [])] or p.get("items"))
        ident_stage["paths"].append({
            "path_id": rec["path_id"], "label": rec["label"],
            "payable_cents": rec.get("payable_cents"),
            "goods_discount_cents": rec.get("goods_discount_cents"),
            "coupon_saving_cents": rec.get("coupon_saving_cents"),
            "identity_conflict": rec.get("identity_conflict"),
            "redline": rec.get("redline"),
            "saving_incomplete": rec.get("saving_incomplete"),
            "identity_discount_ignored_cents": rec.get("identity_discount_ignored_cents"),
            "identity_reason": rec.get("identity_reason"),
            "guard": rec.get("identity_guard"),
        })
    # 若把身份折扣误当券省额会得到的错误省额（**仅供对照，不得输出为结论**）
    if ident_stage["paths"]:
        first = ident_stage["paths"][0]
        first["_if_miscounted_total_saving_cents"] = (
            (first.get("goods_discount_cents") or 0)
            + (first.get("identity_discount_ignored_cents") or 0))
    ident_stage["exit_ok"] = bool(ident_stage["paths"]) and all(
        p.get("identity_conflict") for p in ident_stage["paths"])
    _write_json(out_dir / "stage_s5x_identity_guard.json", ident_stage)

    # ---- S6 比价与硬校验 ----
    verdict = price_compare.rank(paths)
    verdict["is_demo"] = True
    stage_s6 = {"stage": "S6·比价与硬校验", "is_demo": True, **verdict, "exit_ok": verdict["cheapest"] is not None}
    _write_json(out_dir / "stage_s6_verdict.json", stage_s6)

    # ---- 拆单方案（H1）----
    split_plan = split_order.split_cart(
        items=[{"code": "1100", "name": "巨无霸", "quantity": 1},
               {"code": "900001", "name": "麦咖啡（中杯）", "quantity": 1}],
        coupons=candidates, max_orders=3)
    _write_json(out_dir / "stage_s5b_split_plan.json", {"stage": "S5b·拆单规划", "is_demo": True, **split_plan})

    # ---- S7 只读交付 ----
    meta = {
        "store_name": meals_sample.get("storeName", "样例门店"),
        "store_code": store_code,
        "take_way": _TAKE_WAY,
        "query_time": now,                  # 🔴RL-26：结果时点
        "is_demo": True,
        "split_plan": split_plan,
        "expiring_coupons": expiring,        # 🟡 临期券置顶告警
        "unknown_type_coupons": unknown_types,
        "dropped_coupons": dropped,
    }
    md = report_mod.render(verdict, meta)
    (out_dir / "stage_s7_report.md").parent.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "stage_s7_report.md", "w", encoding="utf-8") as fh:
        fh.write(md)

    sentinel = out_dir / "sentinel_done"
    _write_json(sentinel, {"done": True, "session": session, "ts": now,
                           "report": str((out_dir / "stage_s7_report.md").name)})

    # ---- Trace（JSONL，字段稳定）----
    for stage, ev, tool in (
        ("S4·券候选集", "tool_call", "query-store-coupons"),
        ("S5·多路径试算", "tool_call", "calculate-price"),
        ("S6·比价与硬校验", "decision", None),
    ):
        trace_log.emit({"session": session, "stage": stage, "event": ev, "tool": tool,
                        "ok": True, "reason": "离线 demo 样例"})
    trace_path = trace_log.flush(session)

    return {
        "ctx": ctx, "meals": meals, "goldcard": gc, "candidates": candidates,
        "dropped_coupons": dropped, "expiring": expiring, "unknown_types": unknown_types,
        "verdict": verdict, "split_plan": split_plan, "report_md": md,
        "consistency": consistency, "identity_stage": ident_stage,
        "identity_consistency": ident_consistency,
        "out_dir": out_dir, "report_path": out_dir / "stage_s7_report.md",
        "sentinel": sentinel, "trace_path": trace_path, "now": now,
    }


def _print_summary(result: dict) -> None:
    print("=" * 64)
    print("麦金卡省钱选择器 · 离线 demo（样例数据，非真实 MCP 调用 · RL-19）")
    print("=" * 64)
    print(f"门店：{result['ctx']['storeCode']}  取餐：{_TAKE_WAY}  时段：{result['ctx']['timeSlot']}")
    print(f"菜单商品数：{len(result['meals'])}")

    skus = [m for m in result["meals"] if goldcard_rules.is_goldcard_sku(m)]
    selectable = goldcard_rules.goldcard_skus(result["meals"], include_with_order=False)
    with_order = [m for m in skus if goldcard_rules.is_with_order_sku(m)]
    print(f"卡专享 SKU（按 discountType 结构化判定）：{len(skus)} 个（其中可选 {len(selectable)} / 随单购 {len(with_order)}）")
    for m in selectable:
        print(f"  - {m['code']} {m['name']}  [{m['category']}]  discountType={m.get('discountType')}")
    cross = [m for m in selectable if m["category"] != goldcard_rules.GOLDCARD_CATEGORY_NAME]
    print(f"  ★ 跨分类命中（分类≠麦金卡专享，但 discountType=麦金卡优惠）："
          f"{[(m['code'], m['category']) for m in cross]}")
    for m in with_order:
        print(f"  ⊘ 随单购（默认不勾选，RL-04）：{m['code']} {m['name']}  discountType={m.get('discountType')}")

    # CL-A03 反例：tags 启发式必须失效
    neg = [m for m in result["meals"] if m.get("tags") and "麦金卡" in m["tags"]
           and not m.get("discountType")]
    for m in neg:
        print(f"  ✗ 反例（tags=[麦金卡] 但 discountType=null）→ is_goldcard_sku="
              f"{goldcard_rules.is_goldcard_sku(m)}（应 False，RL-15）")

    cands = result["candidates"]
    print(f"券候选（仅 query-store-coupons 形态）：{len(cands)} 张")
    for c in cands:
        v = c.get("_voucher") or {}
        val = c.get("_validity") or {}
        tp = c.get("_type") or {}
        flags = []
        if val.get("expiring_soon"):
            flags.append(f"⏳临期({val.get('valid_to')})")
        if val.get("validity_unknown"):
            flags.append("❓有效期未知(保守保留)")
        if tp.get("type_unknown"):
            flags.append(f"❓券型未知({tp.get('raw')})→只能实算")
        elif tp.get("code"):
            flags.append(f"券型={tp['code']}")
        print(f"  - {c.get('couponId')} {c.get('name')}  凭证码={v.get('productCode')} "
              f"可用={v.get('usable')}  {' '.join(flags)}")
    for d in result.get("dropped_coupons") or []:
        print(f"  ✗ 剔除：{d.get('couponId')} {d.get('name')} —— {d.get('reason')}")
    if result.get("expiring"):
        print(f"  ⏳ 临期券告警（≤1 天）{len(result['expiring'])} 张 → 报告置顶")
    if result.get("unknown_types"):
        print(f"  ❓ 未知券型 {len(result['unknown_types'])} 张 → 保守保留，抵扣额仅由 calculate-price 实算")

    v = result["verdict"]
    print("-" * 64)
    print("候选对比（实付升序，单位元）：")
    for i, r in enumerate(v["ranked"], 1):
        print(f"  {i}. {r['label']:<26} 实付 ¥{price_compare.to_yuan_str(r['payable_cents'])}"
              f"  省额 ¥{price_compare.to_yuan_str(r['total_saving_cents'])}"
              f"  (商品discount ¥{price_compare.to_yuan_str(r['goods_discount_cents'])}"
              f" + 券 ¥{price_compare.to_yuan_str(r['coupon_saving_cents'])})")
    for r in v["excluded"]:
        print(f"  ✗ {r['path_id']}：{r.get('exclude_reason')}")
    cheapest = v["cheapest"]
    print(f"→ 最省方案：{cheapest['label']}，实付 ¥{price_compare.to_yuan_str(cheapest['payable_cents'])}")
    print("-" * 64)
    print("🔴RL-23 身份折扣守卫（构造样例 · 无真机样本，假设 H8）：")
    for p in (result.get("identity_stage") or {}).get("paths", []):
        print(f"  - {p['path_id']}：实付 ¥{price_compare.to_yuan_str(p['payable_cents'])}"
              f"  identity_conflict={p['identity_conflict']} redline={p.get('redline')}")
        print(f"      券省额={p['coupon_saving_cents']}（身份折扣**未**计入）"
              f"  被拦下留痕={p.get('identity_discount_ignored_cents')} 分"
              f"  省额给数字={not p.get('saving_incomplete')}")
        print(f"      话术：{p.get('identity_reason')}")
        print(f"      ⚠️ 若误计入会虚报省额 "
              f"¥{price_compare.to_yuan_str(p.get('_if_miscounted_total_saving_cents') or 0)}"
              f"（真实应为口径不完整 → 不给数字）")
    print("-" * 64)
    print(f"拆单：{split_order.summarize_split(result['split_plan'])}")
    print("-" * 64)
    print("样例算价自洽三式核验（04-eval §9.1）：")
    for c in result["consistency"]:
        flags = f"①Σsubtotal==price:{c['f1']} ②Σ(orig−sub)==discount:{c['f2']}"
        if "f3" in c:
            flags += f" ③随单购差额==withOrderCents:{c['f3']}"
        print(f"  - {c['path_id']}: {flags}")
    for c in result.get("identity_consistency") or []:
        print(f"  - {c['path_id']}(RL-23 场景): "
              f"①Σsubtotal==price:{c['f1']} ②Σ(orig−sub)==discount:{c['f2']}")
    print("-" * 64)
    print(f"阶段产出目录：{result['out_dir']}")
    print(f"报告：{result['report_path']}")
    print(f"sentinel：{result['sentinel']} 存在={result['sentinel'].exists()}")
    print("完成：demo 全链路跑通（样例数据）。")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="麦金卡省钱选择器 · 离线 demo（样例数据）")
    parser.add_argument("--demo", action="store_true", help="以离线样例数据跑通全链路")
    parser.add_argument("--out", default=str(_ROOT / ".goldcard" / "snapshots" / "demo"),
                        help="阶段产出落盘目录")
    args = parser.parse_args(argv)

    if not args.demo:
        parser.print_help()
        print("\n提示：加上 --demo 运行离线全链路（无需 MCP / token）。")
        return 2

    result = run_demo(Path(args.out))
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
