# Binance Testnet 持续实验交易

该服务只连接 Binance USDⓈ-M Futures Testnet，不连接生产交易端点。V5.6 固定使用
BTCUSDT、ETHUSDT、BNBUSDT、SOLUSDT 和 XRPUSDT。它每 60 秒读取闭合 1 分钟/5 分钟/1 小时 K 线、20 档
深度及最近 5 秒 WebSocket 聚合成交，并以最多 5 个观察 worker 并行生成信号。

## 当前实验规则（V5.6）

V5.6 继续禁止普通单币 PA 计划和原始宽度冲量直接提交。新仓只有两个受控入口：
`MARKET_BREADTH_PULLBACK_RESUMPTION` 与 `MARKET_BREADTH_CONTINUATION`：

- 先积累完整 5 轮数据；至少 3/5 个固定币在约 3 分钟快速窗口同向达到 2 bps，或至少 4/5 在约
  4 分钟持续窗口同向达到 5 bps，才给同向本币建立待入场状态；建立状态本身不下单；
- 状态最多保留 10 轮（约 10 分钟）。这是信号形态的失效时间，不是持仓时间退出；仓位仍然没有
  到期平仓。期间方向相反的四币市场背景会立即撤销旧状态；
- 多头从状态建立后的最高价回踩至少 3 bps，空头从最低价反抽至少 3 bps，才进入
  `PULLBACK_OBSERVED`。回撤/反抽超过 40 bps 视为形态破坏并撤销，不会逆向猜底/猜顶；
- 回踩后，相邻一分钟观察价格同向恢复至少 0.50 bps、本币 1m/5m PA 或 5/5 宽度所有权、以及“主动成交
  失衡至少 0.25 同向并有盘口 0.03 或 microprice 0.10 bps 二级确认”这三类证据，不再强制同一
  轮同时出现；它们在回踩后 2 轮（约 2 分钟）内集齐即可。真正下单时点差仍必须不超过 5 bps，
  1 小时 PA 不得明确反向；
- 锁存证据集齐时，当前轮快速背景必须仍有至少 3/5 同向，持续背景必须至少 4/5，本币约 3 分钟
  动量必须同向且在 2–15 bps；旧波段建立时的动量不能继续授权已经消失或反向的市场环境；
- 对不回踩的干净单边行情，当前轮快速背景至少 3/5、持续背景至少 4/5，本币约 3 分钟
  方向动量必须在 4–15 bps，且当前主动成交和至少一个二级流信号确认、点差不超过 5 bps，才可
  生成 `MARKET_BREADTH_CONTINUATION`。15 bps 上限用于拒绝尾端追价；同一波段每币只提交一次；
- 宽度只负责发现市场背景，不单独授权交易。四币以上回踩入口仍要求本币 1m/5m 至少一个方向或
  HH/HL、LH/LL 结构一致，预测至少同向 0.10 bps；直接续行入口要求预测至少同向 2 bps。若 PA 尚
  未成形，预测至少 3 bps、主动成交方向强度至少 0.75，且盘口或 microprice 同向至少 0.10，可用
  `FORECAST_AND_FLOW_SUBSTITUTE` 提前取得结构权限。三币快速背景无论 PA 是否已对齐，都必须通过
  这一组更强的预测与资金流门控；预测反向直接拒绝。Testnet 主动
  成交额异常倍率只记录诊断，目标历史触达率仍至少为 2%；
- 止损取最近 5 根闭合 1 分钟结构极值加 0.10 ATR 缓冲，并至少外扩至 60 bps；超过 120 bps
  拒绝。执行器按真实数量、双边 taker 费和 12 bps 风险缓冲缩小保证金，预计整单最大净损失
  不得超过 1 USDT；
- 反向快速宽度不再直接把正常回踩当成持仓失效。除四币反向趋势失效外，系统每分钟跟踪持仓最大
  顺向幅度：顺向至少 6 bps 后回吐至入场价、逆向达到 10 bps，或顺向至少 20 bps 后回吐 10 bps，
  只要当前轮有反向主动成交和二级流确认，就发出 exit-only 局部失效，不再额外等待下一分钟；
  完整的反向 V5 入场信号仍可按“先平旧仓、再开新向”接管；
