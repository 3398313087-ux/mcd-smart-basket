# 麦当劳 MCP 接入说明

## 当前验证状态

**2026-10-16 已完成真实自取与外送查询、券查询和整单计价。** 用户在官方平台激活 Token 后，以北京海淀镇餐厅完成认证、35个工具发现、已有地址查店、自取门店查询、两种菜单和套餐详情查询，并完成11个官方整单报价。只查询和计价，创建订单数量为0。

自取：14个候选，核价6个，刚好买齐最低¥47.50，单点基准¥61.00。外送：13个候选，核价5个，刚好买齐最低¥65.20（含配送费¥6.00、包装费¥2.70），单点基准¥76.20。两种场景全部4种刚好买齐候选均核价；剩余未核价方案为额外食品组合。

历史报价见 [脱敏证据](examples/live-price-evidence.json) 和 [自取](examples/live-pickup-results.txt) / [外送](examples/live-delivery-results.txt)测试记录，购买前必须重新查询。本轮只覆盖相关套餐配置与现有两张券，不宣称全店最优。原始响应和凭据仅留在 `private/`，不公开。

修复了 macOS Python 缺少 CA 根证书的问题，使用系统 CA 并保留主机名与证书校验。客户端同时检查 MCP 层和 `structuredContent.success` 业务状态。

## 服务与能力

官方远程服务：`https://mcp.mcd.cn`，Streamable HTTP，认证使用 `Authorization: Bearer`。Token 从本机 `MCD_MCP_TOKEN`、用户主动保存的 `private/mcd-token` 或隐藏终端输入读取，配置示例仅含环境变量占位符。

计划调用工具如下，实际参数以服务 `tools/list` 的 `inputSchema` 为准：

| 工具 | 本项目用途 |
| --- | --- |
| `now-time-info` | 用餐时段和查询时间 |
| `query-nearby-stores` | 查找自取门店 |
| `delivery-query-addresses` | 读取用户已有地址供选择 |
| `delivery-query-stores` | 查询该地址可配送门店 |
| `query-meals` | 各场景当前在售菜单 |
| `query-meal-detail` | 套餐组件及可替换配置 |
| `query-store-coupons` / `query-my-coupons` | 查已有券及门店适用性 |
| `calculate-price` | 验证完整购物车及优惠，返回实付和费用 |

上述工具中实际使用了地址、配送门店、自取门店、菜单、详情、门店券和计价；now-time-info 与 query-my-coupons 本轮未额外调用，查询时间由官方业务响应 datetime 提供。

## 调用流程与业务价值

用户食品清单 → 确认用餐方式与门店/地址 → 获取菜单、套餐详情和已有券 → 规范化为固定食品组合 → 本地枚举可行买法 → 官方整单计价 → 按实付排序 → 输出可照着购买的清单。

到店自取与外送分开查询和计价。外送把配送与包装费用纳入官方总额，不能仅按菜单小计算便宜。组合计算减少用户逐个点开套餐的操作，官方计价负责最终可用性；多买方案独立展示，保留用户对原食品清单的控制。

代码入口：`skills/mcd-smart-basket/scripts/mcp_readonly.py`（只读桥接）和 `basket.py`（枚举/排序）。可运行演示：`examples/demo.py`（完全模拟、无网络请求）。

完整接入步骤见 [live-workflow.md](skills/mcd-smart-basket/references/live-workflow.md)，中间格式见 [data-contract.md](skills/mcd-smart-basket/references/data-contract.md)。当前由宿主 AI Agent 依据实时 schema 转换请求/响应。已核实：自取 orderType=1、beType=1 且不传 beCode；外送 orderType=2、beType=2，beCode 来自已有地址对应的配送门店。calculate-price 不接收 addressId；地址通过先前查店所得的 beCode 和本地快照绑定。成功报价的 price、deliveryPrice、packingPrice 等金额字段单位为分，已通过11个真实响应验证其与商品金额、配送/包装费用的加总关系。

## 自动计价适配

`mcd_adapter.py` 从规范化 offer 的 `official_selection` 构造完整商品与 roundList，明确区分自取/外送；以官方回显的主商品编号及数量核对请求。真实外送响应会把“打包服务费”放入 productList，适配器将其作为费用而非食品，并核验 `price = productPrice + deliveryPrice + packingPrice + tablewarePrice`，避免费用重复计算。未知附加商品、数量变动或总额不符时拒绝自动采纳。

`quote_live.py` 可对新鲜快照自动完成最多20个候选计价、私有证据保存与排序。餐品身份、规格和合法套餐配置仍由 Agent 根据官方菜单与详情整理，当前并非独立的自然语言网站应用。报价响应不回显每个套餐子项；该部分按原始请求及详情核对，不能宣称完成响应级子项校验。

```sh
python3 skills/mcd-smart-basket/scripts/quote_live.py private/snapshot.json private/search.json --out private/live-quotes
```

不得把历史快照的时间手动改新来绕过过期校验。先重新查询菜单、券和相关详情，再构造新快照。官方工具 schema 变更时需重新核对适配。

不调用 `create-order` 等交易工具。Token、地址、手机号、券码和追踪编号均不进入公开证据，模拟数据与真实数据独立。

参考：[官方接入文档](https://github.com/M-China/mcd-mcp-server) · [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)。
