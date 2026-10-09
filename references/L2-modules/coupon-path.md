# L2 · 路径 B（普通优惠）

> 目标：构建**真的能用**的券候选集，逐张真实算价取最低。

## 输入 / 产出
- 输入：`storeCode`、`orderType`、`beType`、时段（`ctx`）。
- 产出：`stage_s4_coupons.json`（券 0 也须落盘）。

## 步骤
1. **取门店可用券**：`query-store-coupons({storeCode, orderType, beType})` —— **不传 `reservationDate`**（传了返回空）。
2. **过滤**（`coupon_filter.py`）：按 `couponId`/`couponCode` 去重；按门店/时段/渠道做可用性过滤（早餐券在非早餐时段剔除）；构建"券-凭证商品码"映射。
3. **逐张算价**：对每张可用券，把券绑定商品入单并传 `couponId`+`couponCode` 调 `calculate-price`（**一单一券**默认）→ 取最低。
4. **拆单（H1 定论后启用）**：`split_order.py` 把一车拆为多单、**每单各用 1 张券**，逐单试算后汇总总成本；
   须明示拆单边界（最多拆 N 单、跨单不可共享门槛/满减）。**不再**做券组合枚举（原 `candidate_pruner.py` 已删除）。

> **H1 定论**：同一订单最多 1 张券（传 2 张报 `600022`）→ 组合枚举**物理不可行**。

## 失败分支
- 券集为空 / 接口失败 → 落盘空数组 + `reason`，输出"本单暂无可用券"，仍给卡路径/原价路径。
- 算价报 600022 / 600012 → 归因（一单一券 / 券须绑定凭证码）。

## 硬约束
- 🔴RL-03：**只来自 `query-store-coupons`**；`query-my-coupons` 仅展示。
- 🔴RL-08：省钱额只取 `discount`，不用划线价。
- 🔴RL-09：查券不传 `reservationDate`。
