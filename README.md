# 麦金卡省钱选择器（mcd-goldcard-saver）- 针对性适用于尊贵的麦金卡用户的省钱选择工具 Skill，解决麦金卡用户对于拥有麦金卡及非麦金卡优惠两种不同情况下如何选择最省钱的问题。

> 给**麦当劳麦金卡（O 麦金会员）**持卡人算「这一单怎么点最省」：把「**卡权益路径 A**」与
> 「**普通优惠路径 B**（门店券 / 活动 / 凑单 / 拆单）」放在**同一把标尺**上真实试算比价，
> 输出**最省方案 + 官方支付链接**。

**一句话**：判断归 LLM，数据归脚本；成功看落盘文件，失败走降级阶梯；分叉留两手，红线不可越。

---

## 🔴 只读声明（一票否决）

本技能**全程只读**，止步于「方案 + 官方支付链接」：

- **禁止**调用 `create-order` / `auto-bind-coupons` / `draw-lottery` / `mall-create-order` /
  `party-order-create` / `cancel-order` 及一切写操作。
- 不代下单、不代领券、不代抽奖；领券 / 下单 / 抽奖请您自行在官方渠道完成。

> ⚠️ **诚实定位**：脚本内的 `whitelist.py` 是「**调用前策略辅助 + 违规检测**」，
> **不是架构级强制拦截**（技能脚本无法拦截 Agent 直接发起的 MCP 调用）。
> 真正的只读强制依赖 **`SKILL.md` 硬规则（RL-01）**（+ 平台 Hook / 连接器权限层，如适用）。
> 我们**不宣称**「脚本已封死写操作」。

---

## 快速上手

### 方式一：离线 demo（无需 Token，推荐先跑通）

```bash
python scripts/demo.py --demo
```

仅用 `scripts/demo_data/` 的**样例** JSON 跑通全链路（S0→S7），产出：

```
.goldcard/snapshots/demo/
├── stage_s0_context.json      # 上下文装配
├── stage_s1_membership.json   # 持卡确认（H4：无法自证）
├── stage_s2_meals.json        # 菜单建模（元，字符串）
├── stage_s3_goldcard.json     # 卡权益判定（路径 A）
├── stage_s4_coupons.json      # 券候选集（路径 B）
├── stage_s5_prices.json       # 多路径试算（分）
├── stage_s5b_split_plan.json  # 拆单规划（H1 一单一券）
├── stage_s6_verdict.json      # 比价与硬校验
├── stage_s7_report.md         # 只读交付报告
└── sentinel_done              # 落盘判定：成功证据 = 文件存在
```

> 报告中的门店 / 商品 / 券**全部为样例数据**，会显式标注「样例 · 非真实 MCP 调用」（RL-19）。

### 方式二：接入 `mcd-mcp` 连接器（真实比价）

1. 在连接器管理中**信任 `mcd-mcp`**；
2. 通过环境变量注入 Token（**禁止明文落盘**，🔴RL-11）：

   ```bash
   export MCD_MCP_TOKEN="<your-token>"     # Windows: set MCD_MCP_TOKEN=<your-token>
   ```

3. 参考 `mcp_servers.json` / `mcp-config.example.json`（**仅占位符**）配置 MCP；
4. 用触发语开始，例如：「我是麦金卡用户，午餐想点个大堡口福套餐，怎么点最省？」

---

## 运行依赖

- **Python 3.8+**，**仅标准库**（零第三方依赖，可直接 clone 运行）。
- 真实比价需 `mcd-mcp` 连接器（麦当劳开放平台 MCP）。

---

## 目录结构

