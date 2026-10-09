# Changelog

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)（SemVer）。
每段采用 `Added / Changed / Deprecated / Removed / Fixed / Security` 六段式；
**不兼容变更**以 `**BREAKING**` 前缀标注，供发布门禁扫描。

---

## [1.0.0] - 2026-10-09

首个公开发布版本（技能代号 `mcd-goldcard-saver`）。基于真机压测（H1–H4 定论）定稿。

### Added
- **决策流水线 S0–S7**（落盘判定，每阶段产出 `stage_*.json` + `sentinel_done`）。
- **数据区脚本（零第三方依赖，纯标准库）**：
  - `money.py`：三源单位（菜单元字符串 / 算价分整数 / `enjoyable.realDiscount`元·`balance`分）统一归一到**分**，单位不符显式抛错。
  - `envelope.py`：剥离「说明文字 + JSON」混合体、以 `success` 判定成败、注入隔离。
  - `coupon_filter.py`：券候选**仅来自 `query-store-coupons`**，去重 + 时段/门店/渠道过滤 + 券-凭证码映射。
  - `goldcard_rules.py`：卡权益以单品 `discountType` 为主判定（可捕获跨分类 SKU），含 RL-17 身份折扣守卫。
  - `price_compare.py`：逐张券取最低、**分列**「商品自身 discount」与「券后省额」、剔除随单购、RL-02/08/20 硬校验。
  - `split_order.py`：**拆单器**（H1 一单一券 → 每单 1 券），给出拆单边界声明。
  - `guards.py`：**入参守卫**（把红线落成数据层确定性断言）——RL-09 查券不带 `reservationDate`、RL-10 门店 `searchType=2`+city+keyword、RL-12 `beCode` 规则、RL-22 打烊预筛。
  - `rate_limiter.py`：串行 120ms + 缓存 + 算价预算 12 次；仅 429/瞬时错误重试。
  - `whitelist.py`：只读策略辅助 + 违规检测（**非架构级强拦**，见 README 诚实声明）。
  - `trace_log.py`：结构化 Trace（JSONL）+ 成本画像。
  - `selfcheck.py`：入场自检（8 项）+ `--pre-commit` 校验。
  - `report.py`：按 `assets/report-template.md` 装配报告。
  - `demo.py --demo`：离线 demo 全链路（样例数据，标注 RL-19）。
- **知识库**：`references/L1-overview.md`、`L2-modules/*`、`L3-semantic-bridge.md`、`tool-contract.md`、`red-lines.md`（22 条）、`assumptions.md`（H1–H7）。
- **发布门禁产物**：`README.md`、`LICENSE`（MIT）、`MCP_INTEGRATION.md`、本文件、`mcp-config.example.json`、`.gitignore`。

### Changed
- 卡权益判定由「分类名 / 商品名启发式」升级为**结构化判定**（以单品 `discountType` 为主，分类名补充）。
- 金额口径扩展为**三源**归一（ADR-12），`enjoyable` 门槛提示不计入到手价（RL-20）。
- `SKILL.md` 版本由 `0.1.0`（骨架）升至 `1.0.0`。

### Removed
- **BREAKING** `scripts/candidate_pruner.py`（券组合枚举 / top-K 剪枝）——真机压测已定论 **H1 一单一券**
  （传 2 张券报 `600022 暂不支持多张券使用`），组合枚举**物理不可行**且与只读红线冲突。
  其位置由 `scripts/split_order.py`（拆单器）承接：改为「一车拆多单、每单 1 券」。
  > 迁移影响：输出结构由「组合候选」变为「逐张券/拆单候选」，`stage_s5_prices.json` 的 `paths[]`
  > 语义随之变化（见下 `Changed`）。

### Fixed
- 修正 party 系列工具名拼写：`query-party-store-date`（连字符），非文档所写 `query-partystore-date`。
- 门店预筛：用 `businessStatus`/`businessEndTime` 规避打烊门店 `600057`（RL-22）。
- `demo_data/meals_sample.json`：补齐 `discountType` 结构化字段，并**刻意保留**两类反例（`tags:["麦金卡"]+discountType=null`、`discountType="早餐卡优惠"`）与「随单购麦金卡优惠」样例。
- `demo_data/price_sample.json`：重生成使四条路径满足**自洽三式**（Σsubtotal==price / Σ(originalSubtotal−subtotal)==discount / 带-不带随单购差额==withOrderCents）。
- `whitelist.py`：语义修正——把 `mall-order-*` 等**只读但越界**工具从「写操作」集合拆到 `OUT_OF_SCOPE_TOOLS`（保守拒绝方向不变，表述更准确）。
- 文档一致性（严把关 P1-1）：`docs/02-design.md` 的 ADR-01 / §1 / §4 / §12.2 已就地改为「脚本层前置守卫 + 留痕；架构级不可达依赖 SKILL.md 硬规则 + 平台 Hook」，与 §14.2 保持一致。

