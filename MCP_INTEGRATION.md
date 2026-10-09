# MCP 集成说明（mcd-mcp）

> 本文件记录 **`mcd-mcp` 连接器**的集成要点：工具清单、已知文档偏差、错误码表、只读边界。
> 一切**以运行时 `tools/list` 为准**；官方文档有 ≥4 处已知偏差，照文档字符串拼写必翻车。

---

## 1. 连接方式

| 项 | 值 |
|---|---|
| 连接器 | `mcd-mcp`（麦当劳开放平台 MCP） |
| 传输 | `streamablehttp` |
| 端点 | `https://mcp.mcd.cn` |
| 鉴权 | `Authorization: Bearer <TOKEN>`，**仅经环境变量注入**（🔴RL-11） |
| 环境变量 | `MCD_MCP_TOKEN` |
| 声明文件 | `mcp_servers.json`（`Bearer ${MCD_MCP_TOKEN}`，占位符）、`mcp-config.example.json`（占位符） |

> **禁止**将真实 Token 写入仓库 / 配置文件 / 报告（🔴RL-11）。仓库仅含占位符。

---

## 2. 工具清单（真机实测 **35 个**，官方文档仅列 33）

> 证据：`docs/05-probe-report.md` §2。下表「本技能白名单」列标注该工具是否被 `scripts/whitelist.py` 放行。

| # | 工具名 | 读/写 | 本技能白名单 | 用途摘要 |
|---|---|---|---|---|
| 1 | `auto-bind-coupons` | **写** | ✗ 禁止 | 一键领券（属写操作，红线拒绝） |
| 2 | `available-coupons` | 读 | ✗ | 可领券（**不含 `couponId/couponCode`**，不可试算 🟡RL-21） |
| 3 | `calculate-price` | 读 | ✓ | 试算价格（返回**分**） |
| 4 | `campaign-calendar` | 读 | ✓ | 活动日历（日期语义与文档不符） |
| 5 | `cancel-order` | **写** | ✗ 禁止 | 取消订单 |
| 6 | `create-order` | **写** | ✗ 禁止 | 下单 |
| 7 | `delivery-create-address` | **写/有副作用** | ✗ | 新建外送地址 |
| 8 | `delivery-query-addresses` | 读 | ✓ | 查询外送地址 |
| 9 | `delivery-query-stores` | 读 | ✓ | 麦乐送门店 |
| 10 | `draw-lottery` | **写** | ✗ 禁止 | 抽奖 |
| 11 | `list-nutrition-foods` | 读 | ✗ | 营养信息（超出本技能范围） |
| 12 | `mall-create-order` | **写** | ✗ 禁止 | 商城下单 |
| 13 | `mall-order-detail` | 读 | ✗ | 商城订单详情 |
| 14 | `mall-order-list` | 读 | ✗ | 商城订单列表 |
| 15 | `mall-points-products` | 读 | ✗ | 积分商品 |
| 16 | `mall-product-detail` | 读 | ✗ | 商城商品详情 |
| 17 | `now-time-info` | 读 | ✓ | 当前时间 / 时段 |
| 18 | `order-list` | 读 | ✓（v1.1.0 放行） | 订单列表 —— 用于 **S0 门店三级兜底第 3 级**：由历史订单反推 `storeCode`。⚠️ **只有最近 ~10 单、无分页**（srv#13），仅兜底、不作主路径 |
| 19 | `party-order-create` | **写** | ✗ 禁止 | 团餐下单 |
| 20 | `query-lottery-info` | 读 | ✗ | 抽奖信息 |
| 21 | `query-meal-assistance` | 读 | ✗ | 餐品协助 |
| 22 | `query-meal-detail` | 读 | ✓ | 餐品详情（促销品可能取不到 → 全量兜底） |
| 23 | `query-meals` | 读 | ✓ | 门店全量菜单 |
| 24 | `query-my-account` | 读 | ✓ | 我的积分（**无卡状态字段**，H4） |
| 25 | `query-my-coupons` | 读 | ✓ | 我的券包（**仅展示**，不进候选 🔴RL-03） |
| 26 | `query-my-prizes` | 读 | ✗ | 我的奖品 |
| 27 | `query-nearby-stores` | 读 | ✓ | 附近门店（`searchType=2`+city+keyword） |
| 28 | `query-order` | 读 | ✗ | 查询订单 |
| 29 | `query-party-city` | 读 | ✗ | 团餐城市 |
| 30 | `query-party-store` | 读 | ✗ | 团餐门店 |
| 31 | `query-party-store-date` | 读 | ✗ | 团餐门店日期 |
| 32 | `query-party-store-session` | 读 | ✗ | 团餐场次 |
| 33 | `query-promotions` | 读 | ✗ | 促销 / 团餐满减（文档漏列） |
| 34 | `query-store-coupons` | 读 | ✓ | **门店可用券（券候选唯一来源 🔴RL-03）** |
| 35 | `query-survey-coupon` | 读 | ✗ | 满意度奖券（文档漏列） |

