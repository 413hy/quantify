# Binance Testnet 三日策略观察

该服务只连接 Binance USDⓈ-M Futures Testnet，不连接生产交易端点。它每 10 秒读取
SOLUSDT、BNBUSDT、XRPUSDT、DOGEUSDT 和 ADAUSDT 的 1 分钟/5 分钟闭合 K 线、20 档
深度和最近 500ms 聚合成交，调用仓库现有的 Price Action 与 Order Flow 原语生成候选
信号。行情读取最多使用 3 个并行观察 worker，缩短五个标的之间的观测偏移；交易选择
仍严格保持每轮最多一个。这五个标的是当前 Testnet 上能够在 1 USDT 保证金、10x 杠杆和交易所最小下单量
约束内执行的初始候选池；它扩大前向样本，但不能冒充文档要求的正式 Top10 排名证据。

当前参数来自仓库中标记为 `UNVALIDATED_ENGINEERING_BASELINE` 的配置，因此本服务的目标
是采集前向观察并验证执行，不是声明策略已经盈利。只有以下条件同时成立才允许测试网下单：

- 1 分钟和 5 分钟 PA 均为多头趋势；
- book imbalance、microprice、trade imbalance 和 CVD 同向确认；
- 当前点差不超过文档规定的 10 bps Universe ceiling；这里只是实时代理值，正式资格仍须
  使用 15 分钟一秒槽的 Type-7 中位点差；
- 5 分钟全局冷却、每日最多 24 单以及每日净亏损 0.30 USDT 的试验上限均未触发；
- 交易账户无遗留订单或持仓。

每轮最多从全部合格候选中确定性选择一个，不允许并行堆仓。完整文档流程仍需 15 分钟
全市场一秒槽、正式 Top10 防抖/预热、完整 setup 状态机和三日 gross-edge 校准；在这些
证据形成前，本服务结果保持 `UNVALIDATED_TESTNET_BASELINE`，不能作为生产策略结论。

每次交易继续执行 1 USDT 保证金上限、10 倍杠杆上限、原生止损/止盈、900 秒保护性最长持仓和
最终零状态对账。目标/止损是成本模型预算，不是成交或盈亏保证；跳空与滑点仍可能导致
实际结果偏离。900 秒不是目标持仓时间：原生止盈或止损是正常退出，只有二者均未
发生时才触发 reduce-only 最终保险退出。Testnet 净止盈目标从 0.10 调整为 0.05 USDT，
降低约 10 USDT 名义仓位需要等待的有利价格移动；0.1 USDT 最大净损失预算对应首笔 SOLUSDT
约 0.97% 的价格距离；该笔并未触发止损，而是在旧版 30 秒最大持仓时间退出。正式策略必须用 setup 的结构锚点生成
止损，不能仅为增加交易次数而放宽固定亏损预算。

运行状态：

```bash
systemctl status aiq-testnet-campaign.service
journalctl -u aiq-testnet-campaign.service -n 100 --no-pager
jq . /var/lib/ai-quant/evidence/testnet/campaign/current/state.json
```

信号观察追加在
`/var/lib/ai-quant/evidence/testnet/campaign/current/observations.jsonl`，逐单证据在其
`trades/` 子目录。Telegram 只发送启动、6 小时简报、逐单结果、异常和结束通知，不会按
分钟发送无信号消息。通知时间统一显示北京时间；真实逐单通知包含方向、数量、入场价、
止盈/止损触发价、已实现盈亏、手续费、净结果、保护确认延迟和最终零状态。标注“模拟”的
通知只用于格式验证，不计入交易统计。

停止服务不会创建新仓；如果恰逢 30 秒有界持仓，执行器会先完成原生保护、平仓和零状态
清理：

```bash
systemctl stop aiq-testnet-campaign.service
```

## 并行执行压力样本

`scripts/run-testnet-parallel-sample.py` 可在暂停候选服务、确认各标的零状态后，对不同标的
并行执行有界 Testnet 仓位。它用于验证并发下单、原生保护、手续费、通知和最终清理，输出
必须标记为 `EXECUTION_STRESS_NOT_STRATEGY_SIGNAL`，不能并入策略胜率。

2026-07-14 的首个三币样本使用旧版 30 秒窗口，同时运行 SOLUSDT、BNBUSDT 和 XRPUSDT。三单均在 30 秒到期
退出，没有触发止损或止盈；净结果分别为 `-0.00742656`、`-0.00525511` 和
`-0.00930724 USDT`，最终订单、条件单和持仓全部为零。该结果表明当前瓶颈不是止损过紧，
而是短持仓窗口内的毛收益没有覆盖手续费。证据位于
`/var/lib/ai-quant/evidence/testnet/parallel/20260714-sample-01/`。
