"""report.py · 报告装配（按 assets/report-template.md 渲染）。

职责：把 S6 结论（`price_compare.rank` 的输出） + 元数据渲染为最终报告；
按命中情况附加**模板化红线话术**（RL-04 / L3 / RL-17 / L4 / RL-19 / RL-18 / RL-01）。

必含字段：方案名 / 商品明细 / 实付【元】 / 省钱额【元】 / 优惠来源 / 查询时间戳 / 门店。
**只读交付**：报告不含任何写操作；支付链接由用户自行完成（🔴RL-01）。

零第三方依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from money import to_yuan_str
    from price_compare import SOURCE_COUPON, SOURCE_GOLDCARD, SOURCE_PLAIN
except ImportError:  # pragma: no cover
    from .money import to_yuan_str
    from .price_compare import SOURCE_COUPON, SOURCE_GOLDCARD, SOURCE_PLAIN

_SOURCE_LABEL = {
    SOURCE_GOLDCARD: "麦金卡权益（卡专享 SKU 价差）",
    SOURCE_COUPON: "门店券",
    SOURCE_PLAIN: "菜单原价",
}

_FOOTNOTES = {
    "RL-04": "已默认不勾选随单购（附加付费项）；以上为不含附加项的真实到手价。",
    "RL-01": "本技能全程只读，止步于方案与官方支付链接；领券/下单/抽奖请您自行在官方渠道完成。",
    "RL-17": "检测到其他身份折扣，与麦金卡不叠加，无法判断两条路径相对优劣。",
    "RL-18": "价格随活动/门店变动，以官方页面为准。",
    "RL-19": "当前为离线样例演示，非真实 MCP 调用。",
    "RL-20": "`enjoyable` 是营销提示（再买 X 元可享 Y 优惠），非已享受折扣；到手价只认 `discount`。",
    "RL-02": "检测到金额单位不一致，已按『菜单=元、算价=分』统一换算；实付以算价【分】÷100 为准。",
    # ---- v1.1.0 新增（RL-23 ~ RL-27）----
    "RL-23": "检测到身份类折扣（员工卡/内部/家属卡等）：它与麦金卡是『替换』而非『叠加』，"
             "已**不计入**券后省额，故本单不给出省额数字；两条路径的相对优劣无法判断。",
    "RL-24": "本技能只读：不领券（`auto-bind-coupons` 属写操作，已禁用），因此**未领取的可领券不在候选内**，"
             "实付可能因此被**系统性高估** —— 您自行领券后重算可能更省。",
    "RL-25": "外送场景：配送费 / 打包费 / 餐具费已**单列**，未混入商品价；麦金卡『免配送费』权益已显式计入。",
    "RL-26": "结果为**查询时点**的试算快照；价格 / 活动 / 券库存随时变动，**以官方最终结算为准**。",
    "RL-27": "『随心配』类商品官方未暴露可自选商品池，只能按**默认搭配**算价 —— 此价为默认组合价，实际可自选。",
    "L2": "本单暂无可用门店券；以上为卡路径 / 原价路径对照。",
    "L3": "无法确认您的麦金卡权益在本单是否生效，以上为两条路径各自的实测价，供您对照选择。",
    "L4": "当前无法获取优惠试算，以上为菜单原价，建议稍后重试或到官方 App 核对。",
    "L5": "无法给出可靠比较；禁止编造数字，请补充信息后重试。",
}

# 每次渲染**必带**的通用脚注（与命中无关）
_ALWAYS_FOOTNOTES = ("RL-26", "RL-24", "RL-18", "RL-01")


def _yuan(cents: Optional[int]) -> str:
    if cents is None:
        return "—"
    return f"¥{to_yuan_str(cents)}"


def _items_str(rec: Dict[str, Any]) -> str:
    items = rec.get("items") or []
    if not items:
        return "—"
    parts = []
    for it in items:
        if isinstance(it, dict):
            name = it.get("name") or it.get("code")
            qty = it.get("quantity", 1)
            parts.append(f"{name} × {qty}")
        else:
            parts.append(str(it))
    return "，".join(parts)


def _saving_str(rec: Dict[str, Any]) -> str:
    """省钱额展示。**口径不完整（如 RL-23 身份折扣冲突）时不给数字**（🔴RL-06/RL-23）。"""
    if rec.get("saving_incomplete") or rec.get("identity_conflict"):
        return "—（口径不完整，不给数字 · RL-23）"
    return _yuan(rec.get("total_saving_cents"))


def _saving_detail_str(rec: Dict[str, Any]) -> str:
    """省钱额拆分（商品自身 discount + 券后省额）。口径不完整时给「—」。"""
    if rec.get("saving_incomplete") or rec.get("identity_conflict"):
        return "—（商品 discount 与券省额无法可靠拆分 · RL-23）"
    return (f"商品自身 discount {_yuan(rec.get('goods_discount_cents'))}"
            f" + 券后省额 {_yuan(rec.get('coupon_saving_cents'))}")


def _looks_delivery(meta: Dict[str, Any], verdict: Dict[str, Any]) -> bool:
    """是否外送场景：`fee_breakdown` 存在 / `verdict.delivery` / 取餐方式含外送语义。"""
    if meta.get("fee_breakdown") or verdict.get("delivery"):
        return True
    take_way = str(meta.get("take_way") or "")
    return any(k in take_way for k in ("外送", "麦乐送", "delivery"))


def pick_footnotes(verdict: Dict[str, Any]) -> List[str]:
    """按命中情况返回需附加的模板化话术编号（RL-/Lx），顺序稳定、去重。"""
    picked: List[str] = []

    def add(code: str) -> None:
        if code not in picked:
            picked.append(code)

    if verdict.get("is_demo"):
        add("RL-19")
    if verdict.get("bundle_seen"):
        add("RL-04")
    if verdict.get("membership_unconfirmed"):
        add("L3")
    if verdict.get("identity_conflict"):
        add("RL-23")                       # 算价侧身份折扣（enjoyed，v1.1.0）
        add("RL-17")                       # 菜单侧身份折扣
    # 🔴RL-25 **只在真有费用明细时才声称「已单列」** —— 无数据却声称单列属误导（NE-20）
    if verdict.get("fee_breakdown") or verdict.get("has_fee"):
        add("RL-25")                       # 外送费用单列
    if verdict.get("has_flexible_combo"):
        add("RL-27")                       # 随心配默认组合价
    if verdict.get("degraded_level"):
        add(verdict["degraded_level"])       # L2 / L3 / L4 / L5
    if verdict.get("uses_marketing_hint"):
        add("RL-20")
    if verdict.get("unit_mismatch"):
        add("RL-02")
    for code in _ALWAYS_FOOTNOTES:
        add(code)
    return picked


def render(verdict: Dict[str, Any], meta: Dict[str, Any]) -> str:
    """渲染 Markdown 报告字符串。

    - `verdict`：`price_compare.rank` 的输出（可附 `is_demo` 等标志）；
    - `meta`：`{store_name, store_code, take_way, query_time, is_demo, split_plan?}`。
    """
    meta = meta or {}
    is_demo = bool(meta.get("is_demo") or verdict.get("is_demo"))
    ranked: List[Dict[str, Any]] = verdict.get("ranked") or []
    cheapest: Optional[Dict[str, Any]] = verdict.get("cheapest")
    excluded: List[Dict[str, Any]] = verdict.get("excluded") or []
    identity_conflict = bool(verdict.get("identity_conflict"))
    query_time = meta.get("query_time", "—")

    lines: List[str] = []
    lines.append("# 麦金卡省钱方案（只读试算）")
    lines.append("")
    if is_demo:
        lines.append("> ⚠️ **样例数据**：当前为离线 demo 演示，非真实 MCP 调用（RL-19）。")
        lines.append("")

    # 🔴RL-23：身份折扣冲突 → 不给省额数字、不下优劣结论（裁定口径 B）
    if identity_conflict:
        lines.append("> ⚠️ **RL-23 身份折扣冲突**：检测到其他身份折扣，与麦金卡不叠加，"
                     "无法判断两条路径相对优劣 —— 以下仅为各路径**实测实付**对照，**不给省额数字**。")
        lines.append(">")
        # 裁定口径 B 第 3 条：**必须**带这条警告，否则用户会自行比大小（而我们判定不可比）
        lines.append("> ⚠️ **两条路径的实付不可直接比较** —— 身份折扣与麦金卡为"
                     "**替换**而非**叠加**关系（带卡价可能反而更高）。")
        lines.append(">")
        lines.append("> 因此本单**不给出推荐方案**；各路径实付均为其自身实测值，请您自行判断或"
                     "前往官方渠道核对。")
        lines.append("")

    # 标题随「是否可作优劣结论」变化（RL-23）
    lines.append("## 实测实付对照（不作优劣结论）" if identity_conflict else "## 最省方案")
    lines.append("")
    if cheapest:
        src = _SOURCE_LABEL.get(cheapest.get("source"), cheapest.get("source") or "—")
        lines.append(f"- **方案名**：{cheapest.get('label') or cheapest.get('path_id')}")
        lines.append(f"- **商品明细**：{_items_str(cheapest)}")
        lines.append(f"- **优惠来源**：{src}")
        lines.append(f"- **实付金额**：{_yuan(cheapest.get('payable_cents'))}"
                     f"（= calculate-price 分 ÷ 100）")
        lines.append(f"- **省钱额**：{_saving_str(cheapest)}（{_saving_detail_str(cheapest)}）")
    elif identity_conflict:
        # 裁定口径 B 第 3 条：不标 cheapest、不出现任何「推荐 / 最优」措辞
        lines.append("- **方案名**：—（身份折扣冲突，两条路径不可比 → 不给出推荐方案）")
        lines.append("- **实付金额**：见下方「候选对比」中各路径的实付【元】（均为各自实测值，**不可相互比较**）")
        lines.append("- **省钱额**：—（口径不完整，不给数字 · RL-23）")
    else:
        lines.append("- **方案名**：—（无可用方案，见下方降级说明）")
    lines.append(f"- **取餐方式 / 门店**：{meta.get('take_way', '—')} @ "
                 f"{meta.get('store_name', '—')}（{meta.get('store_code', '—')}）")
    # 🔴RL-26：结果时点性 —— 查询时间戳必带
    lines.append(f"- **查询时间**：{query_time}（**结果只代表该时点**；"
                 f"价格以官方最终结算为准 · RL-26）")
    lines.append("")

    # 冲突时**不**用「实付升序」措辞（升序暗示可排序比较），且不出 tie-break 择优选言
    lines.append("## 候选对比（各路径实测实付 · 不可比较）" if identity_conflict
                 else "## 候选对比（实付升序）")
    lines.append("")
    lines.append("| 路径 | 方案 | 实付【元】 | 省钱额【元】 | 来源 | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    for i, rec in enumerate(ranked, 1):
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            i,
            rec.get("label") or rec.get("path_id"),
            _yuan(rec.get("payable_cents")),
            _saving_str(rec),
            _SOURCE_LABEL.get(rec.get("source"), rec.get("source") or "—"),
            "（含随单购，已剔除）" if rec.get("bundle_stripped") else (rec.get("note") or "—"),
        ))
    if not ranked:
        lines.append("| — | — | — | — | — | 无成功算价路径 |")
    lines.append("")
    if excluded:
        lines.append("**已排除路径（不给数字）**：")
        for rec in excluded:
            lines.append(f"- `{rec.get('path_id')}`：{rec.get('exclude_reason') or '未成功算价'}")
        lines.append("")

    # 🟡 临期券告警（≤1 天到期，置顶提示优先使用）
    expiring = meta.get("expiring_coupons") or verdict.get("expiring_coupons") or []
    if expiring:
        lines.append("> ⏳ **临期券告警（≤1 天到期，建议优先使用）**")
        for c in expiring:
            lines.append(f"> - {c.get('name') or c.get('couponId')}（券号 {c.get('couponId')}）："
                         f"{c.get('valid_to') or c.get('raw') or '到期时间未知'}")
        lines.append("")

    # 🔴RL-25：外送费用单列（配送费 / 打包费 / 餐具费不得混入商品价）
    fee = meta.get("fee_breakdown")
    is_delivery = _looks_delivery(meta, verdict)
    if fee:
        goods = fee.get("goods_cents")
        d_fee = fee.get("delivery_fee_cents")
        pack = fee.get("packing_fee_cents")
        table = fee.get("tableware_fee_cents")
        total = fee.get("total_cents")
        lines.append("## 外送费用单列（🔴RL-25：费用不混入商品价）")
        lines.append("")
        lines.append("| 项目 | 金额【元】 | 说明 |")
        lines.append("|---|---|---|")
        lines.append(f"| 商品小计 | {_yuan(goods)} | 仅商品，不含任何费用 |")
        lines.append(f"| 配送费 | {_yuan(d_fee)} | {fee.get('delivery_note') or '—'} |")
        lines.append(f"| 打包费 | {_yuan(pack)} | — |")
        lines.append(f"| 餐具费 | {_yuan(table)} | — |")
        lines.append(f"| **合计实付** | {_yuan(total)} | 商品价与费用**分列** |")
        lines.append("")
        if fee.get("free_delivery_applied") is True:
            lines.append("> ✅ 麦金卡「免配送费」权益**已计入**本外送方案（RL-25 / #70）。")
        else:
            lines.append("> ℹ️ 麦金卡「免配送费」权益**未计入**（未达门槛或该门店不适用）—— "
                         "请以算价返回的实际配送费为准。")
        lines.append("")
    elif is_delivery:
        # 🔴RL-25：外送但**未取到**费用明细 → 必须显式标注，且**不得**声称「已单列」（NE-20）
        lines.append("> ⚠️ **外送场景**：本单**未取到**费用明细数据（配送费 / 打包费 / 餐具费），"
                     "无法单列展示 —— 请以算价返回的**实际配送费为准**（🔴RL-25）。")
        lines.append("")

    if verdict.get("tie_break_note") and not identity_conflict:
        lines.append(f"> {verdict['tie_break_note']}")
        lines.append("")

    for note in verdict.get("notes") or []:
        lines.append(f"> {note}")
    if verdict.get("notes"):
        lines.append("")

    # 拆单方案（如提供）
    split_plan = meta.get("split_plan")
    if split_plan:
        lines.append("## 拆单方案（H1：一单一券）")
        lines.append("")
        lines.append(f"- 共 **{split_plan.get('total_orders')}** 单。")
        for o in split_plan.get("orders", []):
            c = o.get("coupon") or {}
            lines.append(f"- 第 {o.get('order_index')} 单：用券「{c.get('name') or '—'}」"
                         f"，凭证商品 {o.get('voucherProductCode')}")
        if split_plan.get("dropped_coupons"):
            lines.append(f"- 未用券 {len(split_plan['dropped_coupons'])} 张（超出拆单上限）。")
        lines.append("")
        for b in split_plan.get("boundaries", []):
            lines.append(f"> 边界：{b}")
        lines.append("")

    lines.append("## 支付")
    lines.append("")
    lines.append("- 官方支付链接：（仅在下单场景由官方渠道返回；**本技能不代下单**）")
    lines.append("- **本技能为只读试算，不代下单、不代领券、不代抽奖（RL-01）。**")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("### 提示")
    lines.append("")
    # RL-26 文案带**实际查询时点**（结果时点性，🔴RL-26）
    notes_map = dict(_FOOTNOTES)
    notes_map["RL-26"] = (f"本结果代表 **{query_time}** 的试算时点；价格 / 活动 / 券库存随时变动，"
                          f"**以官方最终结算为准**（RL-26）。")
    for code in pick_footnotes({**verdict, "is_demo": is_demo,
                                "has_fee": bool(meta.get("fee_breakdown")),
                                "delivery": is_delivery}):
        lines.append(f"- 〔{code}〕{notes_map.get(code, '')}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("report.py self-test")
    verdict = {
        "ranked": [
            {"path_id": "pathB_coupon", "label": "路径B·门店券", "source": "coupon",
             "payable_cents": 2100, "total_saving_cents": 500,
             "goods_discount_cents": 0, "coupon_saving_cents": 500, "items": [{"name": "巨无霸", "quantity": 1}]},
            {"path_id": "pathA_goldcard", "label": "路径A·卡专享", "source": "goldcard",
             "payable_cents": 2800, "total_saving_cents": 3000,
             "goods_discount_cents": 3000, "coupon_saving_cents": 0, "items": [{"name": "四件套", "quantity": 1}]},
        ],
        "excluded": [{"path_id": "pathB_bundle", "exclude_reason": "RL-04：含随单购附加付费项，已排除"}],
        "cheapest": None,
        "notes": ["RL-04：…"],
        "bundle_seen": True,
        "is_demo": True,
    }
    verdict["cheapest"] = verdict["ranked"][0]
    meta = {"store_name": "样例门店", "store_code": "10000000", "take_way": "到店自取",
            "query_time": "2026-10-09T22:30:00+08:00", "is_demo": True}
    md = render(verdict, meta)
    for token in ("## 最省方案", "实付金额", "省钱额", "候选对比", "## 支付", "样例数据"):
        check(token in md, f"报告含『{token}』")
    check("¥21.00" in md and "¥28.00" in md, "实付金额以元展示")
    check("〔RL-01〕" in md and "〔RL-19〕" in md and "〔RL-04〕" in md, "红线脚注按命中附加")
    notes = pick_footnotes(verdict)
    check("RL-19" in notes and "RL-04" in notes and "RL-18" in notes, "pick_footnotes 命中正确")
    # v1.1.0：通用脚注必带（RL-24 只读取舍 / RL-26 时点性 / RL-18 / RL-01）
    check("RL-24" in notes and "RL-26" in notes, "RL-24/RL-26 为必带通用脚注")
    check("2026-10-09T22:30:00+08:00" in md, "RL-26 脚注带实际查询时点")

    # ---- v1.1.0：RL-23 身份折扣冲突 → 不给省额数字 ----
    v_id = {
        "ranked": [
            {"path_id": "pathX_identity", "label": "路径X·员工卡", "source": "goldcard",
             "payable_cents": 1950, "total_saving_cents": 650, "goods_discount_cents": 650,
             "coupon_saving_cents": 0, "identity_conflict": True, "saving_incomplete": True,
             "items": [{"name": "巨无霸", "quantity": 1}]},
            {"path_id": "pathA", "label": "路径A·卡专享", "source": "goldcard",
             "payable_cents": 2800, "total_saving_cents": 3000, "goods_discount_cents": 3000,
             "coupon_saving_cents": 0, "items": [{"name": "四件套", "quantity": 1}]},
        ],
        "excluded": [], "cheapest": None, "notes": ["RL-23：检测到其他身份折扣…"],
        "identity_conflict": True,
    }
    v_id["cheapest"] = v_id["ranked"][0]
    md_id = render(v_id, {"store_name": "S", "store_code": "1", "take_way": "到店自取",
                          "query_time": "2026-10-09T23:00:00+08:00"})
    check("实测实付对照" in md_id and "不作优劣结论" in md_id,
          "RL-23：标题改为『实测实付对照（不作优劣结论）』")
    check("不给数字" in md_id, "RL-23：省额不给数字")
    check("〔RL-23〕" in md_id, "RL-23：脚注附加")
    check("¥19.50" in md_id, "RL-23：实付（真实事实）仍给出")

    # ---- v1.1.0：临期券告警 ----
    md_exp = render(verdict, dict(meta, expiring_coupons=[
        {"couponId": "E2", "name": "临期券", "valid_to": "2026-10-10T21:00:00"}]))
    check("临期券告警" in md_exp and "E2" in md_exp, "临期券告警行渲染")

    # ---- v1.1.0：RL-25 外送费用单列 ----
    md_fee = render(verdict, dict(meta, fee_breakdown={
        "goods_cents": 3900, "delivery_fee_cents": 0, "packing_fee_cents": 100,
        "tableware_fee_cents": 0, "total_cents": 4000,
        "free_delivery_applied": True, "delivery_note": "麦金卡免配送费已生效"}))
    check("外送费用单列" in md_fee and "配送费" in md_fee and "打包费" in md_fee and "餐具费" in md_fee,
          "RL-25：配送/打包/餐具费单列")
    check("免配送费" in md_fee and "已计入" in md_fee, "RL-25：麦金卡免配送费权益显式计入")
    check("〔RL-25〕" in md_fee, "RL-25：脚注附加")
    check("¥39.00" in md_fee and "¥1.00" in md_fee, "RL-25：费用与商品价分列展示")

    # ---- NE-20：外送但**无**费用明细 → 必须显式标注，且**不得**声称「已单列」----
    v_delivery = {
        "ranked": [{"path_id": "t_d", "label": "外送", "source": SOURCE_COUPON,
                    "payable_cents": 2100, "total_saving_cents": 500,
                    "goods_discount_cents": 500, "coupon_saving_cents": 0,
                    "items": [{"name": "巨无霸", "quantity": 1}]}],
        "excluded": [], "cheapest": None, "notes": [], "delivery": True,
    }
    v_delivery["cheapest"] = v_delivery["ranked"][0]
    md_nofee = render(v_delivery, {"store_name": "s", "store_code": "1",
                                   "take_way": "麦乐送",
                                   "query_time": "2026-10-09T22:30:00+08:00"})
    check("未取到" in md_nofee and "实际配送费为准" in md_nofee, "NE-20：费用缺失 → 显式标注『未取到』")
    check("已单列" not in md_nofee, "NE-20：无费用表时**不得**声称『已单列』（误导性声明）")
    check("外送费用单列" not in md_nofee, "NE-20：无费用数据不出费用表")
    check("〔RL-25〕" not in md_nofee, "NE-20：无费用数据不附加 RL-25 脚注")
    # 到店自取（verdict 不带 delivery 标记）→ 不该出现任何费用段提示
    md_instore = render(dict(v_delivery, delivery=False),
                        {"store_name": "s", "store_code": "1", "take_way": "到店自取",
                         "query_time": "2026-10-09T22:30:00+08:00"})
    check("未取到" not in md_instore and "配送费" not in md_instore,
          "非外送场景不出现费用段提示（无误报）")

    print("report.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
