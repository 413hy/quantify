# 契约使用说明

## 1. 契约边界

本目录定义服务之间、数据库事件账本和离线研究机之间的稳定交换格式。Schema 是实现的最低约束，不授予交易、实盘解锁或策略发布权限。

| 文件 | 消费者 | 作用 |
|---|---|---|
| `openapi.yaml` | 控制与通知服务、受控 CLI、运维客户端 | loopback/private REST；仅查询、暂停、撤单和二次确认紧急平仓 |
| `event-envelope.schema.json` | 全部事件生产者/消费者 | 公共事件信封、关联、序列和幂等字段 |
| `domain-events.schema.json` | 热路径、持久化、回放、审计 | 18 类领域事件的严格 payload |
| `market-decision-context.schema.json` | 实时引擎、Codex 编排器、规则引擎 | 脱敏、内容寻址、60 秒内有效的决策输入 |
| `trade-plan.schema.json` | Codex/规则分析器、独立风险层、执行编排 | 单笔完整交易参数或 `NO_TRADE`；不授予下单权 |
| `strategy-analysis-cycle.schema.json` | 调度器、authority lease、监控 | 30/20 分钟周期、Codex 全新 ephemeral 会话、唯一 holder、失败接管与三次恢复探针 |
| `model-selection-decision.schema.json` | 月度 selector、catalog 验证器、研究调度器 | 从当前账户 OpenAI Codex 官方 catalog 选择正式分析模型的内容寻址决定 |
| `auto-iteration-report.schema.json` | 月度研究、验证、自动发布控制器 | attempt、额度延期/FIFO、observe-only、离线门禁、Shadow、0.10/0.50、晋升或回滚证据 |
| `engineering-proposal.schema.json` | 月度研究、人工工程审查 | 非白名单改进建议；`auto_publish_allowed=false` |
| `strategy-package.schema.json` | 研究、发布工具、加载器 | 不可变策略内容、参数、数据边界、依赖与验证证据；不包含生命周期或签名 |
| `cost-model.schema.json` | 策略、风险、回放 | gross edge 估计、双向完整成本、证据和动态输入时效 |
| `execution-model.schema.json` | 执行、回放、Testnet | maker-first、队列成交、延迟、部分成交保护和未知状态语义 |
| `edge-decision.schema.json` | 策略、风险、订单意图 | 每次 ENTRY 的毛优势、成本分解、净优势和通过/拒绝裁决 |
| `strategy-health-report.schema.json` | 72 小时门禁、发布 CLI、审计 | 漏斗、setup 覆盖、成交/成本、漂移和 live/replay parity 报告 |
| `codex-review-report.schema.json` | 实现会话、独立复核会话、人工审批 | 变更分级、测试、缺陷、OOS 访问和验证重置裁决 |
| `strategy-approval.schema.json` | 人工批准工具、发布 CLI | 对策略包阶段转换的只追加签署记录与前驱链 |
| `operator-approval.schema.json` | 本机受控 CLI | 对实盘解锁、风险倍率、切包、解锁与秘密轮换的一次性签署授权 |
| `research-proposal.schema.json` | 离线 Codex、研究审批 | 含完整运行与结果的不可变 challenger 提案及样本污染防护 |
| `research-review.schema.json` | 人工研究/风险评审 | 独立、只追加、可验签的提案决定；不回写 ResearchProposal |
| `calibration-dataset-plan.schema.json` | 校准 launcher、登记服务、审计 | T0 前签署和登记的三日数据计划，固定 dataset、质量、OF/PA/标的池与失败策略 |
| `calibration-dataset-manifest.schema.json` | 采集、归档、研究导入 | 三日校准数据根、质量、远端验证与 project seal 的签名清单 |
| `validation-equivalence-profile.schema.json` | C2/C3 validation runner、发布 CLI | 外部/互斥验证机的精确一致项、唯一允许差异、资源/隔离/清理证明 |
| `of-calibration-search-plan.schema.json` | 三日校准研究机、参数候选验证器 | 首版全部可优化 OF alpha 参数键、类型/范围/步长、分桶、目标、成本、seed/预算/停止与禁止项 |
| `of-parameter-candidate.schema.json` | 校准研究机、策略打包器、验证 runner | 三日校准输出的封闭 OF 参数候选、全部 trial 证据、确定性选择和高过拟合风险标签 |
| `testnet-protocol-probe-plan.schema.json` | Testnet adapter、72 小时门禁、审计 | Testnet 自有行情/规则驱动的预登记协议探针、生产输入禁令、清理与必需证据 |
| `rate-budget-uds.schema.json` | 所有 Binance outbound caller、gateway、`rate-budget-service`、审计 | 宿主 Unix socket 上九类闭合消息：预约/裁决、permit 消费/裁决、发送结果、header、连接状态、server time 与交易所动态限额观测；不是 Binance User Data Stream |
| `enums.yaml` | 各语言模型、数据库迁移 | 稳定状态、动作和 reason code |
| `examples/` | CI、契约测试、实现者 | 可验证示例，不是真实市场或账户数据 |

