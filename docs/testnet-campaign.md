# Binance Testnet 三日策略观察

该服务只连接 Binance USDⓈ-M Futures Testnet，不连接生产交易端点。它每 10 秒读取
SOLUSDT、BNBUSDT、XRPUSDT、DOGEUSDT 和 ADAUSDT 的 1 分钟/5 分钟闭合 K 线、20 档
深度和最近 5 秒 WebSocket 聚合成交，调用仓库现有的 Price Action 与 Order Flow 原语生成候选
信号。行情读取最多使用 3 个并行观察 worker，缩短五个标的之间的观测偏移；交易选择
仍严格保持每轮最多一个。这五个标的是当前 Testnet 上能够在 1 USDT 保证金、10x 杠杆和交易所最小下单量
约束内执行的初始候选池；它扩大前向样本，但不能冒充文档要求的正式 Top10 排名证据。

当前参数来自仓库中标记为 `UNVALIDATED_ENGINEERING_BASELINE` 的配置，因此本服务当前以
`OBSERVATION_ONLY` 运行，只采集前向 PA/OF 观察，不会下单。下列条件只能形成行情诊断
候选，不能形成可执行 `TradePlan`：

- 1 分钟和 5 分钟 PA 均为多头趋势；
- book imbalance、microprice、trade imbalance 和 CVD 同向确认；
- 当前点差不超过文档规定的 10 bps Universe ceiling；这里只是实时代理值，正式资格仍须
  使用 15 分钟一秒槽的 Type-7 中位点差；
- 5 分钟全局冷却、每日最多 24 单以及每日净亏损 0.30 USDT 的试验上限均未触发；
- 交易账户无遗留订单或持仓。

每轮最多从全部诊断候选中确定性选择一个。完整文档流程仍需 15 分钟全市场一秒槽、正式
Top10 防抖/预热、完整 setup 状态机和三日 gross-edge 校准；在这些证据形成前，候选固定
记录 `PA_SETUP_STATE_INCOMPLETE`、`NET_EDGE_EVIDENCE_INCOMPLETE` 和
`STRATEGY_EXIT_PLAN_INCOMPLETE`，入场结论固定为 `REJECT`。

根据 owner 在 2026-07-14 的明确变更，原冻结基线中的固定持仓时限已经撤销。任何经过秒数
都不能单独触发平仓。持仓只由原生结构止损、PA 结构失效、OF 衰竭/反向、结构目标、硬风控
或人工/对账动作退出；数据暂时不健康但原生保护健康时继续持有。旧的固定盈亏/倒计时
Testnet runner 已删除，三日观察服务不会调用该路径。变更记录见 ADR 0006。

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

聚合成交由 `demo-fstream.binance.com` 的公开实时 `aggTrade` 流持续接收，只接受带有效
`nq` normal quantity 的事件，不再用 10 秒 REST 轮询冒充实时 OF。每条新观察同时记录当时
`mid_price` 和 `microprice`，供不使用持仓到期退出的结构策略做
前向 markout、结构止损/目标触发和费用后结果统计。旧观察缺少价格字段，不允许反推或补值。

当前服务不创建新仓，可直接停止：

```bash
systemctl stop aiq-testnet-campaign.service
```

## 并行执行压力样本

2026-07-14 的旧三币压力样本是历史协议证据，不是策略证据。其固定倒计时 runner 已按
ADR 0006 删除，不能再次运行。历史证据仍保留在
`/var/lib/ai-quant/evidence/testnet/parallel/20260714-sample-01/` 供审计。
