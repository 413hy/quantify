# 11 月度 Codex 动态选模、额度延期、自动审查、灰度与回滚手册

## 目的

在初始 90 天正式门禁通过后，每月由独立新会话 selector 从当前账户签名官方 Codex model catalog 选择适合本次任务的模型，再由另一个全新会话检查策略是否存在可重复、可控的改进空间。仅字段级白名单候选可自动进入离线验证、Shadow、`0.10/24h`、`0.50/24h` 并晋升；候选失败自动恢复上一 champion。selector 或 analysis 额度不足只延期，不产生 challenger、不改变 champion、不切换较弱模型。本文中的命令是 VPS Codex 必须实现的受控 CLI 契约，不是本交付包已经执行的部署命令。

本流程不等待 Telegram 确认。Telegram/飞书只接收阶段通知，不能启动、停止、批准、晋升或回滚 cycle。

## 1. 调度、唯一性与积压队列

- 时区固定 UTC，计划为每月 1 日 `03:10`。
- `iteration_cycle_id` 固定为计划月份的 `YYYY-MM`，数据库唯一约束禁止同月第二个 cycle。
- 首次登记同时冻结 `scheduled_at=<YYYY-MM-01T03:10:00Z>` 与 `data_cutoff_at`；后续 attempt 不得推进数据截止点。
- 普通调度错过时只补跑一次；补跑仍使用原计划月份 ID。额度延期属于独立 attempt 语义，可每日同一时刻重试，不受“普通补跑一次”限制。
- 调度器先取得持久化 cycle fencing lease，并按最旧 `iteration_cycle_id` FIFO 取队首；全局最多一个月度 attempt 运行。
- 旧 cycle 因额度延期跨月时，新 cycle 仍登记但状态为 `QUEUED_BEHIND_QUOTA`，不得抢占、并行或使用更新数据替代旧 cycle。
- 已有 `RUNNING/COMPLETE/NO_CHANGE/REJECTED/ROLLED_BACK/PROMOTED` 终态时不得重复创建。`DEFERRED_QUOTA` 是 cycle 非终态、attempt 终态。
- 初始 90 天统计门禁未通过，cycle 强制为 `OBSERVE_ONLY`：可审计和生成报告，不得生成可发布 challenger、不得进入 Shadow/灰度。

```bash
quantctl iteration start \
  --policy /etc/ai-quant/auto-iteration.yaml \
  --cycle-id "<YYYY-MM>" \
  --scheduled-at "<YYYY-MM-01T03:10:00Z>" \
  --catch-up-once \
  --require-unique \
  --queue-order fifo-oldest-cycle \
  --global-max-concurrency 1
```

## 2. 前置检查

开始前必须全部为真：

1. 独立研究/回测机可用，使用独立 `CODEX_HOME` 和只读研究沙箱，无生产 Binance/Telegram/数据库写凭据；月度型号不硬编码。
2. champion package、运行配置、成本/执行模型、知识卡、数据 manifest 和代码/镜像摘要均可复算。
3. 数据用途标签、初始 3 天校准集和受保护 87 天 OOS 边界完整；月度训练/验证分区不回写或污染受保护样本。
4. 生产零未解决订单差异、零无保护持仓、零开放 P0/P1；数据库和归档可写。
5. 当前 cycle 是 FIFO 队首；不存在另一个运行 attempt 或持有发布/回滚 lease 的任务。
6. 临时 run 根目录为空或为本次新建；启动器强制新 process/thread/workspace、ephemeral，禁止 resume、历史 transcript/memory/reasoning 和未批准文件。

```bash
quantctl iteration preflight \
  --cycle-id "<YYYY-MM>" \
  --require-independent-research \
  --require-clean-oos-boundaries \
  --require-zero-p0-p1 \
  --require-zero-order-diff \
  --require-protection-healthy \
  --require-fresh-ephemeral-sessions \
  --require-fifo-head
```

非额度前置失败：写入 `AutoIterationReport.final_decision=REJECTED` 或 `OBSERVE_ONLY`，发送通知，保持当前 champion，不影响实时 30/20 分钟双引擎。额度不足不得在此归为普通 `REJECTED`，按第 3.3 节延期。

## 3. 签名 catalog、两阶段新会话与额度延期