### Security
- Token **仅经环境变量注入**（`mcp_servers.json` / `mcp-config.example.json` 仅占位符），仓库无明文（RL-11）。
- 工具返回一律视为**纯数据**，注入文本中和且不执行（RL-05）。

---

## [1.1.0] - 2026-10-09

基于**外部调研交叉比对**（麦当劳 MCP 大赛踩坑总结 + 服务端侧记）的增补版本。
**无破坏性变更**（`BREAKING` 段落为空）；新增红线编号为兼容变更。

### Added

**两项 P0 修复**

- **`scripts/identity_guard.py`（新）· 🔴RL-23 身份折扣守卫**：`calculate-price` 的 `data.enjoyed`
  **语义双关**（既可能是券省额，也可能是**身份折扣**，且身份折扣与麦金卡是**替换而非叠加**）。
  - `detect_identity_discount()`：命中身份关键词（员工卡/员工餐/内部/家属卡/亲情卡/staff/employee/identity）
    或 `type`/`promotionName` 指向非券类权益 → `conflict=True`；
  - `coupon_saving_cents()`：冲突时券省额**恒 0**（**不得**计入），券类 `enjoyed` 通道**保留**；
  - `price_compare.from_calculate_price()`：冲突路径标 `identity_conflict` + `redline=RL-23` +
    `saving_incomplete=True`，`rank()` 汇总为 `identity_conflict`/`comparison_unreliable`；
  - `report.render()`：冲突时标题改「实测实付对照（**不作优劣结论**）」，**省额不给数字**（实付仍给）；
  - `goldcard_rules.detect_identity_conflict()` / `evaluate_with_guards(calc_responses=...)`：
    合并**菜单侧 RL-17** 与**算价侧 RL-23** 两类信号。
  - ⚠️ **无本账号真机样本**（真机三次试算 `enjoyed` 恒缺省）→ 记 `assumptions.md` **H8【待验证假设】**，
    代码注释写明「未经真机取证，走保守降级」。
- **`scripts/envelope.py` · 空数据中文文案识别（P0-B）**：接口无数据时返回**中文文案**而非空数组
  （本账号 `query-my-coupons` 实测返回「暂无可用优惠券」）。新增 `looks_empty_text()`；命中且无法解析 JSON 时
  返回 `success=True, data=[], code="EMPTY_TEXT", msg=<原文>, _empty=True`，**不再**误报 `PARSE_ERROR`。

**5 条新红线（RL-23 ~ RL-27，红线总数 22 → 27）**

- **RL-23** 🔴：身份折扣不得当券省额计入 / 未检测互斥（上文）。
- **RL-24** 🔴：只读取舍声明 —— 本技能禁用 `auto-bind-coupons`（写操作）⇒ **未领取的可领券不在候选内**
  ⇒ 实付可能被**系统性高估**，必须显式声明（报告**必带**该脚注）。
- **RL-25** 🔴：外送**运费/打包费/餐具费单列**，不得混入商品价；麦金卡「免配送费」权益须显式计入。
- **RL-26** 🟡：结果**时点性** —— 报告必带查询时间戳 + 「价格以官方最终结算为准」（脚注文案带实际时点）。
- **RL-27** 🟡：**随心配**只能按默认搭配算价（官方未暴露自选商品池）→ 注明「此价为默认组合价，实际可自选」。

**实现增强**

- `scripts/coupon_filter.py`：
  - **券有效期** `parse_validity()` —— 解析 `validTo`/`endTime`/`expireDate`/`expire`/`endDate`（含秒/毫秒时间戳）：
    过期→剔除并给 reason；**≤1 天 → `expiring_soon=True`**（报告置顶告警）；格式不可解析 → **保守保留** +
    `validity_unknown=True`（🔴RL-07 不臆造）。
  - **券型归一化** `normalize_coupon_type()` —— 归为 8 类（含积分商城 `bundle_price`）；未知型 → 保守保留 +
    `type_unknown=True`，且 `no_local_deduction=True`（**抵扣额只能靠 `calculate-price` 实算，禁止本地臆造算法**）。
  - `filter_coupons_with_report()` 额外返回**剔除清单**（逐条 reason，不静默丢弃）；
    `expiring_coupons()` / `unknown_type_coupons()` 供报告与落盘使用。
