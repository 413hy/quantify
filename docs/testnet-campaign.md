# Binance Testnet 三日策略观察

该服务只连接 Binance USDⓈ-M Futures Testnet，不连接生产交易端点。它每 60 秒读取
SOLUSDT 的 1 分钟/5 分钟闭合 K 线、20 档深度和最近 500ms 聚合成交，调用仓库现有的
Price Action 与 Order Flow 原语生成候选信号。

当前参数来自仓库中标记为 `UNVALIDATED_ENGINEERING_BASELINE` 的配置，因此本服务的目标
是采集前向观察并验证执行，不是声明策略已经盈利。只有以下条件同时成立才允许测试网下单：

- 1 分钟和 5 分钟 PA 均为多头趋势；
- book imbalance、microprice、trade imbalance 和 CVD 同向确认；
- 点差不超过 3 bps；
- 15 分钟冷却、每日最多 8 单以及每日净亏损 0.30 USDT 的试验上限均未触发；
- 交易账户无遗留订单或持仓。

每次交易继续执行 1 USDT 保证金上限、10 倍杠杆上限、原生止损/止盈、30 秒最长持仓和
最终零状态对账。目标/止损是成本模型预算，不是成交或盈亏保证；跳空与滑点仍可能导致
实际结果偏离。

运行状态：

```bash
systemctl status aiq-testnet-campaign.service
journalctl -u aiq-testnet-campaign.service -n 100 --no-pager
jq . /var/lib/ai-quant/evidence/testnet/campaign/current/state.json
```

信号观察追加在
`/var/lib/ai-quant/evidence/testnet/campaign/current/observations.jsonl`，逐单证据在其
`trades/` 子目录。Telegram 只发送启动、6 小时简报、逐单结果、异常和结束通知，不会按
分钟发送无信号消息。

停止服务不会创建新仓；如果恰逢 30 秒有界持仓，执行器会先完成原生保护、平仓和零状态
清理：

```bash
systemctl stop aiq-testnet-campaign.service
```
