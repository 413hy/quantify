# Binance Testnet 三日实验交易

该服务只连接 Binance USDⓈ-M Futures Testnet，不连接生产交易端点。V4.6 固定使用
BTCUSDT、ETHUSDT、BNBUSDT、SOLUSDT 和 XRPUSDT。它每 10 秒读取闭合 1 分钟/5 分钟 K 线、20 档
深度及最近 5 秒 WebSocket 聚合成交，并以最多 5 个观察 worker 并行生成信号。

## 实验规则（V4.6）

这是 `UNVALIDATED_TESTNET_EXPERIMENT`，不能声称已经盈利，也不能用于生产交易：

- 保留 V4 趋势确认入口，并增加 Testnet 专用的多币联动冲量入口。五币池均参与大盘宽度
  判断并均可生成联动开仓候选；每轮最多提交质量最高的 3 个，不强制补满活动仓位；
- 冲量候选要求本币动量同向、1m/5m PA 均未明确反向、点差不超过 5 bps、主动成交方向
  同向，并且盘口或 microprice 至少一项确认。多币同步本身作为市场确认，因此冲量入口使用
  1 轮提交和 1.25 倍近期主动成交额；原趋势入口仍使用连续 3 轮和 2.00 倍活跃度；
- V4.2 同时计算约 40 秒快速联动和约 110 秒持续联动。快速入口要求至少 3 个币同向
  2 bps，持续入口要求至少 3 个币同向 5 bps；本币在对应窗口已经移动超过 12 bps 时视为
  可能接近冲量尾端，不再新开仓，避免追高或追空；
- 状态文件持续记录 `last_signal_diagnostics` 和累计 `signal_gate_counts`，区分历史不足、市场
  宽度不足、本币动量不足或过热、微观结构/PA 拒绝、交易池排除和已生成计划，避免再次只看到
  “0 交易”却无法定位具体门控；
- V4.6 取前 10 根已闭合 1 分钟 K 线收盘价，使用最小二乘直线预测后 10 分钟收盘价，再对
  前后共 20 个价格取平均。预测均价不再直接作为订单价格，而是相对当前中间价形成有方向的
  预测幅度：预测与信号同向时采用“延续趋势中的回撤入场”，预测与信号反向时采用“预测反弹/
  回落后的均值回归入场”。挂单距离为预测幅度绝对值的一半，限制在 2–8 bps；预测幅度不足
  1 bps 时跳过。做多始终挂在买一以下、做空始终挂在卖一以上，使用 `LIMIT + GTX` 等待
  最多约 30 秒；未成交就撤销并放弃，彻底取消市价兜底和追单。部分成交后立即
  撤销余量，并且只有实际数量仍满足费用后 0.10 USDT 目标才建立原生保护，否则立即清仓；

- 最近主动成交失衡至少达到 0.25 时确定多空方向；book imbalance 至少 0.03 或
  microprice 至少 0.10 bps 同向；
- 即使其中一项同向，book imbalance 反向超过 0.05 或 microprice 反向超过 0.25 bps
  也会一票否决，避免互相冲突的微观结构证据；
- 每个币维护最近 12 轮主动成交额中位数；至少积累 6 轮后，当前值必须达到中位数的
  2.00 倍，避免把普通噪声或极少量成交形成的 `+1/-1` 失衡当成可靠趋势；
- 1 分钟和 5 分钟 PA 均不得与入场方向相反，且至少一个周期必须同向；当前点差不得超过
  5 bps；
- 综合质量分包含 1 分钟/5 分钟 PA 同向、效率、主动成交、盘口、microprice 和点差，必须
  不低于 2.00；原趋势入口相同方向必须连续出现 3 个评估轮次才可提交；
- 止损使用最近 5 根闭合 1 分钟 K 线极值加 0.10 ATR 缓冲；若距离过近，外扩至
  0.30%，若超过 1.20%则拒绝；