示例中的 StrategyPackage、StrategyApproval、ResearchProposal/Review 与 OperatorApproval 构成首版 `release-20260716-c0` 的交叉引用链；`validation-equivalence-profile.json` 则是上线后另一次 `release-20260720-c0` C3 升级验证的独立示例，不是首次 live 准入证据。

## 2. 规范规则

- JSON Schema 使用 Draft 2020-12；REST 使用 OpenAPI 3.1.0。
- 所有时间为带 `Z` 或明确偏移的 UTC RFC 3339 字符串；数据库落为 `timestamptz`。
- 金额、价格、数量、PnL 和风险百分比在事件中使用十进制字符串，禁止二进制浮点。
- ID 接受 UUID 或 ULID；落库后不得重用。
- `idempotency_key` 在事件类型和生产者作用域内唯一；冲突 payload 不一致时必须进入事故流程。
- `occurred_at` 表示源事件时间，`received_at` 表示本服务接收时间；不得用接收时间替代因果时间。
- `source_sequence` 记录交易所或内部源序列；不适用时四个字段仍存在，序号为 `null`。
- `symbol` 和 `strategy_version` 不适用时显式为 `null`，不得静默省略。
- 所有生产事件均先通过 Schema，再以追加方式写入事实库；投影失败不得改写原事件。

### 2.1 规范化、hash 与签名