```text
mcd-goldcard-saver/
├── SKILL.md                 # 技能入口（L1 总览：触发 + 漏斗 + 红线摘要 + 阶段索引）
├── README.md / LICENSE / CHANGELOG.md
├── MCP_INTEGRATION.md       # 35 工具清单 + 已知文档偏差 + 错误码表
├── mcp_servers.json         # MCP 声明（Authorization 仅环境变量占位符）
├── mcp-config.example.json  # 官方必交文件（仅占位符）
├── scripts/                 # 数据区（零依赖）：一切确定性数值/筛选/排序/换算/限流
│   ├── money.py             # 三源单位归一为分 + 单位不符显式抛错（RL-02/RL-20）
│   ├── envelope.py          # 响应解包：说明文字+JSON、success 判定、注入隔离（RL-05/16）
│   ├── coupon_filter.py     # 券候选集：仅来自 query-store-coupons（RL-03）+ 有效期 + 券型归一化
│   ├── goldcard_rules.py    # 卡权益判定：以单品 discountType 为主（RL-15）+ 身份折扣守卫入口
│   ├── identity_guard.py    # 🆕 身份折扣守卫：enjoyed 双关分类（RL-23，无真机样本 → 保守降级）
│   ├── price_compare.py     # 逐张券取最低 + 分列省额 + 剔随单购（RL-04/08/20/23）
│   ├── split_order.py       # 拆单器（H1 一单一券 → 每单 1 券，替代 candidate_pruner）
│   ├── rate_limiter.py      # 串行 120ms + 缓存 + 算价预算 12 次（RL-13）
│   ├── whitelist.py         # 只读策略辅助 + 违规检测（RL-01，非架构级强拦）
│   ├── guards.py            # 入参守卫：RL-09/10/12/22 数据层确定性断言
│   ├── trace_log.py         # 结构化 Trace（JSONL，字段稳定）+ 成本画像
│   ├── selfcheck.py         # 入场自检 + 提交前校验
│   ├── report.py            # 报告装配（按 assets/report-template.md）
│   ├── demo.py              # 离线 demo 入口（--demo）
│   └── demo_data/           # 离线样例数据（显式标注「样例」）
├── references/              # 知识库（公理Ⅰ：目标是体量 > scripts/；当前实际 28.7KB < scripts 127.6KB，见「待办」）
│   ├── L1-overview.md       # L1 总览（默认预载）
│   ├── L2-modules/          # L2 模块详情（按需加载）
│   ├── L3-semantic-bridge.md# L3 语义桥（执行阶段强制参考）
│   ├── tool-contract.md     # 工具契约 SSOT
│   ├── red-lines.md         # 红线清单（27 条；v1.1.0 增 RL-23~RL-27）
│   └── assumptions.md       # 假设台账 H1–H16（H8–H16 标【待验证假设】）
├── assets/
│   └── report-template.md
└── .goldcard/               # 运行期产物（.gitignore 忽略）
```

---

## 工作原理（决策流水线）

| 阶段 | 动作 | 关键工具 | 产出（落盘） | 退出标准 |
|---|---|---|---|---|
| S0 | 上下文装配 | `now-time-info`/`query-nearby-stores` | `stage_s0_context.json` | 含 `storeCode/beType/orderType/timeSlot` |
| S1 | 持卡确认 | `query-my-account`（尽力） | `stage_s1_membership.json` | `membership_confirmed ∈ {true,false}` |
| S2 | 菜单建模 | `query-meals` | `stage_s2_meals.json` | 商品非空 + 含 `code` + 价档 |
| S3 | 卡权益判定（路径 A） | 本地 `goldcard_rules.py` | `stage_s3_goldcard.json` | 逐条 `{available, reason}` |
| S4 | 券候选集（路径 B） | `query-store-coupons` | `stage_s4_coupons.json` | 券 0 也落盘（空数组 + reason） |
| S5 | 多路径试算 | `calculate-price`（串行+缓存+预算） | `stage_s5_prices.json` | 每候选路径一条记录 |
| S6 | 比价与硬校验 | `price_compare.py`/`money.py` | `stage_s6_verdict.json` | 实付升序 + 单位校验 + 剔随单购 |
| S7 | 只读交付 | `report.py` | `stage_s7_report.md`+`sentinel_done` | 报告字段齐全 + sentinel 存在 |

---

## 关键口径（防 100 倍误差）

| 概念 | 口径 | 单位 |
|---|---|---|
| **实付金额** | `calculate-price` 的 `price`/`productPrice` ÷ 100 | 分 → 元 |
| **省钱额** | **唯一口径** = `calculate-price.discount`（商品自身优惠）+ `enjoyed`（券后省额），**分列展示** | 分 → 元 |
| **菜单划线价** | `query-meals.originalPrice`（元，字符串）——**仅展示，不参与省额** | 元（字符串） |
| **`enjoyable`/`balance`** | 「再买 X 元可享 Y 优惠」的**门槛提示**，**非已生效折扣** → 不计入到手价 | 元 / 分 |
| **`enjoyed`（双关，🆕RL-23）** | 可能是①券省额（**可计入**）或②**身份折扣**（员工卡等，与麦金卡**替换非叠加** → **不计入**、不给省额数字）。先过 `identity_guard` 分类，再决定能否计数 | 分 / 元 |
| **券有效期（🆕）** | `validTo`/`endTime`/`expireDate`/… → 过期剔除；**≤1 天标临期**（置顶告警）；格式不可解析 → 保守保留 + `validity_unknown` | — |
| **券型（🆕）** | 归一为 8 类（含积分商城 `bundle_price`）；**未知型保守保留 + `type_unknown`**，抵扣额**只能实算**，禁止本地臆造算法 | — |
| **外送费用（🆕RL-25）** | 配送费 / 打包费 / 餐具费**单列**，不混入商品价；麦金卡免配送费权益显式计入 | 分 → 元 |
| **随单购** | 默认**不勾选**；含附加付费项且无法拆分者**排除、不报数** | — |
| **卡费（口径）** | **已持卡 ⇒ 沉没成本，不进单笔边际比较**；若需计入，从算价卡费行 `subtotal` **动态读取**（不写死 ¥19），跨单**只扣一次** | 分 → 元 |

