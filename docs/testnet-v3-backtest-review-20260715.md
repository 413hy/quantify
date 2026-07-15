# Testnet V3 交易与回放审查 — 2026-07-15

## 结论

当前 `TESTNET_EXPERIMENT_OF_PA_V3` 未达到正期望，不能解释为“只是交易量不够”。实际完成
的 11 笔策略退出累计净亏损 `-1.11081345 USDT`，胜率和止盈命中率均为 `36.36%`，
Profit Factor 为 `0.3125`。低交易量与负收益同时存在：增加活动仓位上限或强制补满仓位不会
修复信号质量和盈亏比。

本次审查没有修改正在运行的策略参数、没有停止服务，也没有强平审查时仍受原生止盈止损保护
的 XRPUSDT 仓位。生产环境仍保持 `RISK_LOCKED`。

## 实际 Testnet 结果

快照来自 2026-07-14 18:11:52 UTC 开始的当前活动。12 笔结果中有 1 笔是此前部署停止造成
的 `OPERATOR_SERVICE_STOP`；下表的“策略结果”排除了该操作员退出。

| 指标 | 实际结果 |
|---|---:|
| 策略完成交易 | 11 |
| 止盈 / 止损 | 4 / 7 |
| 手续费后正收益率 | 36.36% |
| 策略净收益 | -1.11081345 USDT |
| 策略 Profit Factor | 0.3125 |
| 全部 12 笔毛收益 | -0.67423000 USDT |
| 全部 12 笔手续费 | 0.58074248 USDT |
| 全部 12 笔净收益 | -1.25497248 USDT |

逐币种的全部结果为 ADAUSDT `-0.39927695`、DOGEUSDT `-0.47183265`、XRPUSDT
`-0.38386288 USDT`。BNBUSDT 和 SOLUSDT 没有完成交易。因此当前亏损不是单一币种偶发
异常，现有三个成交币种都为负。

## 当前观测序列因果回放

新增回放器读取当前活动开始后的 append-only `SIGNAL_OBSERVATION`。它按时间顺序重建每个
币种的滚动成交活跃度、连续方向确认、质量分、点差、PA 同向数量、最多 5 个不同币种仓位和
60 秒同币冷却。入场只能使用当时已经记录的实验计划；退出只能由后续 10 秒中间价首次穿越
结构止盈或止损触发，没有持仓时间到期退出。

成本统一按双边 8 bps taker 手续费和退出 2 bps 不利滑点计算。13,805 条观测中，现行参数
只有 86 个原始候选、15 个确认候选、13 笔已结束回放交易，最大同时仓位为 3。换言之，低交易
量主要发生在信号和连续确认漏斗，不是 5 个活动仓位上限造成的。

| 参数变体 | 完成 | 胜率 | 净 bps | Profit Factor | 判断 |
|---|---:|---:|---:|---:|---|
| 当前 V3 | 13 | 38.46% | -210.06 | 0.373 | 失败 |
| 活跃度至少 1 倍 | 8 | 50.00% | -60.00 | 0.625 | 失败 |
| 活跃度至少 2 倍 | 7 | 57.14% | -20.00 | 0.833 | 失败 |
| 连续确认 3 轮 | 8 | 37.50% | -140.06 | 0.349 | 失败 |
| 点差最多 5 bps | 10 | 40.00% | -140.00 | 0.417 | 失败 |
| 活跃度 2 倍 + 确认 3 轮 | 3 | 66.67% | +10.00 | 1.25 | 样本过小 |
| 最小止盈 50 bps | 12 | 25.00% | -255.06 | 0.320 | 更差 |
| 最小止盈 60 bps | 10 | 10.00% | -325.06 | 0.133 | 更差 |
| 50 bps + 活跃度 2 倍 + 确认 3 轮 | 3 | 66.67% | +40.00 | 2.00 | 样本过小 |
| 1m、5m PA 必须都同向 | 1 | 100% | +25.00 | 无亏损样本 | 样本过小 |

两个为正的筛选只有 3 笔或 1 笔，并且都来自同一小段样本内调参，不能据此上线。把止盈简单
扩大到 50 或 60 bps 明显降低命中率，也不能解决问题。

针对约 20 bps 毛价格波动的“蚊子肉”目标又执行了一组固定目标回放。12、15、18、20、22、
25、30、35 bps 八个目标全部为负。20 bps 将胜率提高到 `58.82%`，但双边 8 bps 手续费及
2 bps 不利滑点后，每次止盈仅剩约 10 bps，而结构止损约亏 40 bps，17 笔合计仍为
`-180 bps`。增加 2 倍成交活跃度过滤后胜率为 `66.67%`、9 笔合计 `-60 bps`；再要求
连续确认 3 轮虽得到 3/3、`+30 bps`，样本仍小到不能采信。

## 旧结构代理复核

原有 1m/5m K 线结构代理也重新执行。它只有 2 笔完成交易、0 胜，按 10 USDT 名义价值净亏
`-0.0389499593 USDT`，结论仍为 `FAIL_RESEARCH_GATE`。该代理没有历史 L2 和 normal-quantity
订单流，只能作为独立反证，不能冒充当前 V3 的精确回测。

## 原因判断

1. 当前费用后典型止盈约为 `+25 bps`，多数止损约为 `-40 bps`；仅按这组结果，保本胜率约
   为 `61.54%`，而现行回放只有 `38.46%`。实际成交平均盈利 `+0.1263 USDT`、平均亏损
   `-0.2200 USDT`，实际保本所需胜率约 `63.54%`。
2. 质量分没有把盈利和亏损样本有效分开；多个高质量分、高成交活跃度候选仍然止损。说明现有
   分数更多是在累计“条件出现”，尚未校准为可预测费用后收益的概率。
3. 13,805 条观测只形成 86 个原始候选和 15 个确认候选。提高仓位上限、缩短空槽轮询或为了
   补满而放宽条件，只会增加未验证信号，不能从证据推出收益改善。
4. 10 秒中间价回放可能漏掉区间内止盈/止损触碰，也不能复现真实成交滑点；本次结果只能用于
   Testnet 研究淘汰和参数比较，不能用于生产资格或收益承诺。

## 可复现证据

```bash
.venv/bin/python scripts/review-testnet-results.py \
  --observations /var/lib/ai-quant/evidence/testnet/campaign/current/observations.jsonl \
  --strategy TESTNET_EXPERIMENT_OF_PA_V3 \
  --output /var/lib/ai-quant/evidence/testnet/backtest/20260715-review/v3-actual-results.json

.venv/bin/python scripts/replay-testnet-v3-observations.py \
  --observations /var/lib/ai-quant/evidence/testnet/campaign/current/observations.jsonl \
  --campaign-state /var/lib/ai-quant/evidence/testnet/campaign/current/state.json \
  --output /var/lib/ai-quant/evidence/testnet/backtest/20260715-review/v3-observation-replay.json
```

机器证据位于：

- `/var/lib/ai-quant/evidence/testnet/backtest/20260715-review/v3-actual-results.json`
- `/var/lib/ai-quant/evidence/testnet/backtest/20260715-review/v3-observation-replay.json`
- `/var/lib/ai-quant/evidence/testnet/backtest/20260715-review/structural-review.json`
