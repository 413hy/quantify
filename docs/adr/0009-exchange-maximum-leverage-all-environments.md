# ADR 0009: 所有环境使用交易所最大杠杆

- 状态：Accepted
- 日期：2026-07-15
- 决策者：Owner（当前会话明确更正原开发文档）

## 更正

Owner 明确声明原文档中的项目 10 倍硬上限是错误需求。Testnet、Shadow 和未来 Production
均不得施加项目自定义杠杆倍数上限；目标策略是约 1 USDT 保证金，并使用 Binance 对当前
账户、币种和名义仓位实际允许的最高初始杠杆。

“没有上限”在实现上定义为 `EXCHANGE_MAXIMUM`，不是无穷大或固定 125 倍。125 只是当前
USDⓈ-M 杠杆修改参数的协议边界；实际值必须来自最新 `leverageBracket`，并受账户资格、
名义仓位阶梯和交易所规则约束。bracket 缺失、过期或响应不一致时拒绝新增仓位。

## 实现影响

- 风险配置从 `hard_caps` 和 `configured_limits` 中彻底移除杠杆项目上限，改为
  `leverage_policy.selection=EXCHANGE_MAXIMUM`；125 仅记录为交易所 API 参数边界；
- TradePlan 使用 `selected_initial_leverage`，同时强制 `EXCHANGE_MAXIMUM`、bracket 哈希、
  观察时间和最大证据年龄，不再使用会暗示项目上限的 `leverage_cap`；
- 风险倍率继续缩放止损、总风险、日损和回撤预算，不再缩放交易所选择的初始杠杆；
- Python 数量计算、Testnet 能力探针及杠杆修改接口接受交易所协议范围；
- Testnet 与 Production 的强制端点清单同时包含 bracket 查询和初始杠杆修改；
- 生产的校准、正净优势、签名批准、保护单、日损和 `RISK_LOCKED` 门槛不因本 ADR 自动解除。

## 风险

最大初始杠杆会按比例放大盈亏、手续费和滑点。系统必须继续先确定结构止损和费用后净优势，
再按亏损预算缩小数量；不得通过取消止损、隐藏浮亏或宣称 100% 胜率绕过风险核算。