三源单位统一在 `money.py` 归一到**分（int）**；**任一来源单位与声明不符即显式抛错**，禁止静默换算。

---

## 💳 卡费摊销与边际节省（**差异化核心 · 必读**）

### 定位：面向**已持卡**用户，只做「这一单怎么点最省」

| | 本技能 `mcd-goldcard-saver` | 「麦回本 mc-breakeven」#60 |
|---|---|---|
| **问题** | 这一单怎么点最省？ | 要不要开卡 / 多久能回本？ |
| **用户状态** | **已持卡** | 未持卡 / 犹豫是否开卡 |
| **卡费处理** | **沉没成本，不摊销进单笔决策** | 核心变量（要算回本周期） |
| **输出** | 两条路径同标尺比价 → 最省方案 | 开卡建议 + 回本测算 |

**口径（明确交代，避免两套算法混淆）**：

1. **卡费已付 = 沉没成本**。既然卡已买，它**不影响「这一单 A 路径 vs B 路径」的边际比较** ——
   两边都要付（或不付）同样的卡费，差额只由**本单**的价差决定。因此本技能
   **不做**「要不要开卡」的回本测算，那是 #60 的领域。
2. **每单决策只看边际节省**：`边际节省 = 路径A实付 − 路径B实付`（同一购物车、同一时点试算）。
3. **卡费若出现在算价里，必须动态读取**：带卡价 / 随单购场景的卡费
   **从算价返回的卡费行 `subtotal` 动态读取**（**不写死 ¥19**，不同卡种/活动价不同）。
4. **跨单汇总只扣一次**：若把卡费纳入跨单汇总（拆单场景），**只扣一次**，不得每单重复扣。
   > 注：本技能**默认不勾随单购**（🔴RL-04），故通常不会有卡费行；上述口径是为
   > 「用户主动要求把卡费算进来」时准备的，避免写死数字。

---

## 🏪 门店三级兜底（S0）

| 级别 | 手段 | 约束 |
|---|---|---|
| ① | `query-nearby-stores` + `searchType=2` + `city` + `keyword`（**两者均非空**） | 缺 city/keyword 报 `600058`；`searchType=1` 查收藏（新账号 `600050`）→ 非首选 |
| ② | 收藏门店（`searchType=1`） | 新账号可能 `600050` |
| ③ | **`order-list` 由历史订单反推 `storeCode`**（只读） | ⚠️ **只有最近 ~10 单、无分页**（srv#13）→ **仅兜底**，不作主路径 |

> #79 称第 ③ 级是「最关键的工程处理」。本技能已在 `scripts/whitelist.py` 把 `order-list`
> 归入**只读白名单**（原在「只读越界」集合，v1.1.0 移入）。

---

## 📐 解析指引与防御性解析

1. **优先 `structuredContent`**，取不到再用 `content[0].text` 兜底（#16/#79/#99）。
2. **空数据中文文案 ≠ 接口失败**：接口无数据时可能返回「暂无可用优惠券」这类**中文文案**而非空数组
   → `envelope.unwrap()` 返回 `success=True, data=[], code="EMPTY_TEXT", _empty=True`。
   （本账号 `query-my-coupons` 实测即返回「暂无可用优惠券」，**必踩路径**。）
3. **防御性解析**：`query-meals` 结构随门店/版本漂移（#79 实测可能只返回 `code`+`tags`，无名称/价格）
   → 缺字段走**降级标注**而非崩溃；价格缺失的 SKU 不参与比价。

---

## ⚖️ 合规声明