- 固定毛止盈改为 BTC/ETH 22 bps，BNB/SOL/XRP 25 bps；这是约 1 USDT 保证金、交易所最大杠杆、
  双边 taker 费和执行缓冲下仍可留下至少 0.10 USDT 预计净值的下界。真实成交后继续按实际数量
  复核费用后目标至少 0.10 USDT，宽度入口最低净收益风险比为 0.15，并立即挂原生止盈止损；
- 五个固定币都可同时持有仓位；每轮最多处理五个合格候选，不设市场波段总入场名额，也不设逐币
  时间冷却。每分钟循环给每个已确认计划生成稳定的 `方向:形态:起始轮` 信号事件身份并持久化，
  同一个连续信号无论持续多少轮、平仓或进程重启都只提交一次；信号中断后重新完成确认才是新的
  可交易事件。已有同币仓位时不做同向加仓；不同方向的完整确认信号可先平旧仓再开新向。连续
  4 轮的 5/5 快速反向仍只承担持仓失效，不凭快速宽度自动反手；服务及决策不依赖 Codex 在线。

V5.6 的因果回放只能用于淘汰明显不合格参数，不能证明未来盈利。V5.0 的回踩入口依据见
[ADR 0032](adr/0032-pullback-resumption-v5-0.md)，V5.1 的漏单根因、10 秒重标定和续行参数见
[ADR 0033](adr/0033-latched-evidence-continuation-v5-1.md)，V5.1 的 11 笔费用审计与 V5.2 低换手修复见
[ADR 0034](adr/0034-fee-aware-direction-authority-v5-2.md)，五币机会容量与信号事件去重见
[ADR 0035](adr/0035-signal-owned-five-position-capacity-v5-3.md)，V5.3 低频亏损审计与 V5.4 修复见
[ADR 0036](adr/0036-predictive-flow-entry-and-local-exit-v5-4.md)，三币快速配置失效审计与 V5.5 修复见
[ADR 0037](adr/0037-three-coin-predictive-fast-context-v5-5.md)，一分钟轮询与时间窗口重标定见
[ADR 0038](adr/0038-one-minute-cadence-v5-6.md)。

## 已失效的 V4.17 规则（仅保留历史审计，禁止用于当前入口）

这是 `UNVALIDATED_TESTNET_EXPERIMENT`，不能声称已经盈利，也不能用于生产交易：

- 保留 V4 趋势确认入口，并增加 Testnet 专用的多币联动冲量入口。五币池均参与大盘宽度
  判断并均可生成联动开仓候选；五个币在同一轮都完成确认时可全部提交，但不强制补满活动仓位；
- 冲量候选要求本币动量同向、1m/5m PA 均未明确反向、点差不超过 5 bps、主动成交方向
  同向，并且盘口或 microprice 至少一项确认。渐进宽度信号还必须至少一个 PA 周期同向；快速
  冲量可短暂领先 PA，至少需要三币同向。服务必须先积累完整 8 轮市场上下文，才允许快速入口，
  避免重启后只看单次局部波动就追入。快速入口使用 1 轮提交；Testnet 最近 5 秒聚合成交额
  曾在相邻轮次从正常值跳到数万倍，V4.17 只把该绝对倍率写入诊断，不再作为任何入口的硬门槛。
  主动成交方向及盘口/microprice 确认仍保留；普通趋势入口仍使用连续 3 轮；
- 系统按 30 秒轮询同时计算约 90 秒快速联动和约 210 秒持续联动。快速入口要求至少 3 个币同向
  2 bps，持续入口要求至少 3 个币同向 5 bps。持续宽度达到至少 4 币且覆盖币数多于快速宽度时，
  优先按持续趋势处理；若两者覆盖币数相同，但持续窗口中位动量比快速窗口至少多 5 bps，也按
  已建立的持续趋势处理。否则优先快速冲量。8 bps 尾端限制只约束快速窗口，已由更长窗口和 PA
  确认的持续趋势不因本币超过 8 bps 被机械拒绝；
