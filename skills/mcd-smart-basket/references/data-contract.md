# 本地计算数据规范

本规范是项目的中间格式，**不是麦当劳 MCP 的 inputSchema**。Agent 负责依据当前官方工具 schema 和返回值转换，不能直接把此 JSON 传给 `calculate-price`。

## snapshot.json

```json
{
  "source": "live",
  "captured_at": "2026-10-15T12:00:00+08:00",
  "context": {
    "mode": "pickup",
    "store_id": "来自真实返回的门店ID",
    "currency": "CNY",
    "address_key": null,
    "service_time": "immediate"
  },
  "demand": {"burger:standard": 1, "fries:medium": 1},
  "labels": {"burger:standard": "指定汉堡", "fries:medium": "中薯"},
  "max_extra_units": 0,
  "coupon_limits": {},
  "offers": [
    {
      "id": "local-offer-1",
      "name": "指定汉堡单点",
      "items": {"burger:standard": 1},
      "price_cents": 2500,
      "max_quantity": 1,
      "coupon_usage": {},
      "exclusion_groups": [],
      "official_selection": {"note": "本机保存真实商品ID和所选配置；此示意对象不是官方参数"}
    }
  ]
}
```

示例只有一个商品且故意无法买齐需求，金额是格式示例而非实际报价。可运行的完整模拟输入由 `examples/demo.py` 构造。

字段规则：

- `source` 为 `demo` 或 `live`，不能混用。`live` 表示调用方导入了真实数据，不提供真实性签名保证。
- `captured_at` 必须带时区。`context` 中 mode 为 `pickup` / `delivery`，外送需要 `address_key`。后者是本地私有地址映射键，不要存明文地址。
- `service_time` 为 `immediate` 或包含时区的预约时间标识；不同预约时间分别查询。
- `demand` 键需编码餐品身份、规格、数量单位与特制要求。“5块鸡块”是一份餐品，不能当作五份。用户未允许的替代不能映射成同一个键。
- `items` 表示购买一次 offer 实际得到的食品。套餐所有组件都必须录入，不能隐藏赠品/额外食品。价格只供排序核价优先级，最终用整单应付额。
- `max_quantity` 为实际购买数量上限，整数 0–20。取已知库存、官方购买限制和本次搜索需要的较小值；限制不明时明确记录假设，最终交官方核价。
- 同一张券用于不同套餐配置时，`coupon_usage` 共用相同券 ID；`coupon_limits` 表示现有可用张数。券 ID 留本机，不公开。券商品不能同时再记作未用券的基础商品，避免重复计费。
- `exclusion_groups` 中每个组整单最多一个使用单位。只有已确认的“一单限一项”互斥关系才能这样建模，复杂门槛交官方计价，勿擅自简化。
- `max_extra_units` 是整单额外食品件数上限，默认 0；设置 1 允许任意额外一件，但仍不允许少买原清单中的食品。
- 可以附加 `official_selection`、`evidence_ref` 等字段存本地真实选择和查询依据，求解器不会猜测或修改官方数据。

## search.json

由 `basket.py enumerate` 输出，包括快照指纹 `snapshot_id`、`search_complete`、每个候选 `id`、`cart`、食品总数、多买数量和估计小计。每个 cart 条目只有 `offer_id` 与 `quantity`；须回查 snapshot 中的实际商品及配置再构造官方请求。指纹绑定整个快照（包括门店、方式、地址标识、时间、券），用于防止误用，不是官方签名。

默认最多访问 200,000 个搜索节点、保存 5,000 个候选。80 个 offer 是输入上限；现实组合可能提前达到搜索上限，届时必须报告候选未穷尽。先保留与目标食品及明确允许多买有关的配置，可降低搜索量。

## quotes.json

```json
{
  "source": "live",
  "snapshot_id": "从search.json原样复制",
  "quotes": [
    {
      "plan_id": "从候选方案原样复制",
      "status": "accepted",
      "total_cents": 3900,
      "delivery_fee_cents": 0,
      "packaging_fee_cents": 0,
      "quoted_at": "2026-10-15T12:01:00+08:00",
      "evidence_ref": "private/quote-response-001.json"
    }
  ]
}
```

这里的金额仍只是格式示例。只有官方返回成功并接受**完整购物车**、没有静默移除食品或优惠券时才能写 `accepted`；逐项核对官方结果。总额是最终应付金额；配送费和包装费是已包含的明细，只展示不叠加。以官方净额字段为准，不手工推算满减。

官方业务拒绝时写 `status: rejected` 和 `reason`，不需要金额；未调用的候选不要填记录。网络失败可记 `status: failed`，不能冒充官方拒绝。`evidence_ref` 必须指向本机实际原始响应；排序器检查其存在于记录中但不会联网证明或读取文件内容，Agent 必须对照原始结果核实。

重复方案报价、未知方案 ID、来源混用、快照指纹不一致会报错；过期报价排除。导入购物车重新验证需求、数量、券库存和互斥组，重新计算多买项，不信任手改的合计。