- 仅基于麦当劳官方 MCP 返回的**只读数据**试算，**不与其他品牌对比**。
- 输出**不构成营养 / 健康建议**（营养咨询请交 `list-nutrition-foods` / 专业人士）。
- **不涉及**抽奖 / 赌博导向（不调 `draw-lottery` / `query-lottery-info`）。
- 价格 / 活动 / 券库存随时变动 → **以官方最终结算为准**（🔴RL-26）。
- 只读取舍的代价已显式声明：未领取的可领券**不在候选内**，实付可能被**系统性高估**（🔴RL-24）。

---

## 已知限制与诚实声明（**必读**）

来自真机压测（`docs/05-probe-report.md`）的**诚实边界**：

1. **H1 一单一券（已验证）**：同一订单最多 1 张券（传 2 张报 `600022`）。因此本技能走
   **逐张券单独算价取最低 + 拆单**，**不做**券组合枚举。⚠️ 「有效单券的正向折扣金额」因账号
   名下 0 张券未实测，属 **missing evidence**。
2. **H2 卡权益（部分已验证）**：权益**内置在卡专享 SKU**（`discountType="麦金卡优惠"`），
   下单即自动出 `discount`；普通商品不自动套卡；**无独立卡来源字段**。
   ⚠️ **无「非持卡对照 token」** → 无法证明「卡专享价是持卡才享有」，**不排除该价对所有人公开**。
   **不得**作为已验证事实宣传。
3. **H3 结构化判定（已验证）**：判定以**单品 `discountType`** 为主，分类名仅作补充
   （跨分类 SKU 如 `9900016311` 落在「人气热卖」但仍是麦金卡优惠，按分类会漏判）。
4. **H4 持卡无法自证（已验证不成立）**：`query-my-account` **无卡状态字段** →
   采用「**用户自述 + 运行时推断**」，**不宣称「已确认您持有麦金卡」**。
5. 门店已打烊（`600057`）→ 用 `businessStatus`/`businessEndTime` 预筛。
6. 价格随活动/门店/时段变动，**以官方页面为准**。
7. **v1.1.0 新增规则中，H8–H12、H15、H16 属【待验证假设】**（本账号无真机样本，来源为外部调研交叉比对）：
   `enjoyed` 身份折扣语义、券有效期字段、券型枚举、外送费用字段、随心配自选池、
   `structuredContent` 优先、菜单结构漂移。实现一律走**保守降级**（认不出即标 `unknown`、
   保守保留或不计数），**严禁**当作已验证事实宣传。

---

## 可靠性 & 可测性

- **输入分层**：正常 / 边界（追问，不猜）/ 异常（降级阶梯 L0–L5 + 红线话术）。
- **不静默吞异常**：所有分支落到「用户可理解反馈」或「明确降级结果」。
- **幂等**：只读无写副作用；缓存键 = 工具名 + 归一化入参 sha256，重复执行结果一致。
- **限流**：串行 + 最小间隔 120ms + 缓存 + 算价预算 12 次/会话；仅 429/瞬时错误重试（max 2, base 1.5s），业务错误不重试。
- **Trace**：`.goldcard/traces/<session>.jsonl`，字段稳定（`ts/session/stage/event/tool/args_digest/cache_hit/ok/code/elapsed_ms/reason/cost`）。
- **成本画像**：`trace_log.cost_summary()` 汇总 Token / 工具次数 / 延迟 p50/p95/p99 / 失败重试率。
- **落盘判定**：每阶段产出文件 + `sentinel_done`；支持断点续跑与基线快照。

---

## 归因记录（Prior-Art：adapt / keep / reject）

源自 §6.2（先例研究 11 类候选）：

| 来源 | 决策 | 说明 |
|---|---|---|
| WorkBuddy 内置 `mcdonalds` 技能（MIT） | **adapt** | 借其取餐方式/必填参数/支付链接交付/合规骨架；**止于下单、无比价、无卡权益** → 只借壳 |
| #116 麦麦省粮管家（mcd-saver） | **adapt** | 保留「多价格档位 + top-K 精排」思路；补齐「卡权益为一等路径」 |
| #70 mcd-no-pickle | **keep(事实)+adapt(做法)** | 保留「一单一券(600022)」「随单购不勾」「降级阶梯」 |
| 官方站点/会员资料（mcdonalds.com.cn 等） | **keep（领域事实）** | 卡权益清单与门槛；规则随活动/门店变动，须标注查询时点 |

---

## 待办（诚实列出，未伪造）

- `assets/_icon.png`：技能图标**尚未生成**（当前仓库无该文件）。发布前需补一张 PNG 图标。

---

## License

[MIT](LICENSE) 。