- RFC 8785 JSON Canonicalization Scheme（JCS）是所有内容 hash 和签署 payload 的唯一规范化算法；不得以缩进 JSON、键插入顺序或平台默认序列化字节计算。
- `StrategyPackage.package_hash = SHA-256(JCS(content))`；生命周期、批准和签名不得写入 `content` 后重算“同一个包”。
- `CostModel.model_hash = SHA-256(JCS(content))`、`ExecutionModel.model_hash = SHA-256(JCS(content))`、`EdgeDecision.edge_evaluation_hash = SHA-256(JCS(content))`、`StrategyHealthReport.report_hash = SHA-256(JCS(content))`、`CodexReviewReport.report_hash = SHA-256(JCS(content))`。示例 hash 必须可复算；结构合法但 hash 不符仍视为契约失败。
- `ResearchProposal.proposal_hash = SHA-256(JCS(content))`；人工决定只写入独立 `ResearchReview`。
- `MarketDecisionContext.context_hash`、`TradePlan.plan_hash`、`ModelSelectionDecision.decision_hash`、`AutoIterationReport.report_hash`、`EngineeringProposal.proposal_hash` 分别等于其 `content` 的 JCS SHA-256。任何引用对象必须逐字匹配，不接受模型复述或日志文本代替。
- `CalibrationDatasetPlan.plan_hash = SHA-256(JCS(content))`；`registration.payload_hash = SHA-256(JCS(registration.signed_payload))`。`plan_signature` 验证消息为 JCS 后的 `content`，登记签名验证消息为 JCS 后的 `registration.signed_payload`。
- `CalibrationDatasetManifest.manifest_hash = SHA-256(JCS(signed_payload))`；签名时必须绑定 data roots、质量裁决、远端回执根和停止后的 project seal。
- `ValidationEquivalenceProfile.profile_hash = SHA-256(JCS(signed_payload))`；签名 allowlist 禁止通配，逐字段 diff 中任何清单外差异都使 C2/C3 发布失败。
- `ValidationEquivalenceProfile.exact_match.execution_semantics_hash = SHA-256(JCS(execution_semantics_manifest))`；manifest 恰好含 `execution_model_hash`、`order_state_machine_hash`、`exchange_adapter_contract_hash`、`native_protection_policy_hash`、`cost_model_hash`，不能以人工命名的“不透明语义 hash”替代。PA 和 OF 分别直接绑定可复算的 Schema/config、search plan/candidate/parameter manifest hash。
- `OFCalibrationSearchPlan.plan_hash = SHA-256(JCS(content))`；`parameters` 是 closed object，缺少或增加参数键均失败，研究运行必须覆盖全部声明参数并保留所有 trial。
- `OFParameterCandidate.candidate_hash = SHA-256(JCS(content))`，`content.parameter_manifest_hash = SHA-256(JCS(content.parameters))`；签名验证消息为 JCS 后的 `content`，参数清单 hash 不能由显示格式或部分参数代替。
- `TestnetProtocolProbePlan.plan_hash = SHA-256(JCS(content))`；签名验证消息为 JCS 后的 `content`。独立 Testnet 预检和 72 小时 Testnet lane 每次启动都必须从原始工件重算，不能信任缓存的通过布尔值。
- `StrategyApproval`、`ResearchReview`、`OperatorApproval` 的 `payload_hash = SHA-256(JCS(signed_payload))`，签名验证的消息也是 JCS 后的 `signed_payload`。`schema_version`、`payload_hash` 和 `signature` 是工件外壳，不进入被签 payload。
- `signature_ref` 示例只证明结构有效，不证明密码学有效。实现验收必须用测试公钥验证算法、`key_id`、签名字节、吊销状态和签署时有效性；私钥不得进入仓库或生产容器。

### 2.2 Schema 之外仍必须执行的跨字段规则

JSON Schema 负责单工件结构与大部分判别分支，下列跨对象或算术不变式必须由应用、数据库约束和契约测试共同验证：

领域事件中的 Decimal 字符串采用唯一规范表示：UTF-8、十进制普通记法、无 `+`、无指数、零只能是 `"0"`、禁止 `"-0"`，小数末尾不得为 `0`，整数部分不得有前导零，最多 18 位小数。需要固定显示精度的值必须先按字段规则量化，再移除小数末尾的零；因此数学值 `0.5` 的唯一事件表示是 `"0.5"`，`"0.50"`、`"5e-1"` 和 `"-0"` 均非法。风险倍率、配置阈值等被 Schema 声明为枚举或 `const` 的字符串属于配置协议值，继续保留其固定书写形式。任何 JCS、幂等键或内容哈希都必须使用上述规范化后的领域事件值。

