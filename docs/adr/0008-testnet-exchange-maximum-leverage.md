# ADR 0008: 隔离的 Testnet 交易所最大杠杆实验

- 状态：Superseded by ADR 0009
- 日期：2026-07-15
- 范围：Binance USDⓈ-M Futures Testnet experiment only

## 背景

Owner 明确要求研究“约 1 USDT 保证金、交易所允许的最高杠杆、0.20%–0.35% 小目标”的
Testnet 模型。原项目风险契约和普通杠杆修改接口固定拒绝 10 倍以上，不能直接放宽并影响
生产路径。

## 决策

本 ADR 当时新增只属于精确 Testnet 客户端的实验杠杆方法。结构实验执行器在每次入场前读取该币种
`leverageBracket`，选择当前返回的最高 `initialLeverage`，上限 125 倍；单笔保证金仍不
超过 1 USDT。普通 `change_initial_leverage`、生产风险模型、能力探针和所有生产准入仍
保持 10 倍硬上限。

每单继续强制交易所原生结构止损和止盈，不接受“不设止损”。仓位按止损距离、双边 taker
费和不利滑点缩小，使预计单笔净亏损不超过 0.35 USDT。实验记录必须写入实际杠杆和
`EXCHANGE_MAXIMUM_TESTNET_ONLY` 策略标签。

## 后果

当前候选币种的 Testnet 最大初始杠杆约为 50–75 倍，交易所可随时调整。高杠杆会放大收益、
手续费、滑点和爆仓风险；本实验不证明盈利，不得解锁生产或被描述为 100% 胜率。
