# V2 止损样本根因分析（2026-07-15）

本报告只使用追加式 Testnet 观察、保护和成交结果，不把事后价格方向包装成可提前知道的事实。

## 结果摘要

V2 最终完成 5 单：2 单结构止损、3 单在部署 V3 时按操作员停止流程平仓，目标命中 0，
费用后累计 -0.38867081 USDT，手续费 0.22624080 USDT，Profit Factor 约 0.1508。样本量不足以
估计稳定胜率，但已能定位放行条件的具体缺陷。

## 两笔结构止损的入场证据

| 交易 | 入场时证据 | 缺陷 | V3 处理 |
| --- | --- | --- | --- |
| DOGEUSDT SHORT | 1m PA 空、5m PA 中性；trade imbalance -1；aggressive notional 15.802470 USDT；book imbalance +0.165961（反向）；microprice -0.854843 bps；spread 2.695418 bps | V2 的 `book OR microprice` 允许一项强烈反向；极少成交即可形成饱和 -1 | book 反向超过 0.05 否决；另需 12 轮活动中位数过滤和连续两轮确认 |
| SOLUSDT SHORT | 1m/5m PA 均中性且结构未确认；trade imbalance -1；aggressive notional 25.377000 USDT；book imbalance -0.043376；microprice +2.948955 bps（反向）；spread 6.499838 bps | 没有 PA 趋势仍可下单；低活动量和反向 microprice 未否决 | 至少一个 PA 周期必须同向；microprice 反向超过 0.25 bps 否决；活动量过滤也会拒绝 |

SOL 空单从 76.900 入场，止盈 76.630、止损 77.140，约 34 分钟后触发止损，费用后
-0.18398575 USDT。持仓时间不是退出条件，也不是亏损原因；错误发生在入场证据不足。

## 外部工具参数如何使用

- Kronos 的 `lookback/context`、`pred_len`、`T`、`top_p` 和 `sample_count` 属于概率 K 线预测
  参数，不能直接替换订单流阈值。本轮只采用其“必须有上下文、不能靠单点”原则：增加 12 轮
  活动基线和跨轮确认。模型本身需在当前五币 1 分钟数据上做费用后 walk-forward 后才可进入
  shadow；温度或采样数不得成为实盘风险参数。
- TradingAgents/AI-Trader 的多角色意见只有在冲突可见时才有价值。本轮将 PA、trade、book、
  microprice 拆开记录，并对明显冲突直接否决，而不是让一个综合分掩盖反向证据。
- QuantDinger 的研究/回测/执行分层继续保留；外部模型没有 Binance 凭据，未验证预测不能
  绕过本地确定性门槛。
- daily-stock-analysis、PTrade 和 Lightweight Charts 不产生适用于本场景的微观结构 alpha，
  因此不拿它们的股票周期、券商接口或 UI 参数冒充交易参数。

V3 可能同时过滤掉后来短时盈利的机会（例如 V2 的 BNB 多单入场时 PA 也为中性）。这是减少
误判必然付出的交易频率代价，不能同时承诺高频、低误判和接近百分百胜率。

## V3 部署过程样本说明

初版 V3 在 02:05 建立 ADAUSDT 空单：1m PA 空、5m PA 中性，但方向性 microprice 为
-2.648673 bps，说明 microprice 实际与空头方向强烈冲突。它会被本报告新增的反向 0.25 bps
否决条件挡住。该仓位没有触发策略止损；02:06 为部署冲突过滤而停止服务时由操作员退出，
净结果 -0.14415903 USDT，不应统计为策略止损或目标样本。

该单还显示实际成交后的预计止损净额为 0.3763552 USDT，超过配置的 0.35 USDT。根因是旧
定仓只按信号参考价计算，市场成交价偏移扩大了到结构止损的距离。修正后风险定仓使用 12 bps
成交偏移缓冲，并在真实成交后再次执行预算复核。
