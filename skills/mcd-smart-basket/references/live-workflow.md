# 真实 MCP 查询流程

## 前置条件与现状

用户在 [官方 MCP 平台](https://open.mcd.cn/mcp) 登录、阅读相关协议并激活 Token。Token 仅保留本机。2026-10-16 已完成真实认证、35个工具发现、门店/菜单/详情/券查询及11个整单计价。Token 仅在本机私有目录。公开接入文档不包含完整参数 schema，应使用认证后的工具发现结果。

为便于后续查询，可在项目根目录双击 `连接麦当劳.command`，或运行 `python3 skills/mcd-smart-basket/scripts/mcp_readonly.py setup`。它隐藏输入 Token，先验证官方工具列表，再以 0600 权限保存到 `private/mcd-token`；后续脚本自动读取。这个步骤由用户本人操作，不需要把 Token 发到聊天。删除本地文件即可移除凭据，撤销 Token 则在官方平台操作。

优先用宿主已有的 MCP 连接。若无法连接，附带的只读客户端支持初始化、工具发现、JSON/SSE 响应、会话头和分页，仅发送至固定官方端点。它是调试桥接工具，不是完整通用 MCP SDK；不实现 OAuth、自动重连或后台订阅。2026-10-16 已通过真实初始化、工具发现与地址查询验证；JSON/SSE 分支都有本地测试。

## 只读客户端

以下命令在仓库根目录执行，响应写入被 Git 忽略的私有目录。终端会隐藏输入 Token；已有 `MCD_MCP_TOKEN` 环境变量时直接读取，不需要写到命令行或配置仓库。

```sh
mkdir -p private
python3 skills/mcd-smart-basket/scripts/mcp_readonly.py tools > private/tools.json
```

读取 `private/tools.json` 中目标工具的 `inputSchema`，按要求生成本地参数文件。不要使用网上示例冒充当前 schema。通用调用形式：

```sh
python3 skills/mcd-smart-basket/scripts/mcp_readonly.py call query-meals private/query-meals-args.json > private/menu-response.json
```

每次运行客户端会建立新会话。401 时重新获取 Token；429 时停止并稍后重试。不通过密集重试绕过限流。真实响应可能含个人信息，仅保留本机，公开演示应使用独立脱敏副本。

## 两条场景路径

| 阶段 | 到店自取 | 外送 |
| --- | --- | --- |
| 时间 | `now-time-info`，确认立即或预约 | 同左 |
| 位置 | 用户给定地点 → `query-nearby-stores` | `delivery-query-addresses`，用户确认已有地址 → `delivery-query-stores` |
| 菜单 | `query-meals`，完整读取分页 | 在外送门店与外送方式下重新查询 |
| 套餐 | 对相关套餐调用 `query-meal-detail`，固定合法组件及替换规格 | 同左，不能复用自取的价格或套餐资格 |
| 券 | `query-store-coupons`，按需 `query-my-coupons` | 同左，验证外送适用性 |
| 比价 | `calculate-price` 核算整单 | 使用该地址查店得到的 beCode，加用餐方式，确认配送/包装费用 |

工具名称来自[官方接入指南](https://github.com/M-China/mcd-mcp-server)，但参数始终从实时 schema 获取。查询返回中的分页、营业时间、可售时段、券条件都必须处理。地址新增不在本 Skill 范围；没有已有地址时让用户在官方渠道添加。

## 从真实返回到购买建议

1. 保存官方原始响应到 `private/`，按 `data-contract.md` 规范生成 `snapshot.json`。整理相关套餐和合法选项；同一种食品与规格的身份应一致。保留官方商品编号、配置、券引用，方便回填核价参数。
2. 生成候选：

   ```sh
   python3 skills/mcd-smart-basket/scripts/basket.py enumerate private/snapshot.json > private/search.json
   ```

3. 从候选中优先选择单点基准、若干便宜方案和配送费门槛附近的方案，默认本轮最多核价 20 个。对每个候选以官方 schema 构造完整参数，执行：

   ```sh
   python3 skills/mcd-smart-basket/scripts/mcp_readonly.py call calculate-price private/price-args-001.json > private/price-response-001.json
   ```

4. 检查工具级与业务级成功，检查返回食品/规格/数量、券与请求一致。把实际整单结果映射到 `quotes.json`，其中每个 `evidence_ref` 对应真实响应文件。**餐品与套餐配置仍由 Agent 整理。quote_live.py 已支持自动构造计价请求并核对结果，CLI 不会自动从自然语言完成整个链路。**
5. 生成可解释的买法：

   ```sh
   python3 skills/mcd-smart-basket/scripts/basket.py rank private/snapshot.json private/search.json private/quotes.json
   ```

6. 两种模式分别执行。跨模式比较时说明门店可能不同、外送总价含费用，自取不含出行时间和交通成本。无法核价则不展示为可买的推荐。

## 参赛前的真实验收

- 到店：选择真实门店，完成菜单/套餐/券查询，核算单点基准及至少两个不同买法。
- 外送：在用户确认的已有地址下重复，并展示官方费用明细。
- 保存脱敏后的调用证据和真实演示，记录时间、工具名称、核价候选数量、限制；不能用当前模拟演示代替。
- 检查公开文件无 Token、手机号、地址、券码或账号私密标识。
- 完成后更新根目录的 `MCP_INTEGRATION.md` 状态，再发布并报名。无需为了验证创建真实订单。

## 自动核价入口（已按真实响应验证）

完成真实 snapshot 和 search 后，可用 `python3 skills/mcd-smart-basket/scripts/quote_live.py private/snapshot.json private/search.json --out private/live-quotes` 自动核价。开始前验证快照时效，优先单点基准与刚好买齐，每次最多20个方案；网络或格式异常时停止并保留已完成的原始响应。输出仅存 private/，共享时另做脱敏材料。

context 中外送须含 be_code（来自已选地址的配送门店），预约时须提供 reservation_date（官方 yyyy-MM-dd HH:mm 格式）。official_selection 包含从详情绑定的 productCode、roundList、默认或用户指定 modification，券商品同时包含 couponId/couponCode 并与 coupon_usage 一致。