> 说明：白名单只放行**本技能实际需要**的只读工具；其余只读工具（如 `query-promotions`）当前保守拒绝，
> 如需启用请更新 `scripts/whitelist.py` 的 `READONLY_TOOLS` 并记入 `CHANGELOG.md`。

---

## 3. 已知文档偏差（**以运行时为准**）

| # | 偏差 | 处置 |
|---|---|---|
| 1 | 工具名拼写：文档写 `query-partystore-date/session`，**实为** `query-party-store-date` / `query-party-store-session`（连字符分段） | **一律用连字符版本**，不要照文档字符串拼写 |
| 2 | 数量差异：文档列 **33**，真机实测 **35**（+2） | 以 `tools/list` 为准；具体多出哪两个未逐条证实，**不推测** |
| 3 | 漏列工具：`query-promotions`、`query-survey-coupon` | 运行时动态发现 |
| 4 | 入参 schema 比文档更全：`calculate-price.items[]` 支持 `productCode / quantity / couponId / couponCode / modification{values[]} / roundList[]` | 以运行时 schema 为准 |
| 5 | `query-meals.beType` 实际 enum 为 `1/2/5/6` | 以运行时 schema 为准 |
| 6 | `reservationDate` 语义：文档称「预约必传」，但**查可用券传了返回空** | **查可用券不传** `reservationDate`（🔴RL-09） |
| 7 | `campaign-calendar` 返回天数 / 锚点语义与文档不符 | 以实际返回为准 |

---

## 4. 错误码表

| 错误码 | 含义 | 处置 |
|---|---|---|
| `600012` | 未找到优惠券或已赠送给好友 / 券商品码不匹配 | 用券绑定专属凭证 `productCode`；核对券有效性 |
| `600022` | **一单只能用一张券**（真机已验证，H1 定论） | 逐张券单独算价取最低，或**拆单**（每单 1 券） |
| `600050` | 该 `beType` 无收藏餐厅（`searchType=1`） | 改 `searchType=2` + city + keyword |
| `600057` | 门店可能已关闭或不在营业时间 | 用 `businessStatus`/`businessEndTime` 预筛门店（RL-22） |
| `600058` | `searchType=2` 时 city 或 keyword 为空 | 两个都传 |
| `429` | 限流（官方约束约 600 次/分钟） | 串行队列 + 缓存 + 退避，**不轰炸** |
| `401` | Token 无效 / 过期 | 重新申请，检查连接器授权 |

---

## 5. 关键字段语义（真机实测）

| 字段 | 语义 | 单位 |
|---|---|---|
| `query-meals.originalPrice` / `currentPrice` | 菜单划线价 / 现价 | **元（字符串）**，如 `"58"` |
| `calculate-price.productPrice`/`originalPrice`/`discount`/`price` | 现价 / 原价 / 优惠额 / 应付 | **分（整数）**，如 `2800` |
| `calculate-price.enjoyed` | 已享受的优惠（券/活动生效时出现） | —— |
| `calculate-price.enjoyable` | 「再买 X 元可享 Y 优惠」门槛提示，**非已生效折扣**（🔴RL-20） | `realDiscount`=元, `balance`=分 |
| `query-meals.discountType` | 优惠类型枚举：`null / 早餐卡优惠 / 促销优惠 / 麦金卡优惠 / 随单购早餐卡优惠 / 随单购麦金卡优惠` | —— |
| `query-meals.categories[].name` | 含分类名 `麦金卡专享`（作补充信号） | —— |
| `query-meals.canWithOrder` / `calculate-price.withOrder{...}` | 随单购机制（默认不传，🔴RL-04） | —— |
| `calculate-price.takeWayList` | `eat-in` / `take-in-store` / `take-dt-quick` | —— |

> **`discount` 语义提醒**：`discount` = 商品本身优惠额（＝划线价 − 现价），**不含券**；券生效走 `enjoyed`。

---

## 6. 只读边界（🔴RL-01）

本技能**只读**，仅调用上表「白名单 ✓」工具。**禁止**：`auto-bind-coupons` / `create-order` /
`cancel-order` / `draw-lottery` / `mall-create-order` / `party-order-create` / `delivery-create-address` 等。

> 校核口径：`scripts/whitelist.py` 在技能脚本链路内做**前置守卫 + 违规检测**，
> 但**不是**架构级强制拦截；真正的只读强制依赖 `SKILL.md` 硬规则（RL-01）（+ 平台 Hook 层，如适用）。
