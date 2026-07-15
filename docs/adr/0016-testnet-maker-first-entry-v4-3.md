# ADR 0016：Testnet maker-first 入场与五币候选 V4.3

- 状态：Accepted
- 日期：2026-07-15
- 范围：Binance USDⓈ-M Futures Testnet；不解锁生产交易

## 背景

V4.2 的执行器把入场类型固定为 `MARKET`，尽管 Binance USDⓈ-M Futures 支持 `LIMIT`、
`STOP`、`STOP_MARKET`、`TAKE_PROFIT` 和 `TAKE_PROFIT_MARKET`。官方枚举还定义 `GTX`
为无法成为挂单方就撤销。V4.2 同时只允许 BTC/ETH 表达联动交易意向，BNB/SOL/XRP 仅参与
市场宽度计算，导致状态中大量 `ENTRY_SYMBOL_EXCLUDED`。

官方参考：

- <https://developers.binance.com/zh-CN/docs/products/derivatives-trading-usds-futures/common-definition>

## 决策

1. 信号仍是唯一入场触发器；不提前长期挂无信号订单。
2. 信号通过后，执行器先在买一（做多）或卖一（做空）提交 `LIMIT + GTX`，轮询最多 6 次、
   每次 250ms。
3. 完全未成交时撤销挂单。最新市价相对初始参考价的方向性追价不超过 3 bps 才允许
   `MARKET` 兜底；超过则拒绝该次入场。
4. 部分成交时立即撤销余单，以实际数量重新检查费用后目标与最大损失；不足 0.10 USDT
   则清仓拒绝，不留下无保护小仓位。
5. 仓位建立后继续使用交易所原生 `STOP_MARKET` 和 `TAKE_PROFIT_MARKET`。结果证据和中文
   通知记录 GTX 挂单价、实际入场方式及市价兜底状态。
6. BNB、SOL、XRP 与 BTC、ETH 一样可以生成联动候选；质量排序和每轮最多 2 个仍适用，
   点差、执行成本、过热、活动仓位和每日损失限制不变。

## 后果

maker-first 可能降低入场手续费和滑点，也可能因不成交而错过行情；3 bps 市价兜底是成交率
和追价风险之间的 Testnet 实验边界，不保证更优。五币均可开仓会增加相关敞口和未完成仓位，
必须按逐币费用后结果评估。生产继续 `RISK_LOCKED`。