- `UniverseSnapshot.scoring_input_hash = SHA-256(JCS({"ranking_window_start": ..., "ranking_window_end": ..., "exchange_rules_version": ..., "symbols": [...] }))`；`symbols` 是按 UTF-8 symbol 升序排列的 `{symbol, raw_inputs}` 数组，且只含取得 exact 输入并进入 `ranking` 的 eligible symbol。`raw_inputs` 恰好使用闭合 15 根 1m kline 的 `trading_liquidity_quote_notional=Σk.q`、exact 双侧 depth 的 `book_depth_quote_notional=min(twap_bid,twap_ask)`、`spread_bps=median(一秒槽)`、`trade_event_count=Σk.n`、`input_completeness_pct=100×complete_slot_count/900`；字段名、大小写和 Decimal 字符串表示固定。缺 band/超 H=40 的候选进入 `excluded_candidates`，保留 nullable raw、排除阶段、H40 priority rank、原因和 evidence hash，不得补零后进入 `symbols`。`normalization_evidence` 保存每项 Type-7 q05/q95、方向和 eligible count；每个 `ranking` 项保存 raw、winsorized、0–100 normalized、weighted 与 score。事件幂等键固定为 `universe:<sha256(JCS({computed_at,score_model_version,source_manifest_hash,scoring_input_hash,exchange_rules_version}))>`。每项 weighted component 等于对应 normalized component 乘固定权重 `0.30/0.30/0.20/0.10/0.10`，`score` 等于五项之和。active/standby/managed 不得交叉重复，active 少于 10 时必须告警。
- `FeatureSnapshot` 的幂等摘要输入必须包含 `input_manifest_hash`；相同 manifest、策略版本与 feature schema 必须产生相同特征结果。
- `RiskDecision.primary_reason` 必须逐字等于有序 `reason_codes[0]`。ENTRY 的 `entry_mode=NEW_POSITION` 不带 position，`SCALE_IN` 必须带既有 position；所有 ENTRY 必须携带完整 `edge_evaluation`，批准时其 verdict 必须为 `PASS`；所有 exit kind 必须批准、`edge_evaluation=null`，并绑定对应仓位与事故/命令来源。
- `OrderIntent.intent_origin`、`entry_mode`、position/incident/operator 引用必须与前置 RiskDecision 完全一致。ENTRY 必须逐字携带同一 edge evaluation，且 `expected_gross_edge_bps-total_cost_bps=expected_net_edge_bps`；各成本分项之和必须等于 `estimated_total_cost_bps`。退出数量不得超过交易所当前绝对仓位，且必须 reduce-only 或合法 `closePosition`。
- `StrategyPackage` 的 cost/execution artifact 路径、schema hash 和 model hash 必须从实际文件重算并逐字一致；`edge_decision_schema_hash` 必须等于发布使用的精确 Schema 文件。任何不透明摘要或缺失工件都不能成为 champion。
- `CostModel` 中经验查找表属于冻结 release，不按墙钟自动更换；运行时必须分别检查 decision、盘口、手续费、exchange filters 与 funding 快照年龄。`EdgeDecision` 的 gross edge 减全部五类成本必须精确等于 net edge；maker 转 taker 必须生成新 decision，不得改写旧 decision。
- `StrategyHealthReport` 的漏斗计数必须单调不增，四类 setup 每类至少具备规定的正/负合成或回放覆盖；自然机会不足必须延长观察，不得以合成事件冒充自然观测。`CodexReviewReport` 必须满足实现者与复核者不同、开放 P0/P1 为零，并且 C0–C4 的 reset decision 与运行手册矩阵一致。
- 风险结论、订单意图、订单事件和操作命令均按 [API 与事件设计](../docs/06_API_AND_EVENT_CONTRACTS.md) 列出的语义对象计算 JCS SHA-256，再使用 `<type>:<64位摘要>`，不得直接拼接可能突破信封 200 字符上限的原字段。
- `OrderEvent` 幂等摘要按 transport 分支：STANDARD parent 使用 `exchange_order_id ?? client_order_id`；ALGO parent 使用 `algo_id ?? client_algo_id`；Algo 触发后的 ordinary child 使用 `actual_order_id` 并带 parent identity。成交 `source_event_key` 必须稳定包含 trade ID，其余更新使用稳定交易所更新键，不能只用本地接收时间。
- StrategyApproval 的 package ID/hash、前驱 ID/hash、action/stage/environment 必须形成连续链；StrategyApproval 与 OperatorApproval 均验证 `issued_at <= effective_at < expires_at`，不含 `effective_at` 的旧批准不得用于当前版本。ResearchReview 必须同时匹配 proposal ID/hash并验证 `reviewed_at < expires_at`。全部签名工件都要求 nonce 未使用且签名有效。`FREEZE_CHAMPION` 还要保证批准 `effective_at`、策略包 OOS 起点、`CHAMPION_FROZEN` 和 `GATE_TIMER_STARTED` 相同，并由单一事务消费。OperatorApproval 还要匹配执行前状态 hash，并以追加消费事实保证只消费一次。
- CalibrationDatasetManifest 的 `t1_exclusive-t0_inclusive` 必须精确 72 小时、`created_at/signature.signed_at` 晚于 T1，且 `signed_payload.data_quality.status=QUALIFIED` 时所有对象均已 `REMOTE_VERIFIED`。最终 C0 的 `data_manifest_hashes` 必须包含该 manifest hash，创建时间必须晚于其验签时间。
- CalibrationDatasetPlan 的 `registration.signed_payload.plan_hash` 必须等于顶层 `plan_hash`，其 collector/quality/OF search/PA/universe hash 必须逐字匹配实际预登记工件；`content.created_at <= plan_signature.signed_at <= registration.signed_payload.registered_at <= registration.signature.signed_at < resolved T0`。Manifest 的 dataset ID、dataset plan hash、登记 payload hash、OF/PA/质量 hash 必须反向匹配该计划，失败窗口不得复用 plan 或 dataset ID。
- OFCalibrationSearchPlan 的 `plan_hash` 必须在不可变 dataset plan/审计回执中绑定；计划签名时间和登记回执时间都必须早于校准 `T0`。仅回填 `created_at` 不构成预登记，任何 T0 后签名或换 hash 都使 dataset 与参数候选无效。
- OFParameterCandidate 的 dataset manifest/plan、OF search plan、PA Schema/config hash 必须逐字匹配其输入工件，`created_at` 和签名时间必须晚于已验签的校准 Manifest；重算的 `parameter_manifest_hash` 必须匹配，global/LOW/MEDIUM/HIGH 的每个值必须落在预登记网格与 scope。`executed = completed + failed + pruned <= planned`，所有 trial 均保留；选择必须按固定 evaluator、目标和第 1 名确定性回放一致，任一差异使候选无效且不得打包。
- TestnetProtocolProbePlan 必须满足 `content.created_at <= signature.signed_at < first_probe_started_at`，运行环境、行情、规则、价格和数量输入均只能来自同一 Testnet；生产 endpoint、secret、价格或过滤器命中任一项即失败。每次探针运行、72 小时 gate evidence 和最终 project seal 都必须绑定同一 `plan_hash`；closed Schema 声明的全部 case 与 required evidence 必须完整，结束时取消全部探针订单、零持仓、零对账差异并撤销临时凭据。429/418 只能由本地故障注入器模拟响应/headers，禁止主动轰击真实 Testnet；缺失、hash/签名不符或计划发生语义变化均 fail-closed。
- rate-budget UDS 的 caller 由 `SO_PEERCRED` 与签名 ACL 映射，不能自报 operation class。每个 `GRANTED` permit 绑定 caller、catalog hash、canonical request hash、causation、fencing epoch 与全部窗口 allocation，只可发送一次且 v1 不退回已授成本。`NOT_SENT` 必须无发送时间、HTTP 状态或响应 hash；已发送结果必须有 `sent_at`，UNKNOWN 保守计费至窗口结束。任何请求若无可关联 permit，网络层必须拒绝。
- ValidationEquivalenceProfile 必须证明 exact-match 字段与目标 release 一致、实际差异仅来自签名枚举路径、`unapproved_difference_count=0`；C3 的 Shadow 健康小时不少于 72，C2 具备预登记短期 Shadow，且均有 24h 网络/资源证据、零生产凭据/事实/控制通道和 Testnet 凭据撤销/临时 secret 清理证明。
- ResearchProposal 必须同时包含 baseline、1.5 倍成本和 ±20% 参数扰动运行；run ID 在提案内唯一。Profit Factor 分母为零时 `profit_factor_defined=false` 且值为 `null`，不得填无穷大或用其自动过门禁。
- `TradePlan` 必须在 `expires_at-created_at <= 60s` 且 `now <= expires_at` 时消费，绑定当前 `context_id/context_hash`、active strategy package、知识引用和唯一 authority fencing token；ENTRY 的数量由独立风险层按止损距离和风险上限计算，Codex 不直接指定绕过风控的最终数量。`expected_gross_edge_bps-expected_total_cost_bps=expected_net_edge_bps`，下单前价格/盘口/成本/规则任一重验证失败即拒绝。
- `StrategyAnalysisCycle` 每个 epoch 只能存在一个有效 holder。所有 `CODEX_PRIMARY` 周期和恢复 dry-run 都必须有新的 process/thread/workspace、ephemeral、零 resume、零历史 transcript/memory/reasoning 输入；`:00` AI 健康时规则为 shadow，`:30` AI 失败必须立即创建 rule fallback cycle。恢复 AI 需要 cooldown 后连续 3 次同样隔离的 dry-run 成功，并只能从下一 epoch 取得 authority。
- `ModelSelectionDecision` 的 selector 和正式分析必须使用不同 thread；选定模型逐字存在于未过期签名 catalog，provider 固定 `OPENAI_CODEX`。rank 1、selected model/effort、可用性和 catalog hash 的跨字段一致性由应用测试验证，不能接受模型自报 provider 或未列出的模型。
- `AutoIterationReport.iteration_cycle_id` 在月份内唯一，但同一 cycle 可有多个追加式 attempt。初始 90 天未通过只能 `OBSERVE_ONLY`；quota attempt 只能 `DEFERRED_QUOTA`，次日 `03:10 UTC` 以新会话重试并保持原 cycle/schedule/data cutoff，跨月严格 FIFO、全局并发 1、不得回退其他模型。候选必须只触及配置白名单并满足 ≥500 OOS、正净期望、PF ≥1.15、DD ≤5%、1.5 倍成本不为负、±20% 仍为正、单标的贡献 ≤40% 且移除后不为负、风险调整收益改善高于配置阈值。只有候选验证/灰度失败才回滚且同一 cycle 不再尝试候选晋升；quota 延期不是候选重试。

