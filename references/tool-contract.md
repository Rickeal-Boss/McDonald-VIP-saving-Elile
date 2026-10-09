# 工具契约（SSOT）

> **本文件是工具契约的单一事实源**。记录本设计阶段核验的 schema 快照、已知文档偏差、错误码表。
> 硬规则：**一切以运行时 `tools/list` 为准**；官方文档有 ≥4 处已知偏差，照文档写必翻车。

## 已核验工具签名（本设计阶段快照，证据等级【实测 schema】）

| 工具 | 入参 | 关键约束 |
|---|---|---|
| `query-store-coupons` | `storeCode`(纯数字)/`orderType`/`beType`；可选 `beCode`/`reservationDate` | 门店+订单类型维度；**查可用券不传 reservationDate** |
| `query-my-coupons` | `page`/`pageSize` | 自述"不进行门店、渠道、配送方式等下单规则校验"→ **仅展示** |
| `calculate-price` | `storeCode`/`orderType`/`beType`/`items[]`；`withOrder{}`；`needTableware`；`gmServiceCode`；`beCode`；`reservationDate` | 返回**分**；`withOrder` = 随单购；`items` 须 JSON 数组对象；`items[].roundList[].round` 为字符串 |
| `query-meals` | `storeCode`/`orderType`/`beType`；可选 `beCode`/`reservationDate` | 促销品全量兜底 |
| `query-meal-detail` | `storeCode`/`orderType`/`beType`/`code` | 促销品可能取不到 |
| `query-my-account` | **无参** | 仅返回积分字段（accountId/accumulativePoint/availablePoint/…/usedPoint）；**无卡状态/卡种/有效期**（H4 已验证不成立） |
| `query-nearby-stores` | `beType`/`searchType`/`city`/`keyword` | `searchType=2`+city+keyword；`searchType=1` 查收藏（新账号 600050）；返回 `businessStatus`/`businessEndTime` 用于**预筛非打烊门店** |
| `delivery-query-stores` | `beType`/`addressId` | 外送场景；地址来自 `delivery-query-address` |
| `available-coupons` | —— | 返回**可领券**，**不含 `couponId/couponCode`**，领取属写操作 → 不可用于试算（🟡RL-21） |
| `now-time-info` | 无参 | 取当前时间/时段 |
| `campaign-calendar` | 可选 `specifiedDate` | 日期语义与文档不符 |
| `query-meal-detail` / `query-meals` 的 `code` | 菜单编码取 `data.categories[].meals[].code` | 绝不臆造编码 |

## 关键字段语义（真机实测）

| 字段 | 语义 | 单位 |
|---|---|---|
| `query-meals.originalPrice` / `currentPrice` | 菜单划线价 / 现价 | **元（字符串）**，如 `"58"` |
| `calculate-price.productPrice`/`originalPrice`/`discount`/`price` | 现价 / 原价 / 优惠额 / 应付 | **分（整数）**，如 `2800` |
| `calculate-price.enjoyed` | 已享受的优惠（券/活动生效时出现；本次恒缺省） | —— |
| `calculate-price.enjoyable` | 「再买 X 元可享 Y 优惠」门槛提示，**非已生效折扣** | `realDiscount`=元, `balance`=分 |
| `query-meals.discountType` | 优惠类型枚举：`null / 早餐卡优惠 / 促销优惠 / 麦金卡优惠 / 随单购早餐卡优惠 / 随单购麦金卡优惠` | —— |
| `query-meals.categories[].name` | 含分类名 `麦金卡专享`（作补充信号） | —— |
| `query-meals.canWithOrder` / `withOrder{cardId,cardType,membershipCode,specId}` | 随单购机制 | —— |
| `calculate-price.takeWayList` | `eat-in` / `take-in-store` / `take-dt-quick` | —— |

> **`discount` 语义提醒**：`discount` = 商品本身优惠额（＝划线价 − 现价），**不含券**；券生效走 `enjoyed`。

## 已知文档偏差（以运行时为准）

| # | 偏差 | 处置 |
|---|---|---|
| 1 | 工具名拼写：`query-partystore-date/session` 实为 `query-party-store-date/session` | 用连字符版本 |
| 2 | 漏列工具：`query-promotions`（团餐满减）、`query-survey-coupon`（满意度奖券） | 运行时动态发现 |
| 3 | 工具数：文档 33，**真机实测 35** | 以 `tools/list` 为准，快照记录 |
| 4 | `reservationDate` 语义：文档"预约必传"，但查可用券传了返回空 | 查券不传 |
| 5 | `campaign-calendar` 返回天数与锚点语义与文档不符 | 以实际返回为准 |
| 6 | `calculate-price.items[]` 实际字段比文档更全（含 `roundList`）；`query-meals.beType` enum 实为 `1/2/5/6` | 以运行时 schema 为准 |

## 错误码表

| 错误码 | 含义 | 处置 |
|---|---|---|
| `600022` | 一单只能用一张券（**真机已验证**） | 逐张单独算价取最低，或拆单 |
| `600012` | 未找到优惠券或已赠送给好友 / 券商品码不匹配 | 用券绑定专属凭证 `productCode`；核对券有效性 |
| `600050` | 该 beType 无收藏餐厅 | 改 `searchType=2` + city + keyword |
| `600057` | 门店可能已关闭或不在营业时间 | 用 `businessStatus`/`businessEndTime` 预筛门店 |
| `600058` | `searchType=2` 时 city 或 keyword 为空 | 两个都传 |
| `429` | 限流 | 串行队列 + 缓存 + 退避，**不轰炸** |
| `401` | Token 无效/过期 | 重新申请，检查连接器授权 |

## 契约漂移自检

`scripts/selfcheck.py` 对本文件记录的工具清单与关键字段做 canonical hash；运行时 `tools/list` hash 不符 → 抛「契约漂移」警告并置本文件为**待复核**。