- 状态文件持续记录 `last_signal_diagnostics` 和累计 `signal_gate_counts`，区分历史不足、市场
  宽度不足、本币动量不足或过热、微观结构/PA 拒绝、交易池排除和已生成计划，避免再次只看到
  “0 交易”却无法定位具体门控；V4.11 另记录 `last_confirmation_diagnostics` 和
  `confirmation_gate_counts`，继续区分计划后的质量、活跃度样本、活跃度倍率、连续确认和已确认；
- V4.17 取前 10 根已闭合 1 分钟 K 线收盘价，使用最小二乘直线预测后 10 分钟收盘价，再对
  前后共 20 个价格取平均。普通趋势入口要求方向化预测至少 2 bps；三币宽度入口要求
  预测同向且至少 0.10 bps。至少四币同向且本币动量、PA/订单流均成立时，多币宽度成为主方向
  预测，线性回归只记录诊断而不再一票否决；同一规则传入市价执行与同向加仓复核，避免执行层
  再次恢复已经降级的回归 veto。这样不会放行普通单币信号，也不会放行本币动量不足或过热的旧计划。
  普通单币和三币宽度的预测反向仍直接拒绝，PA/订单流不得覆盖该方向冲突。计划通过全部门控后直接使用 `MARKET`
  入场，不再提交任何开仓限价单。真实成交后必须用成交均价再次复核费用后目标和整仓最大净损失，
  复核失败立即 fail-closed 平仓，并把真实手续费与净盈亏作为完整交易结果纳入统计。V4.6 的 6 次真实尝试要求额外等待
  2.00–4.50 bps 回撤，但对应等待窗口实际最大变化仅 0.09–0.84 bps，6 次全部未成交，
  因此 V4.10 删除了这层未经数据支持的额外价差。V4.9 进一步证明即使加入最优报价，4/4
  Testnet GTX 尝试仍以零成交撤销；V4.10 的首张市价兜底单成功建立并保护 XRP 仓位。V4.11
  根据账户所有者明确授权彻底删除开仓限价阶段，只保留市价成交后的真实价格复核；
- 每个方向用最近 120 根已闭合 1 分钟 K 线估计固定毛目标在未来 15 根内的历史触达率；普通趋势
  入口低于 20% 时拒绝，多币快速/持续宽度入口使用 2% 的最低可行性底线，并继续由预测方向、
  盘口和真实费用复核联合约束。该窗口不是持仓 15 分钟后平仓，也不使用当前时点之后的数据；

- 最近主动成交失衡至少达到 0.25 时确定多空方向；book imbalance 至少 0.03 或
  microprice 至少 0.10 bps 同向；
- 即使其中一项同向，book imbalance 反向超过 0.05 或 microprice 反向超过 0.25 bps
  也会一票否决，避免互相冲突的微观结构证据；
- 每个币仍维护最近 12 轮主动成交额中位数并记录诊断，但 Testnet 5 秒采样批次会出现不可复现的
  极端倍率，V4.17 不再用该倍率否决订单；成交方向失衡、盘口和 microprice 仍是实时硬门槛；
- 1 分钟、5 分钟 PA 均不得与入场方向相反，且至少一个分时周期必须同向；1 小时 PA 若明确反向
  则一票否决。当前点差不得超过
  5 bps；
- 综合质量分包含 1 分钟/5 分钟 PA 同向、效率、主动成交、盘口、microprice 和点差，必须
  不低于 2.00；原趋势入口相同方向必须连续出现 3 个评估轮次才可提交；
- 止损使用最近 5 根闭合 1 分钟 K 线极值加 0.10 ATR 缓冲；趋势入口若距离过近外扩至
  0.30%，快速入口外扩至 0.60%，若超过 1.20%则拒绝；
- 毛止盈按固定交易池的 Testnet 最大杠杆和执行成本设置：BTC 23 bps、ETH 26 bps、BNB
  28 bps、SOL 32 bps、XRP 25 bps。BTC、ETH、BNB 已按 V4.10 原生市价止盈滑点增加少量缓冲。
  下单前仍以实际数量、实际费率和 2 bps 不利滑点验证
  费用后目标至少 0.10 USDT；不满足时拒绝该单，而不是扩大仓位或降低净目标；
- 同一轮存在多个候选时，仍按质量排序，但 BTC、ETH、BNB、SOL、XRP 最多 5 个独立有效信号
  均可提交；活动币种或未通过确认的币不会为了凑数交易；