## 3. 版本兼容

`schema_version` 使用 `MAJOR.MINOR.PATCH`；每个发布的 closed Schema 文件只接受它声明的精确版本。当前事件信封/枚举为 `1.1.0`，会话编排、策略周期和月度报告等破坏性升级为各自 `2.0.0`，其余文件继续使用自身声明版本：

- `PATCH`：只修正文档、描述或不改变验证结果的示例；
- `MINOR`：新增可选字段或枚举值；由于本包使用 `additionalProperties:false` 与 closed enum，旧 validator 不会自动接受，必须先发布并部署显式支持新 minor 的消费者；
- `MAJOR`：删除/重命名字段、收紧必填、改变金额或状态语义。

生产者升级顺序固定为：消费者先兼容新旧版本 → 数据库迁移 → 生产者发新版本 → 观察完整窗口 → 删除旧兼容。订单事件和审计事件不得就地迁写；需要语义修正时追加 `CORRECTION` 审计记录，并保留原始字节和 hash。

未知版本一律不能由当前 closed Schema 验证。风险/执行消费者遇到未登记版本必须 readiness 失败并暂停新仓；滚动升级期间生产者继续发送所有已部署消费者共同支持的旧版本，直到消费者、数据库和回放器全部支持新版本后才切换。不得依赖“忽略未知字段”实现隐式兼容。