### 3.1 生成当前账户官方 Codex catalog

使用发布 manifest 锁定版本和摘要的 Codex CLI 运行 catalog 诊断，规范化后立即签名：

```bash
quantctl iteration catalog snapshot \
  --cycle-id "<YYYY-MM>" \
  --attempt-id "<ATTEMPT_ID>" \
  --command "codex debug models" \
  --require-current-account \
  --provider OPENAI_CODEX \
  --ttl-seconds 3600 \
  --sign \
  --output "<EVIDENCE_DIR>/model-catalog.json"
```

`codex debug models` 是实验性诊断表面。CLI 命令不存在、输出结构变化、provider/model 字段无法闭合验证、签名无效或快照过期时必须 fail-closed 并创建事故，禁止用旧文档示例、缓存型号或自由字符串继续。

### 3.2 新 selector 会话与新 analysis 会话

selector 只能读取签名 catalog、任务描述/复杂度、冻结数据规模、批准预算以及结构化额度/限速事实。它不读取旧对话，也不持有发布或生产写权限：

```bash
quantctl iteration select-model \
  --cycle-id "<YYYY-MM>" \
  --attempt-id "<ATTEMPT_ID>" \
  --catalog "<EVIDENCE_DIR>/model-catalog.json" \
  --fresh-process --fresh-thread --fresh-workspace --ephemeral \
  --forbid-resume --forbid-history --forbid-memory --forbid-reasoning-reuse \
  --output-schema /etc/ai-quant/contracts/model-selection-decision.schema.json \
  --output "<EVIDENCE_DIR>/model-selection-decision.json"
```

确定性验证器必须确认 selection 的 provider 为 `OPENAI_CODEX`、selected model 逐字存在于 catalog、reasoning effort 合法、catalog/task/cycle/attempt hash 匹配，且新会话证据完整。然后销毁 selector process/thread/workspace；analysis 不得读取 selector transcript、tool trace 或 reasoning。

```bash
quantctl iteration analyze \
  --cycle-id "<YYYY-MM>" \
  --attempt-id "<ATTEMPT_ID>" \
  --selection "<EVIDENCE_DIR>/model-selection-decision.json" \
  --data-cutoff "<ORIGINAL_DATA_CUTOFF_AT>" \
  --fresh-process --fresh-thread --fresh-workspace --ephemeral \
  --forbid-resume --forbid-history --forbid-memory --forbid-reasoning-reuse \
  --output-schema /etc/ai-quant/contracts/auto-iteration-report.schema.json
```

每个命令 manifest 保存 run/thread/workspace ID、CLI/model/reasoning、输入/prompt/command/output hash、ephemeral 与 cleanup 结果；不保存 transcript、memory 或 reasoning 内容。selector 与 analysis 的 run/thread/workspace 必须全部不同。

### 3.3 额度不足处理

只接受官方客户端/SDK 的结构化 quota/rate-limit 分类、结构化进程状态或批准健康探针；无官方剩余额度查询时记录 `UNKNOWN`，不得编造 token 数。selector 或 analysis 明确额度不足时运行：

```bash
quantctl iteration defer-quota \
  --cycle-id "<YYYY-MM>" \
  --attempt-id "<ATTEMPT_ID>" \
  --category "<SELECTOR_QUOTA|ANALYSIS_QUOTA>" \
  --preserve-scheduled-at \
  --preserve-data-cutoff \
  --keep-champion-unchanged \
  --forbid-model-fallback \
  --next-retry "<NEXT_DAY_T03:10:00Z>"
```

必须生成 attempt 级 `final_decision=DEFERRED_QUOTA`，`challenger_package_hash=null`，零发布/灰度状态变化。不得 resume 失败会话，不得在同一 attempt 依次尝试其他模型。次日 `03:10 UTC` 创建新 attempt、新 catalog、新 selector 和新 analysis 会话，但继续使用原 `iteration_cycle_id/scheduled_at/data_cutoff_at`。额度延期没有重试次数上限，但每天最多一次；跨月严格 FIFO、全局单并发。额度延期不是候选门禁失败，因此不触发本 cycle 的候选重试封禁。

## 4. 自动审计与候选分类

