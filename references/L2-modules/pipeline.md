# L2 · 决策流水线（阶段·动作）

> 主流程 8 阶段。每阶段：**输入 → 产出（落盘文件）→ 退出标准（机器可校验）**。后阶段输入 = 前阶段产出，禁止跳步取数。
> 全量细节与失败分支见 `docs/02-design.md` §3。

| 阶段 | 动作 | 关键工具 | 产出文件 | 退出标准 |
|---|---|---|---|---|
| S0 | 上下文装配 | `now-time-info`、`query-nearby-stores`/`delivery-query-stores` | `stage_s0_context.json` | 含 `storeCode/beType/orderType/timeSlot` |
| S1 | 持卡确认 | `query-my-account`（尽力） | `stage_s1_membership.json` | `membership_confirmed ∈ {true,false}`；false 则暂停比价 |
| S2 | 菜单建模 | `query-meals`（+`query-meal-detail`/兜底） | `stage_s2_meals.json` | 商品非空，含 `code` + 价档 |
| S3 | 卡权益判定（路径 A） | 本地 `goldcard_rules.py` | `stage_s3_goldcard.json` | 逐条 `{available,reason}` |
| S4 | 券候选集（路径 B） | `query-store-coupons`（不传 `reservationDate`） | `stage_s4_coupons.json` | 券 0 也落盘（空数组+reason） |
| S5 | 多路径试算 | `calculate-price`（串行+缓存+预算） | `stage_s5_prices.json` | 每候选路径一条记录 |
| S6 | 比价与硬校验 | `price_compare.py`/`money.py` | `stage_s6_verdict.json` | 实付升序 + 单位校验 + 剔随单购 |
| S7 | 只读交付 | `report.py` | `stage_s7_report.md`+`sentinel_done` | 报告字段齐全 + sentinel 存在 |

## 数据流

```text
用户口述 ─S0─► (storeCode,beType,orderType,timeSlot)
        ├─ S2 菜单(元) ──► S3 卡权益规则(路径A) ─┐
        └─ S4 门店券 ─ coupon_filter(路径B) ─────┤
                                                ▼
                    S5 calculate-price(分) ─串行/缓存/预算─► S6 money/discount/剔随单购 ─► S7 只读交付
```