- 每个币最多一个仓位，活动仓位容量为 0–5 个不同币；5 是硬上限而不是目标，不确认时允许
  一直保留空槽，不会为了补满而降低条件。单笔保证金上限约 1 USDT；执行器每次读取
  Testnet leverage bracket，使用该币种当前允许的最高初始杠杆（当前候选约 50–125 倍）。
  系统按结构止损距离、双边 taker 手续费和 12 bps 风险定仓缓冲自动缩小保证金，使单笔
  预计净亏损不超过 1.00 USDT；实际成交后还会用真实入场价再次复核，超限立即拒绝继续持仓。
  目标净额仍按 2 bps 常规不利滑点估算；同币真实成交并平仓后至少冷却 60 秒；
- 活动仓位不再从信号评估中排除。只有通过与新仓相同质量、活跃度和连续轮次门控的“最新有效
  信号”才能交给该币种唯一的订单执行器；同一连续信号事件只分发一次，避免每 30 秒重复加仓。
  同向信号直接市价增加仓位；成交后以整仓数量和
  交易所报告的加权开仓价重新核算并替换原生止盈止损。加仓后的整仓预计净止损仍不得超过
  1.00 USDT，剩余风险不足交易所最小数量时拒绝加仓；
- 最新有效信号与持仓反向时，唯一执行器先撤销旧保护并市价平掉旧方向，将退出原因记录为
  `SIGNAL_REVERSAL`，然后绕过逐币平仓冷却直接市价提交已经确认的新方向。
  新方向仍受每日次数、每日净亏损、费用后目标和所有保护门控约束；低质量或未确认信号无权接管仓位；
- V4.14 将持仓失效与反向开仓拆开。若至少三币形成反向宽度，且持仓币在同一窗口的反向动量
  达到快速 2 bps 或持续 5 bps，立即以 `SIGNAL_INVALIDATION` 市价退出原仓，不再等待预测、
  活动倍率和目标触达率共同形成完整反向入场计划。该退出信号设置 `exit_only=true`，不会自动
  反手；新方向仍须独立通过全部入场规则；
- V4.15 起要求选中的市场宽度方向拥有该轮计划。已有单币计划若与多币宽度相反会被删除，不能再把
  旧多头计划带进空头市场或把旧空头计划带进多头市场；同向普通计划会提升为宽度计划并按 1 轮
  确认快速提交。V4.17 进一步要求旧计划的本币动量也必须落在当前宽度入口允许区间，不能借“已有
  趋势计划”绕过快速窗口的最低动量或 8 bps 尾端上限；
- 原生止盈完成后，如果同币仍出现同方向持续宽度，或至少 4 币同向的快速宽度，系统在下一次
  30 秒评估即可免除该币 60 秒冷却并再次入场。止损、结构失效、操作员退出、反方向或仅 3 币的
  弱冲量都不能使用该豁免；每日次数、每日亏损和全部下单/保护复核仍保留；
- 下单前按当前盘口、数量、实际 taker 费和 2 bps 不利滑点预估目标净额；低于 0.10 USDT
  直接拒绝，不再提交只有“蚊子腿”级费用后空间的仓位。趋势入口费用后目标净值/止损净损失必须
  至少达到 0.50；多币宽度入口使用与短目标/结构止损数学相容的 0.20。新仓、真实成交
  复核和同向加仓后的整仓都执行对应门槛；
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

## 持续运行与重启恢复

部署服务使用 `--duration-seconds 0` 持续运行，不再在三天后正常退出。campaign、用户数据流
和 Telegram 仪表盘均由 systemd 以 `Restart=always` 管理，并设有受控重试间隔和启动频率
上限。操作员执行 `systemctl stop` 时 systemd 仍会尊重显式停止，不会自行反复拉起。

Testnet API 凭据的持久副本位于 root-only 的
`/root/aiq-user-inputs/testnet/secrets/`，开机后由 `aiq-testnet-secrets.service` 以 `0400`
权限复制到易失的 `/run/ai-quant-secrets/`。campaign 和用户数据流都要求该 oneshot 服务先
成功，因而 VPS 重启后不需要人工重新填写 `/run`。