- `scripts/report.py`：新增 RL-23~RL-27 话术；RL-26 脚注带**实际查询时点**；临期券告警行；
  外送费用单列表（RL-25）；身份冲突时省额不给数字。
- `scripts/whitelist.py`：`order-list` 由「只读越界」移入**只读白名单** —— 供 S0 **门店三级兜底**第 3 级
  （历史订单反推 `storeCode`）使用；⚠️ 限制（srv#13）：**只有最近 ~10 单、无分页**，仅兜底。
- `scripts/selfcheck.py --pre-commit`：新增 g/h 两组门禁 —— 校验 `identity_guard` / `envelope.looks_empty_text` /
  `coupon_filter.parse_validity|normalize_coupon_type` 存在（数据层断言，非仅提示词），并校验 `order-list` 放行。
- `scripts/demo_data/`：`coupons_sample.json` 扩 5 张券覆盖新分支（过期 / 临期 / 格式未知 / `bundle_price` /
  未知券型），有效期以 `@now±Nd/Nh` 相对标记表达（demo 运行时解析，保证可重复跑通）；
  新增 `price_sample_identity.json`（🔴RL-23 构造样例，**保持自洽三式**）。
- `scripts/demo.py`：新增 **S5x 身份折扣守卫**独立场景（落盘 `stage_s5x_identity_guard.json`，不污染主比价）；
  券候选打印有效期/券型标记与剔除 reason；报告 meta 带 `expiring_coupons` / 查询时点。

**文档增补（README / SKILL / references 三处一致）**

- **「卡费摊销与边际节省」专章**（README）：面向**已持卡**用户 → 卡费属**沉没成本**，每单只看**边际节省**；
  **不做**「要不要开卡」的回本测算（显式写出与 #60「麦回本 mc-breakeven」的领域切分）；
  口径：卡费须从算价**卡费行 `subtotal` 动态读取**（不写死 ¥19），跨单汇总**只扣一次**。
- **门店三级兜底**（README / SKILL / L3 / L1）：`searchType=2`(city+keyword) → 收藏 → `order-list` 反推，
  标注 **~10 单 / 无分页** 限制。
- **合规声明**（README）：不与其他品牌对比；不构成营养/健康建议；不涉抽奖/赌博导向；价格以官方结算为准。
- **解析指引**（README / SKILL / L3）：优先 `structuredContent`，`content[0].text` 仅兜底。
- **防御性解析**（README / SKILL / L3）：`query-meals` 结构漂移 → 缺字段走降级标注而非崩溃。
- `references/assumptions.md`：新增 **H8–H16**，其中 H8–H12/H15/H16 标 **【待验证假设】**，
  H14（空中文文案）标 **已验证（本账号实测）**；并说明 `feature_flags` 只解析 H1–H7、
  H8–H16 已**硬编码保守分支**（有意不做成可切换开关，防误切换后虚报数字）。
- `references/red-lines.md`：新增 RL-23~RL-27 条目 + RL-23 判定/降级表；计数 22 → 27（🔴16 + 🟡10 + 阶段内 1）。
- `references/L3-semantic-bridge.md`：新增 11 条映射（enjoyed 双关、身份折扣处置、券有效期、券型、
  外送费用、随心配、空文案、解析优先级、菜单漂移、门店三级兜底等）。
- `references/L1-overview.md`：新增 S5x 阶段行 + v1.1.0 新红线速览 + 临期阈值/兜底常量。

### Changed

- 报告脚注**必带**集合扩展为 `RL-26 / RL-24 / RL-18 / RL-01`（与命中无关）。
- `pick_footnotes`：`identity_conflict` 同时附加 RL-23（算价侧）与 RL-17（菜单侧）。
- 版本 `1.0.0` → `1.1.0`（`SKILL.md` frontmatter / `CHANGELOG.md` / `docs/02-design.md` 三处一致）。

### Fixed

- **P0-B**：空数据中文文案（如「暂无可用优惠券」）由 `PARSE_ERROR` 误报修正为
  `success=True, data=[], code="EMPTY_TEXT"`（本账号**必踩路径**）。
- **P0-A**：`price_compare._coupon_saving_cents()` 原**无条件**把 `enjoyed.discountCents` /
  `enjoyed.realDiscount` 当券省额计入 —— 账号带员工卡时会把身份折扣当成「本单省了多少」，
  并吞掉「与麦金卡互斥」的事实。现经 `identity_guard` 分类后才决定是否计入（🔴RL-23）。
- `goldcard_rules._self_identity_discount()` 原**只扫菜单 meals、不扫算价响应** —— 现由
  `detect_identity_conflict()` 合并两侧信号，算价侧缺口补齐。

