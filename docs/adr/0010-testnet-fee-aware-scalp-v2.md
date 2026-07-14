# ADR 0010: Testnet 费用感知超短线 V2 与运行权威可见性

- 状态：Accepted
- 日期：2026-07-15
- 范围：Binance USDⓈ-M Futures Testnet experiment only

## 背景

对 `observations.jsonl` 的实际成交审查显示，首批 14 笔实验中只有 5 笔手续费后为正，累计
净结果约为 -0.444 USDT；手续费约 0.480 USDT，超过全部毛收益。原目标仅 20–35 bps，
结构止损允许 30–120 bps，费用后盈亏比不足。6 个被标记为止盈的样本平均净结果只有约
0.034 USDT，其中一个 `MARK_PRICE` 止盈在合约盘口的实际平仓接近入场，最终手续费后仍为负。

审查还确认两个实现问题：候选按 symbol 字母顺序而非质量排序；持仓归零后立即读取异步
Algo 状态，产生多次 `NATIVE_EXIT_UNCLASSIFIED`。Telegram 结果中也没有展示已经记录在证据
里的实际杠杆。另有 `AuthorityController` 组件测试，但它未接入已部署 runner；实际 Testnet
campaign 本来就是独立确定性规则路径，却没有在状态和通知中明确说明。

## 决策

`TESTNET_EXPERIMENT_OF_PA_V2` 做以下限定变更：

- 目标距离改为 `max(35 bps, min(60 bps, 0.75 * structure_risk_bps))`；结构止损的
  30–120 bps 范围、1 USDT 保证金上限、交易所最大初始杠杆、单笔 0.35 USDT 预计净亏损
  预算和每日 1 USDT 净亏损门槛不变；
- 多候选按 PA 同向、效率、方向性订单流和点差排序；不增加持仓时间退出；
- Testnet 实验原生止盈止损使用 `CONTRACT_PRICE`，并在持仓归零后最多等待约 2 秒确认最终
  Algo 状态。该变化不修改生产 `risk.yaml` 的保护价格源；
- 仓位保护确认事件记录并通知实际杠杆、数量、名义价值、初始保证金、预计毛/净目标、预计
  止损净亏损和触发价源；平仓通知展示相同仓位事实及真实费用后结果；
- Testnet 状态和通知固定写入 `decision_authority=TESTNET_DETERMINISTIC_RULE`、
  `codex_dependency=false`。生产 Codex→规则切换仍按 ADR 0001 冻结，不虚报为已部署。

## 后果

目标距离增加可能降低目标命中频率或延长自然持仓时间，但每个目标样本应留下比 V1 更有意义
的费用后空间。它不保证盈利，也不能用 14 笔样本证明策略有效。V2 必须继续追加 Testnet
真实成交，分别统计净胜率、目标/止损平均净值、profit factor、费用占毛收益比例、逐币结果和
未分类退出率，再决定下一轮参数；生产保持 `RISK_LOCKED`。