若 VPS 在持仓期间硬重启，Binance 原生止盈/止损在主机离线期间继续留在交易所。campaign
恢复后会先对账全部五个固定币种：唯一且完整的系统止盈/止损组合会被新 worker 接管，继续
等待原生退出并补记实际手续费与净结果；恢复仓位仍接受新的已确认反向信号，但不会在继承仓位
上自动加仓。若发现有持仓但保护不完整或混有未完成普通订单，系统会 fail-closed 市价平仓后
清理自身遗留订单。空仓时发现的系统遗留 Algo 单也会清理，并把恢复过程追加到证据日志。

检查自启动与依赖：

```bash
systemctl is-enabled aiq-testnet-secrets.service aiq-testnet-campaign.service \
  aiq-testnet-user-stream.service aiq-telegram-dashboard.service
systemctl list-dependencies aiq-testnet-campaign.service
systemctl status aiq-testnet-secrets.service aiq-testnet-campaign.service \
  aiq-testnet-user-stream.service aiq-telegram-dashboard.service
```

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
  --strategy TESTNET_EXPERIMENT_OF_PA_V5_6
```

少于 30 个已完成 V5 样本时报告固定为 `INSUFFICIENT_SAMPLE`，不能据此宣称策略有效。
2026-07-15 的实际结果、观测序列因果回放、参数小样本风险和旧结构代理交叉检查见
`docs/testnet-v3-backtest-review-20260715.md`。同名带单员的公开订单审查、固定小止盈回放和
BTC/ETH V4 重构边界见 `docs/strategy-v4-refactor-review-20260715.md`。
V4.11 止损样本的 Kronos shadow、逐笔反向反事实、费用后收益风险比和 V4.12 决策依据见
`docs/strategy-v4-12-kronos-audit-20260715.md`。
V4.12 零交易的 606 轮因果重放、Kronos 代表样本复核和 V4.13 快速入口依据见
`docs/strategy-v4-13-zero-trade-audit-20260715.md`。
V4.13 漏掉上涨、止损未及时失效及 V4.14 入场/持仓拆分依据见
`docs/strategy-v4-14-invalidation-audit-20260715.md`。
V4.15 的小时级方向 veto、宽度方向所有权、持续趋势优先和止盈后续做见
`docs/adr/0029-hourly-regime-and-target-continuation-v4-15.md`。
V4.16 将评估周期调整为 30 秒，并把快速/持续窗口按真实时间重标定为约 90/210 秒，见
`docs/adr/0030-thirty-second-cadence-v4-16.md`。
V4.17 对本轮下跌的生产/Testnet 同时间轴回放、持续趋势优先修复和强宽度预测所有权见
`docs/adr/0031-strong-breadth-forecast-authority-v4-17.md`。
V5.0 停用直接追单、改用“宽度建状态—回踩—再启动”的原因、回放结果和运行门控见
`docs/adr/0032-pullback-resumption-v5-0.md`。
V5.1 对多轮证据不同步、单边无回踩漏单、10 秒评估窗口重标定及 BTC 执行数学冲突的修复见
`docs/adr/0033-latched-evidence-continuation-v5-1.md`。
V5.2 对 V5.1 零止盈、手续费过高、预测反向仍入场、波段重复交易和自动反手的修复见
`docs/adr/0034-fee-aware-direction-authority-v5-2.md`。
V5.3 取消与质量无关的二仓/二候选/波段名额/时间冷却，并用独立信号事件身份防止 10 秒循环重复
提交，见 `docs/adr/0035-signal-owned-five-position-capacity-v5-3.md`。
V5.4 对 V5.3 三小时仅一笔且局部失效过迟的审计、强预测/订单流前置权限、费用下界目标和持仓
局部失败退出，见 `docs/adr/0036-predictive-flow-entry-and-local-exit-v5-4.md`。
V5.5 修复快速宽度配置为 3、实际入口仍硬编码 4 所造成的漏单，并为三币背景增加强预测流权限，
见 `docs/adr/0037-three-coin-predictive-fast-context-v5-5.md`。
V5.6 将主策略循环降为一分钟，并同步重标定所有轮数窗口，见
`docs/adr/0038-one-minute-cadence-v5-6.md`。

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