- 毛止盈按固定交易池的 Testnet 最大杠杆和执行成本设置：BTC 20 bps、ETH 22 bps、BNB
  25 bps、SOL 32 bps、XRP 25 bps。下单前仍以实际数量、实际费率和 2 bps 不利滑点验证
  费用后目标至少 0.10 USDT；不满足时拒绝该单，而不是扩大仓位或降低净目标；
- 同一轮存在多个候选时，按 1 分钟/5 分钟 PA 同向程度、PA 效率、订单流强度和点差综合
  排序，不再按交易对字母顺序选择；每轮最多提交 3 个；
- 每个币最多一个仓位，活动仓位容量为 0–5 个不同币；5 是硬上限而不是目标，不确认时允许
  一直保留空槽，不会为了补满而降低条件。单笔保证金上限约 1 USDT；执行器每次读取
  Testnet leverage bracket，使用该币种当前允许的最高初始杠杆（当前候选约 50–125 倍）。
  系统按结构止损距离、双边 taker 手续费和 12 bps 风险定仓缓冲自动缩小保证金，使单笔
  预计净亏损不超过 1.00 USDT；实际成交后还会用真实入场价再次复核，超限立即拒绝继续持仓。
  目标净额仍按 2 bps 常规不利滑点估算；同币真实成交并平仓后至少冷却 60 秒，未成交限价
  不再错误占用这段平仓冷却；
- 下单前按当前盘口、数量、实际 taker 费和 2 bps 不利滑点预估目标净额；低于 0.10 USDT
  直接拒绝，不再提交只有“蚊子腿”级费用后空间的仓位；
- 每日最多 100 个已提交/活动样本，每日净亏损达到 1.00 USDT 后不再新增仓；
- 退出只依赖 Binance 原生 `STOP_MARKET`、`TAKE_PROFIT_MARKET`，或操作员停止服务时的
  reduce-only 平仓。Testnet 超短线实验使用 `CONTRACT_PRICE`，让触发源和可成交合约盘口
  一致；没有按持仓秒数到期平仓。生产保护价源仍由独立风险配置和准入证据决定；
- 交易所报告持仓归零后，执行器会短暂重试 Algo 查询，等待 `FINISHED` 状态后再区分止盈或
  止损，避免异步状态传播造成 `NATIVE_EXIT_UNCLASSIFIED`。

杠杆策略为 `EXCHANGE_MAXIMUM`：Testnet 和未来生产都不再施加项目自定义倍数上限，每次
必须读取当前币种、账户及名义仓位对应的 Binance bracket。生产环境的校准、签名和
`RISK_LOCKED` 准入门槛仍独立存在，杠杆规则变更本身不会启用真钱交易。

信号实验不再受“历史费用后收益必须为正”这一生产准入条件阻断。严格 PA/OF 基线的
`entry_verdict=REJECT` 仍保留在观察证据中，用于区分生产准入结论和 owner 明确授权的
Testnet 样本采集。变更依据见 ADR 0007。

## 运行与证据

```bash
systemctl status aiq-testnet-campaign.service
journalctl -u aiq-testnet-campaign.service -n 100 --no-pager
jq . /var/lib/ai-quant/evidence/testnet/campaign/current/state.json
systemctl status aiq-testnet-user-stream.service
journalctl -u aiq-testnet-user-stream.service -n 100 --no-pager
jq . /var/lib/ai-quant/evidence/testnet/user-stream/current/state.json
```

所有观察、提交、执行错误和逐单结果追加到
`/var/lib/ai-quant/evidence/testnet/campaign/current/observations.jsonl`。状态文件分别记录已提交
开仓数、已完成平仓数、活动币种、目标命中数、手续费后累计净结果和逐币冷却时间。

按策略版本复核费用后胜率、profit factor、目标与非目标平均净值、退出原因和逐币结果：

```bash
uv run python scripts/review-testnet-results.py \
  --observations /var/lib/ai-quant/evidence/testnet/campaign/current/observations.jsonl \
  --strategy TESTNET_EXPERIMENT_OF_PA_V4_6
```