## 4. REST 权限边界

`openapi.yaml` 故意不定义以下端点：`LIVE_ARM`、`RESUME_NEW_ENTRIES`、`RISK_UNLOCK`、风险配置变更、策略包激活和秘密轮换。它们只能由本机受控 CLI 验证人工签名工件后执行。

Telegram 和飞书都只有单向通知出口，不调用任何 REST 查询或操作端点，也不注册 webhook、长轮询或 update handler。API 绑定 loopback 或受控私网；opaque session 在服务端绑定角色和来源，OpenAPI 的 `x-required-roles` 必须由授权中间件和 CI 校验。任何情况下都不得直接暴露到公网。

## 5. CI 验证要求

实现仓库必须用固定版本工具完成以下等价检查：

```bash
check-jsonschema --schemafile contracts/domain-events.schema.json contracts/examples/universe-snapshot.json contracts/examples/feature-snapshot.json contracts/examples/signal-no-trade.json contracts/examples/order-event.json
check-jsonschema --schemafile contracts/domain-events.schema.json contracts/examples/order-event-algo.json
check-jsonschema --schemafile contracts/market-decision-context.schema.json contracts/examples/market-decision-context.json
check-jsonschema --schemafile contracts/trade-plan.schema.json contracts/examples/trade-plan-no-trade.json
check-jsonschema --schemafile contracts/trade-plan.schema.json contracts/examples/trade-plan-entry.json
check-jsonschema --schemafile contracts/strategy-analysis-cycle.schema.json contracts/examples/strategy-analysis-cycle.json
check-jsonschema --schemafile contracts/model-selection-decision.schema.json contracts/examples/model-selection-decision.json
check-jsonschema --schemafile contracts/auto-iteration-report.schema.json contracts/examples/auto-iteration-report-observe-only.json
check-jsonschema --schemafile contracts/auto-iteration-report.schema.json contracts/examples/auto-iteration-report-deferred-quota.json
check-jsonschema --schemafile contracts/engineering-proposal.schema.json contracts/examples/engineering-proposal.json
check-jsonschema --schemafile contracts/strategy-package.schema.json contracts/examples/strategy-package.json
check-jsonschema --schemafile contracts/cost-model.schema.json contracts/examples/cost-model.json
check-jsonschema --schemafile contracts/execution-model.schema.json contracts/examples/execution-model.json
check-jsonschema --schemafile contracts/edge-decision.schema.json contracts/examples/edge-decision.json
check-jsonschema --schemafile contracts/strategy-health-report.schema.json contracts/examples/strategy-health-report.json
check-jsonschema --schemafile contracts/codex-review-report.schema.json contracts/examples/codex-review-report.json
check-jsonschema --schemafile contracts/research-proposal.schema.json contracts/examples/research-proposal.json
check-jsonschema --schemafile contracts/strategy-approval.schema.json contracts/examples/strategy-approval.json
check-jsonschema --schemafile contracts/research-review.schema.json contracts/examples/research-review.json
check-jsonschema --schemafile contracts/operator-approval.schema.json contracts/examples/operator-approval.json
check-jsonschema --schemafile contracts/calibration-dataset-plan.schema.json contracts/examples/calibration-dataset-plan.json
check-jsonschema --schemafile contracts/calibration-dataset-manifest.schema.json contracts/examples/calibration-dataset-manifest.json
check-jsonschema --schemafile contracts/validation-equivalence-profile.schema.json contracts/examples/validation-equivalence-profile.json
check-jsonschema --schemafile contracts/of-calibration-search-plan.schema.json contracts/examples/of-calibration-search-plan.json
check-jsonschema --schemafile contracts/of-parameter-candidate.schema.json contracts/examples/of-parameter-candidate.json
check-jsonschema --schemafile contracts/testnet-protocol-probe-plan.schema.json contracts/examples/testnet-protocol-probe-plan.json
check-jsonschema --schemafile contracts/rate-budget-uds.schema.json contracts/examples/rate-reserve-request.json
check-jsonschema --schemafile contracts/rate-budget-uds.schema.json contracts/examples/rate-reserve-decision.json
check-jsonschema --schemafile contracts/rate-budget-uds.schema.json contracts/examples/rate-send-outcome.json
check-jsonschema --schemafile contracts/rate-budget-uds.schema.json contracts/examples/rate-header-observation.json
check-jsonschema --schemafile config/binance-endpoint-cost-catalog.schema.json config/binance-endpoint-cost-catalog.example.json
openapi-generator-cli validate -i contracts/openapi.yaml
```

在验证示例前，CI 必须对每个 `*.schema.json` 执行 Draft 2020-12 metaschema 检查，并独立重算上节全部 JCS hash。CI 还须从 `enums.yaml` 生成或比对语言枚举，检查 OpenAPI path、事件 `event_type` 和数据库 enum/check constraint 一致；对每个 discriminated union 至少保留一个“合法分支通过、交叉污染分支失败”的负例。生成代码是派生产物，禁止反向覆盖这些源契约。

## 6. 相关文档

- [API 与事件设计](../docs/06_API_AND_EVENT_CONTRACTS.md)
- [数据与数据库](../docs/05_DATA_AND_DATABASE.md)
- [风控与执行](../docs/04_RISK_AND_EXECUTION.md)
- [Codex 研究流程](../docs/07_CODEX_RESEARCH_WORKFLOW.md)
- [AI 策略编排与自动迭代](../docs/14_AI_STRATEGY_ORCHESTRATION_AND_AUTO_ITERATION.md)