### Fixed（v1.1.0 评审后补修 · 齐回归 NE-03 / NE-14 / NE-20 / NE-04）

> 这 4 条是 `04-eval` 断言跑批暴露的**真实缺口**，全部修复在 **1.1.0 发布前**，
> 故**不另开版本号**（1.1.0 尚未发布，按 SemVer 折叠进同一版本的 `Fixed` 段落）；
> 若团队口径要求单独记一次补丁发布，可拆为 `1.1.1`。

- **NE-03 两个身份折扣判定入口结论相反**：菜单侧 `goldcard_rules._self_identity_discount`
  的本地枚举含 `partner优惠`，而算价侧 `identity_guard` 的词表命中不了它 → 同一折扣在菜单链路
  「触发 RL-17 不给数字」、在算价链路「当券省额计入 500 分」。
  修复：枚举**单一来源** —— `identity_guard` 新增 `MENU_IDENTITY_DISCOUNT_TYPES` +
  `is_identity_discount_type()`（精确集合 + 关键词），`goldcard_rules` **不再本地维护枚举**，
  改为调用该函数；`classify_enjoyed()` 先逐类型字段精确判定再走整体关键词。
  同时补 `partner` 关键词（保守取向：少报 > 虚报）。两模块各自新增一致性单测（7 个身份名 + 3 个反例）。
- **NE-14 「单品特价」未被券型规则表覆盖**：`_COUPON_TYPE_RULES` 新增
  `("item_special", ("单品特价","特价","item_special","special_price","sku_price"))`
  （置于 `direct_reduce` 之后、`exchange` 之前，避免与既有 8 类冲突）。未知券型仍保守保留。
- **NE-20 无费用数据却声称「费用已单列」（误导性声明）**：`report` 新增 `_looks_delivery()`；
  `RL-25` 脚注**仅在真有 `fee_breakdown` 时**附加；外送但费用明细缺失时改为输出显式标注
  「本单**未取到**费用明细数据…请以算价返回的**实际配送费为准**」，且**不出**费用表。
- **NE-04 模块 docstring 与实现自相矛盾**：`identity_guard` docstring 原写
  「分类不清时**按身份折扣处理**」，而 `classify_enjoyed` 对不可辨类型返回 `indeterminate`
  并**按券通道计入**（带留痕）。已按**代码为准**改写 docstring：
  「券词与身份词**同时命中**才按身份处理；类型不可辨 → `indeterminate` + 按券通道计入**并**留待复核痕」。

### Removed

- 无（`candidate_pruner.py` 已于 1.0.0 移除并记 BREAKING，本版本无新增破坏性变更）。

### Security

- 不变：Token 仅经环境变量注入（RL-11）；工具返回视为纯数据（RL-05）；**只读**红线 RL-01 依旧
  且新增 RL-24 显式声明其**代价**（实付可能被高估）。

---

## 不兼容点声明（供门禁扫描）

| 变更 | 类型 | 说明 |
|---|---|---|
| 删除 `candidate_pruner.py` → 新增 `split_order.py` | **BREAKING**（1.0.0 已生效） | H1 定论后，候选构成由「券组合」改为「逐张券 / 拆单」 |
| `stage_s5_prices.json` 的 `paths[]` 语义变化 | **BREAKING**（1.0.0 已生效） | 由组合候选变为逐张券 + 拆单候选记录 |
| 红线编号新增 RL-23 ~ RL-27 | 兼容 | 新增编号属兼容；删除/改号属 BREAKING |
| `order-list` 由「越界」移入「只读白名单」 | 兼容（放宽） | 仅放宽，不收紧；用于 S0 门店三级兜底 |
| `verdict` 新增字段（`identity_conflict` / `comparison_unreliable` / `identity_reasons`） | 兼容（新增字段） | 旧消费方忽略新字段不受影响 |
| `report` 在 `identity_conflict` 时标题/省额展示变化 | 兼容（条件分支） | 仅在身份折扣冲突时触发 |
| `L3` 语义桥 `【待验证】` → 确定映射 | 兼容 | 字段名回填 |

| 变更 | 类型 | 说明 |
|---|---|---|
| 删除 `candidate_pruner.py` → 新增 `split_order.py` | **BREAKING** | H1 定论后，候选构成由「券组合」改为「逐张券 / 拆单」 |
| `stage_s5_prices.json` 的 `paths[]` 语义变化 | **BREAKING** | 由组合候选变为逐张券 + 拆单候选记录 |
| 红线编号新增 | 兼容 | 新增编号属兼容；删除/改号属 BREAKING |
| `L3` 语义桥 `【待验证】` → 确定映射 | 兼容 | 字段名回填 |