少于 30 个已完成 V4 样本时报告固定为 `INSUFFICIENT_SAMPLE`，不能据此宣称策略有效。
2026-07-15 的实际结果、观测序列因果回放、参数小样本风险和旧结构代理交叉检查见
`docs/testnet-v3-backtest-review-20260715.md`。同名带单员的公开订单审查、固定小止盈回放和
BTC/ETH V4 重构边界见 `docs/strategy-v4-refactor-review-20260715.md`。

独立的只读用户数据流观察器 `aiq-testnet-user-stream.service` 与实验执行线程解耦。它只连接
当前 Testnet 私有 stream，维护 listen key、自动重连并对 `ORDER_TRADE_UPDATE`、
`ACCOUNT_UPDATE` 和 `ALGO_UPDATE` 做哈希链、去重和脱敏留证；不具备下单接口。状态与事件为：

- `/var/lib/ai-quant/evidence/testnet/user-stream/current/state.json`
- `/var/lib/ai-quant/evidence/testnet/user-stream/current/events.jsonl`

停止观察器形成一致快照后，可用独立验链器校验所有记录、去重身份、事件类型覆盖和状态摘要：

```bash
scripts/verify-testnet-user-stream.py \
  --events /var/lib/ai-quant/evidence/testnet/user-stream/current/events.jsonl \
  --state /var/lib/ai-quant/evidence/testnet/user-stream/current/state.json \
  --output /var/lib/ai-quant/evidence/testnet/user-stream/current/verification.json
```

Telegram 使用中文发送活动启动、信号提交、仓位及原生保护确认、逐单平仓、异常、6 小时
简报和活动结束通知。仓位确认和结果通知包括实际杠杆倍数、数量、名义价值、实际初始保证金、
入场、止损、止盈、预计费用后目标或实际已实现盈亏、手续费及净结果。

独立只读仪表盘 `aiq-telegram-dashboard.service` 使用 Telegram 官方长轮询和持久回复键盘，
仅接受 `telegram_chat_ids` 中的 chat ID。按钮包括：

- `📊 当前盈亏`：当前 UTC 交易日、本轮及全部实验历史费用后结果；
- `📈 当前持仓`：方向、杠杆、保证金、入场、止盈止损和预计净额；
- `🧭 运行状态`：campaign、用户数据流、决策来源、Codex 依赖和生产请求数；
- `🧪 策略统计`：费用后胜率、目标命中率、平均盈亏、Profit Factor 和逐币结果；
- `🔄 刷新盈亏`、`❔ 帮助`。

同时支持 `/start`、`/pnl`、`/positions`、`/status`、`/stats`、`/help`。该服务没有 Binance
凭据或交易接口，不能通过 Telegram 开仓、平仓、撤单或修改参数。运行状态位于：

```bash
systemctl status aiq-telegram-dashboard.service
jq . /var/lib/ai-quant/telegram/dashboard-state.json
```

## Codex 与备用规则策略状态

当前 `aiq-testnet-campaign.service` 的决策权威固定为
`TESTNET_DETERMINISTIC_RULE`，`codex_dependency=false`。也就是说，停止当前 Codex 会话、Codex
CLI 不可用或额度耗尽，都不会让这个 Testnet 服务停止评估和交易；systemd 会独立维持服务。

仓库中的 `AuthorityController` 已实现并测试 Codex 失败后切换 `RULE_FALLBACK` 的状态机，但
生产实时 Codex runner、epoch lease 和规则 runner 尚未接入已部署交易路径。原因是 ADR 0001
冻结的精确 `gpt-5.6` catalog 条件仍未满足，同时生产执行保持 `RISK_LOCKED`。因此不能把该
组件测试描述成已经上线的生产自动切换。Testnet 的确定性策略是当前实际运行的独立路径，
其状态和每笔通知都会明确标注“不依赖 Codex”。

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