月度 Codex 读取最近 30 天运行证据和滚动 90 天批准数据，审计：信号漏斗、PA/OF 一致性、拒绝原因、成本漂移、maker/taker、滑点/不利选择、标的集中度、参数稳定性、数据健康、资源、AI/规则差异和事故复盘。

允许自动改变的路径只有：

- `price_action.bounded_parameters`
- `order_flow.bounded_parameters`
- `universe.weights`、`universe.debounce`
- `codex.strategy_prompt`
- `knowledge.retrieval_weights`、`knowledge.card_combinations`
- `entry.non_hard_risk`、`cost.non_hard_risk`、`position_management.non_hard_risk`

硬风险上限、账户/密钥、执行代码、订单状态机、工具白名单、网络、数据库、容器基础设施和未定义策略类型只能生成 `EngineeringProposal`；自动发布器必须拒绝。

```bash
quantctl iteration classify \
  --cycle-id "<YYYY-MM>" \
  --policy /etc/ai-quant/auto-iteration.yaml \
  --candidate "<CANDIDATE_PACKAGE_PATH>" \
  --emit-engineering-proposals "<EVIDENCE_DIR>/engineering"
```

若没有统计上合理的白名单改进，终态为 `NO_CHANGE`。不能为了“每月必须改”而放宽阈值或制造候选。

## 5. 离线门禁

候选必须同时满足：

- 样本外交易数 `>=500`；
- 扣除全部费用后净期望 `>0`；
- Profit Factor `>=1.15`；
- 最大回撤 `<=5%`；
- 1.5 倍全部成本压力下期望 `>=0`；
- 所有白名单参数上下扰动 20% 后期望仍 `>0`；
- 单一标的收益贡献 `<=40%`；移除贡献最高标的后期望仍 `>=0`；
- 风险调整后改善严格高于签名配置的 `risk_adjusted_improvement_gt`；
- 数据、成本、执行和回放语义与候选 manifest 可复算，OOS 访问日志无污染。

```bash
quantctl iteration offline-gate \
  --cycle-id "<YYYY-MM>" \
  --candidate "<CANDIDATE_PACKAGE_PATH>" \
  --policy /etc/ai-quant/auto-iteration.yaml \
  --require-deterministic-replay \
  --require-oos-access-log \
  --output "<EVIDENCE_DIR>/offline-gate.json"
```

任一不满足即 `REJECTED`，本 cycle 不进入 Shadow，也不得调低阈值或创建第二个候选重试。

## 6. Shadow 阶段

离线通过后在独立、等效 validation 环境运行至少连续 72 个健康小时。使用生产公开行情和 Paper 账本，无生产 key；测试 Top10、PA/OF、Codex/规则双引擎、authority、成本、资源和故障注入。

```bash
quantctl iteration promote-stage --cycle-id "<YYYY-MM>" --to SHADOW --risk-multiplier 0
quantctl gate run-shadow --cycle-id "<YYYY-MM>" --candidate "<CANDIDATE_PACKAGE_PATH>" --minimum-healthy-hours 72
```

通过条件至少包括：零重复订单意图、零双 authority、零未解释决策差异、零无保护 Paper 持仓、所有 AI 失败立即规则接管、资源/时延在签名阈值内、P0/P1 为零。

## 7. 自动实盘灰度

进入灰度前必须再次确认 champion 与 challenger 摘要、交易所规则、账户模式、保护、订单/持仓和数据库均一致。灰度只改变新仓 owner；旧持仓继续由其原策略版本管理。

### 7.1 `0.10` 阶段

```bash
quantctl iteration promote-stage \
  --cycle-id "<YYYY-MM>" \
  --to LIVE_0_10 \
  --risk-multiplier 0.10 \
  --minimum-healthy-hours 24 \
  --automatic
```

从 challenger 第一笔真实订单被交易所接受时起计算连续 24 个健康小时。任一 P0/P1、订单差异、保护失败、authority 冲突、成本或数据门禁失败立即回滚。

### 7.2 `0.50` 阶段

```bash
quantctl iteration promote-stage \
  --cycle-id "<YYYY-MM>" \
  --from LIVE_0_10 \
  --to LIVE_0_50 \
  --risk-multiplier 0.50 \
  --minimum-healthy-hours 24 \
  --automatic
```

