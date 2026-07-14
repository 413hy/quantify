# Binance Testnet 三日实验交易

该服务只连接 Binance USDⓈ-M Futures Testnet，不连接生产交易端点。它每 10 秒读取
SOLUSDT、BNBUSDT、XRPUSDT、DOGEUSDT 和 ADAUSDT 的闭合 1 分钟/5 分钟 K 线、20 档
深度及最近 5 秒 WebSocket 聚合成交，并以最多 3 个观察 worker 并行生成信号。

## 实验规则

这是 `UNVALIDATED_TESTNET_EXPERIMENT`，不能声称已经盈利，也不能用于生产交易：

- 最近主动成交失衡达到 0.20 时确定多空方向；book imbalance 或 microprice 至少一项同向；
- 1 分钟和 5 分钟 PA 不得与入场方向相反；当前点差不得超过 10 bps；
- 止损使用最近 5 根闭合 1 分钟 K 线极值加 0.10 ATR 缓冲；若距离过近，外扩至
  0.30%，若超过 1.20%则拒绝；
- 止盈为价格波动 0.20%–0.35%；
- 每个币最多一个仓位，最多 3 个不同币并行；单笔保证金上限约 1 USDT；执行器每次读取
  Testnet leverage bracket，使用该币种当前允许的最高初始杠杆（当前候选约 50–75 倍）。
  系统按结构止损距离、双边 taker 手续费和 2 bps 不利滑点自动缩小保证金，使单笔预计净
  亏损不超过 0.35 USDT；同币平仓后至少冷却 60 秒；
- 每日最多 100 个已提交/活动样本，每日净亏损达到 1.00 USDT 后不再新增仓；
- 退出只依赖 Binance 原生 `STOP_MARKET`、`TAKE_PROFIT_MARKET`，或操作员停止服务时的
  reduce-only 平仓。没有按持仓秒数到期平仓。

高杠杆只存在于精确 Testnet 客户端的独立实验方法中。生产风险模型、能力探针和普通杠杆
修改接口继续执行 10 倍硬上限；这一例外不能解锁或传播到生产环境。

信号实验不再受“历史费用后收益必须为正”这一生产准入条件阻断。严格 PA/OF 基线的
`entry_verdict=REJECT` 仍保留在观察证据中，用于区分生产准入结论和 owner 明确授权的
Testnet 样本采集。变更依据见 ADR 0007。

## 运行与证据

```bash
systemctl status aiq-testnet-campaign.service
journalctl -u aiq-testnet-campaign.service -n 100 --no-pager
jq . /var/lib/ai-quant/evidence/testnet/campaign/current/state.json
```

所有观察、提交、执行错误和逐单结果追加到
`/var/lib/ai-quant/evidence/testnet/campaign/current/observations.jsonl`。状态文件分别记录已提交
开仓数、已完成平仓数、活动币种、目标命中数、手续费后累计净结果和逐币冷却时间。

Telegram 使用中文发送活动启动、信号提交、逐单平仓、异常、6 小时简报和活动结束通知。
交易通知包括币种、方向、入场、结构止损、目标、已实现盈亏、手续费及净结果。

聚合成交来自 `demo-fstream.binance.com` 的公开实时 `aggTrade` 流，只接受带有效 `nq`
normal quantity 的事件。订单、Algo 保护单和持仓通过 Testnet REST 签名接口核对。

停止服务会视为操作员退出：服务请求所有活动 worker 用 reduce-only 市价平仓并清理剩余
Algo 单，然后才退出。这不是策略持仓时间退出。

```bash
systemctl stop aiq-testnet-campaign.service
```

历史固定倒计时执行样本只作为协议证据保留在
`/var/lib/ai-quant/evidence/testnet/parallel/20260714-sample-01/`；对应 runner 已按 ADR 0006
删除，不得恢复。
