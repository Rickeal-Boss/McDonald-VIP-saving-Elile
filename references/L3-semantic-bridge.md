# L3 · 语义桥（执行阶段**强制参考**，禁止绕过硬编码）

> 术语 / 外部系统概念 → 工程实现的精确映射表。凡标 `【待验证】` 者，运行期先探测再取对应分支（见 `docs/02-design.md` §10）。
> 证据：`【实测 schema】`= 已核验工具 schema；`【一手】`= 踩坑总结；`【待验证】`= 未验证。

| 业务术语 | 工程实现映射（精确到字段/工具） | 证据 |
|---|---|---|
| **实付金额【元】** | `calculate-price` 返回的 `price`/`productPrice`（分）÷ 100；另有 `originalPrice`(分)/`productOriginalPrice`(分) | 【真机实测】单位为分 |
| **省钱额【元】** | `calculate-price` 返回的 `discount`（分）÷ 100；**唯一口径**。语义＝商品本身优惠额（划线价−现价），**不含券** | 【真机实测】+【一手 §2.2】 |
| **已享受的优惠（券）** | `enjoyed` 字段（券/活动生效时出现；本次实测恒缺省＝无券被享受） | 【真机实测】 |
| **🆕 `enjoyed` 语义双关（易误用）** | `enjoyed` 可能是①**券/活动**省额（可计入），也可能是②**身份折扣**（员工卡/内部/家属卡/亲情卡/staff/employee/identity，与麦金卡**替换而非叠加**）。**必须先分类再计数**：命中身份关键词或 `type/promotionName` 指向身份类 → `identity_guard.detect_identity_discount()` 返回 `conflict=True` → 券省额**恒 0**、路径标 `identity_conflict`+`saving_incomplete`、**不给省额数字**（🔴RL-23）。券类 `promotionName` 不匹配身份关键词时，**券省额通道保留** | 【外部 #60 实测 · 无测试账号样本 H8】 |
| **🆕 身份折扣冲突的处置** | 报告标题改「实测实付对照（不作优劣结论）」；实付（事实）仍给，省额给「—」；脚注 RL-23 + RL-17 | 【设计定稿 · 依据 H8】 |
| **🆕 券有效期** | `validTo`/`endTime`/`expireDate`/`expire`/`endDate`（或秒/毫秒时间戳）→ `coupon_filter.parse_validity()`：过期剔除并给 reason；**≤1 天→`expiring_soon=True`**（报告置顶告警）；格式不可解析→**保守保留**+`validity_unknown=True`（🔴RL-07 不臆造） | 【外部 #34 · 无测试账号样本 H9】 |
| **🆕 券型（≥7 种）** | `coupon_filter.normalize_coupon_type()` 归一为 `full_reduce / discount_rate / direct_reduce / exchange / free_delivery / bundle_price(积分商城) / gift / nth_discount`；**未知券型→保守保留+`type_unknown`**，且 `no_local_deduction=True` —— 抵扣额**只能**由 `calculate-price` 实算，**禁止**本地实现满减/折扣算法 | 【外部 #9/#127 · 无测试账号样本 H10】 |
| **🆕 外送费用（单列）** | 配送费 / 打包费 / 餐具费**独立于商品价**单列展示；麦金卡「免配送费」权益须显式计入并注明是否满足门槛（🔴RL-25） | 【外部 #70/#21/#135 · 无测试账号样本 H11】 |
| **🆕 随心配** | 官方未暴露可自选商品池 → **只能按默认搭配算价**；涉及时注明「此价为默认组合价，实际可自选」（🟡RL-27） | 【srv#9 · 无测试账号样本 H12】 |
| **🆕 空数据文案** | 接口「无数据」时可能返回**中文文案**（如 `query-my-coupons` →「暂无可用优惠券」）而非 `[]` → `envelope.looks_empty_text()` 识别后返回 `success=True, data=[], code="EMPTY_TEXT", _empty=True`；**不得**报 `PARSE_ERROR` | 【测试账号实测 · 已验证 H14】 |
| **🆕 响应解析优先级** | 优先取 `structuredContent`；`content[0].text` 仅作兜底（🟡RL-16 类偏差） | 【外部 #16/#79/#99 · 无测试账号样本 H15】 |
| **🆕 菜单结构漂移** | `query-meals` 结构随门店/版本变化（可能只返回 `code`+`tags`，无名称/价格）→ **防御性解析**：缺字段走降级标注，不崩溃；价格缺失的 SKU 不参与比价 | 【外部 #79 实测 · 无测试账号样本 H16】 |
| **🆕 门店三级兜底** | ① `query-nearby-stores` 用 `searchType=2` + city + keyword（RL-10）→ ② 收藏（`searchType=1`，新账号可能 600050）→ ③ `order-list` 由历史订单反推 `storeCode`（**只读**；⚠️ 仅最近 ~10 单、无分页 srv#13） | 【外部 #79 + srv#13 · 部分 H13】 |
| **`enjoyable`（易误用）** | `{amountType,realDiscount,balance}`＝「再买 X 元可享 Y 优惠」的**门槛提示**，**非已生效折扣**；`realDiscount` 单位=元、`balance` 单位=分；达门槛后消失 | 【真机实测】🔴RL-20 |
| **菜单划线价** | `query-meals` 的 `originalPrice`（**元且为字符串**，如 `"58"`）；**仅展示，不参与省额** | 【真机实测】 |
| **可用券** | `query-store-coupons({storeCode, orderType, beType})` 返回集；**不传** `reservationDate` | 【实测 schema】+【一手 §3.3】 |
| **我拥有的券** | `query-my-coupons()` 返回集；**仅展示**，不进候选枚举 | 【实测 schema】自述不做门店/渠道校验 |
| **可领券（勿混用）** | `available-coupons` 返回券标题，**不含 `couponId/couponCode`**，领取属写操作 → **不可用于试算** | 【真机实测】🟡RL-21 |
| **券-凭证商品码** | 券绑定专属 `productCode`；算价须把券商品入单并传 `couponId`+`couponCode`；用普通商品码报 600012 | 【一手 §3.2/§7.2 V9】 |
| **麦金卡权益价** | **结构化判定**：单品 `discountType ∈ {麦金卡优惠, 随单购麦金卡优惠}`（或分类名"麦金卡专享"作补充）；下单该卡专享 SKU 即**自动**出 `discount`（无需传卡 code）；普通商品**不**自动套卡；**无独立卡来源字段** | 【真机实测 H2/H3】 |
| **持卡判定** | 无法自证：`query-my-account` 无卡状态字段 → **用户自述 + 运行时推断**（卡专享 SKU 试算出更低价则推定）；`frequent` 命中卡 SKU 仅弱旁证 | 【真机实测 H4】 |
| **随单购** | `calculate-price` 的 `withOrder{cardId,cardType,membershipCode,membershipSpecId}`（菜单侧 `canWithOrder`(bool)）；**默认不传** | 【实测 schema】+【一手 §2.3】 |
| **到店自取** | `beType=1`，`orderType=1`，**不传** `beCode` | 【实测 schema】 |
| **麦乐送** | `beType=2`，`orderType=2`，**必传** `beCode`（来自 `delivery-query-stores`） | 【实测 schema】 |
| **得来速** | `beType=5`，`orderType=1`，**必传** `beCode`（来自 `query-nearby-stores`） | 【实测 schema】 |
| **团餐** | `beType=6`，`orderType=2`，**必传** `beCode`；卡权益口径不覆盖 | 【实测 schema】+PRD §3.1 |
| **takeWayCode** | 下单必传，**只能来自** `calculate-price`（两段式）；`takeWayList` = `eat-in/take-in-store/take-dt-quick` | 【一手 §4.3】+【真机实测】 |
| **门店定位（到店）** | `query-nearby-stores({beType, searchType:2, city, keyword})`；用 `businessStatus`/`businessEndTime` 预筛非打烊门店（防 600057） | 【实测 schema】+【真机实测】 |
| **门店定位（外送）** | `delivery-query-stores({beType, addressId})` | 【实测 schema】 |
| **时段** | 早餐 `05:00–10:29`，正餐其余；由 `now-time-info` 取得 | 【一手 §3.1】 |
| **卡权益门槛** | 免配送费(满19早/满39正餐)、满额福利金(到店满39/麦乐送满60减5)、超值四件套、6折自由搭、9.9麦咖啡 | PRD §6.2 领域事实【待复核】 |
| **促销品兜底** | `query-meal-detail` 取不到 → `query-meals` 全量兜底 | 【一手 §4.6】 |
| **成功判定** | 统一看响应 `success` 字段 | 【一手 §1.8】 |
| **响应解析** | 先剥离"说明文字 + JSON"混合再解析 | 【一手 §2.5】 |
| **特调** | 走 `roundList`（`round` 为字符串）/ `modification.values[]`（注意 `selectedKey`/`unselectedKey`） | 【一手 §1.5】+【实测 schema】 |

> **硬规则**：任何出现於本表的术语，在执行阶段**必须**按映射取值；未命中映射的术语视为未定义，**先探测/追问，不得臆造**（🔴RL-07）。