第二阶段不得复用第一阶段的计时。要求新增的 24 小时仍满足订单、保护、风险、资源、数据和统计运行阈值。

### 7.3 晋升 `1.00`

```bash
quantctl iteration promote-stage \
  --cycle-id "<YYYY-MM>" \
  --from LIVE_0_50 \
  --to PROMOTE \
  --risk-multiplier 1.00 \
  --automatic \
  --require-all-evidence
```

发布控制器原子写入新 champion、authority package hash、发布证据和通知 outbox。`1.00` 仍只是硬上限倍率，不是目标仓位。任何步骤不能通过 Telegram、普通 REST 或 Codex 文本直接触发；只能由确定性控制器消费已签名、Schema 合法且全部门禁通过的 `AutoIterationReport`。

## 8. 自动回滚

以下任一项触发回滚：门禁失败、超时、候选摘要变化、越过白名单、P0/P1、订单差异、无保护持仓、双 authority、数据库不可写、成本/数据/资源超阈值或发布事实无法原子提交。

```bash
quantctl iteration rollback \
  --cycle-id "<YYYY-MM>" \
  --restore-package-hash "<PREVIOUS_CHAMPION_HASH>" \
  --revoke-challenger-new-entry-authority \
  --preserve-position-ownership \
  --reconcile \
  --forbid-same-cycle-candidate-retry
```

回滚顺序：撤销 challenger 新仓 authority → 恢复上一 champion → 保留既有持仓 owner → 验证原生保护 → 全量对账 → 写 `ROLLED_BACK` 事实 → 发送通知。回滚失败立即 `RISK_LOCKED`、升级 P0/P1，由人工按事故手册处置。

## 9. 通知

只发送以下脱敏通知：cycle/attempt 开始、模型选择摘要、`DEFERRED_QUOTA` 与下次重试、`QUEUED_BEHIND_QUOTA`、额度恢复、observe-only/no-change、候选摘要、离线门禁结果、Shadow 结果、0.10/0.50 开始与结束、晋升、回滚、EngineeringProposal、隔离/catalog 事故。消息不含密钥、完整余额、完整提示内容、原始上下文、selector/analysis transcript/reasoning、仓位明细或可重放签名。

通知失败写 outbox 并重试，但不阻塞门禁、晋升或回滚；不得因通知不可达延长危险 challenger 的 authority。

## 10. 封存与验收

```bash
quantctl iteration finalize \
  --cycle-id "<YYYY-MM>" \
  --report-schema /etc/ai-quant/contracts/auto-iteration-report.schema.json \
  --evidence-root "<EVIDENCE_DIR>" \
  --write-manifest \
  --sync-audit-archive
```

- [ ] cycle ID 唯一，普通漏跑最多补一次；quota retry 使用同 cycle 的新 attempt，不与普通补跑混淆。
- [ ] 原 `scheduled_at/data_cutoff_at` 在全部延期 attempt 中不变；跨月 FIFO、全局并发 1。
- [ ] catalog 来自当前账户官方 Codex、签名/有效期/CLI 版本可验；命令或结构变化时 fail-closed。
- [ ] selector 与 analysis 使用不同的新 process/thread/workspace、ephemeral；无 resume、旧 transcript/memory/reasoning；工作区清理证据完整。
- [ ] `ModelSelectionDecision` 只选择 catalog 中的 model/reasoning，analysis 只接收结构化 selection，不接收 selector transcript。
- [ ] selector/analysis 额度不足时报告为 `DEFERRED_QUOTA`、无 challenger、Champion 不变、不尝试弱模型；次日 03:10 UTC 才重试。
- [ ] 初始 90 天前只有 `OBSERVE_ONLY`。
- [ ] 所有 changed fields 均命中白名单；其余只产生 EngineeringProposal。
- [ ] 离线阈值、72h Shadow、0.10/24h、0.50/24h 证据齐全且摘要可复算。
- [ ] 晋升或回滚是单一终态；候选失败 cycle 不能再晋升；额度延期不是候选失败。
- [ ] 全程无生产 secret 进入研究环境，无 Telegram 入站控制。
- [ ] catalog/selection/attempt/queue/quota、最终报告、阶段事实、OOS 访问日志、通知结果和 `MANIFEST.sha256` 已远端封存。
