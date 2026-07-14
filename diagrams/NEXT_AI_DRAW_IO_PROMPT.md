# next-ai-draw-io 架构图继续修改提示词

你正在修改一套 AI 量化交易系统的**文档架构图**，不是在开发、连接或部署交易系统。只修改 `diagrams/AI_QUANT_SYSTEM_ARCHITECTURE.drawio`、同名 SVG、必要的 `diagrams/README.md` 与本提示词。不得改动实现、账户、外部系统或其他规范文档。

## 1. 开始前必须读取

按顺序完整读取：

1. `VPS_CODEX_START_HERE.md`
2. `docs/02_SYSTEM_ARCHITECTURE_AND_ADR.md`
3. `docs/05_DATA_AND_DATABASE.md`
4. `docs/07_CODEX_RESEARCH_WORKFLOW.md`
5. `docs/10_DOCKER_VPS_DEPLOYMENT.md`
6. `docs/11_SECURITY_OPERATIONS_AND_DR.md`
7. `docs/12_MAINTENANCE_UPGRADE_ROLLBACK.md`
8. `docs/14_AI_STRATEGY_ORCHESTRATION_AND_AUTO_ITERATION.md`
9. `runbooks/11_MONTHLY_AUTO_ITERATION.md`
10. `diagrams/README.md`

权威顺序是：用户冻结决策 > 当前官方接口事实 > `VPS_CODEX_START_HERE.md` 与编号规范文档 > 架构图。若图与规范冲突，先报告冲突和受影响页面；不得用图覆盖规范。

## 2. 输出契约

- 保持 `.drawio` 为标准、未压缩 mxGraph XML：`<mxfile>` 下有三个 `<diagram>`，每个 `<diagram>` 直接包含 `<mxGraphModel>`。
- 保留三个页面及页面名：`01 生产架构`、`02 数据·研究·发布`、`03 阶段·信任边界`。
- 保留稳定节点 ID；新增节点使用 `p1-`、`p2-`、`p3-` 前缀。
- 更新 `AI_QUANT_SYSTEM_ARCHITECTURE.svg`，使其仍是三页核心内容纵向组合的可读静态 SVG。
- 所有标签使用中文，关键服务、事件和 Binance 字段保留英文原名。
- 不引入未完成占位符，不包含真实账户、IP、主机名、API Key、secret、Bot Token、Chat ID、签名私钥或客户端连接配置。
- 生成的文件必须可由 XML 解析器读取，并可在 diagrams.net/draw.io 中继续编辑。

## 3. 不可更改的系统事实

1. 生产为韩国单台 Ubuntu 24.04 LTS VPS：2 vCPU、12 GiB RAM、约 200 GB NVMe；明确不是 HA。
2. 单用户、单 Binance USDⓈ-M Futures 账户，One-way Mode、Cross Margin。
3. 市场/执行热路径确定性；生产 Codex 每 `:00/:30 UTC` 都以全新 process/thread/workspace、ephemeral、禁止 resume/旧 transcript/memory/reasoning，通过受限只读工具生成一个 `TradePlan/NO_TRADE`，规则 PA+OF 每 `:00/:20/:40` 热备。每个 epoch 只有一个 authority；AI 失败立即规则补位。
4. PA 使用已闭合 5m/1m bar 给背景、结构和方向；100ms 至秒级 Order Flow 给触发；冲突即 `NO_TRADE`。
5. 动态池固定 Top 10，每 15 分钟刷新；Top10、候补 11–15 和持仓管理集使用完整深度/成交。
6. `realtime-engine` 的行情、订单簿、特征、策略、独立风险位于同一热路径进程。
7. 只有 `execution-service` 可读取 Binance 交易 secret；它负责 preflight、幂等、User Data Stream/REST 对账、未知状态和交易所原生保护。
8. PostgreSQL 16 + TimescaleDB 是订单、成交、审批、版本和审计事实源；Redis 7 永远不是事实源。
9. `control-service` 提供 loopback/private FastAPI；Telegram 和飞书都只接收脱敏通知，无 webhook、长轮询、入站命令、查询或交易确认。
10. 公网入站只有 SSH；管理面经 SSH 隧道。外部心跳是 monitoring 主动发送带 timestamp、nonce 和 Ed25519 签名的 push，接收端无反向控制能力。
11. 主机失联时依赖交易所原生保护单和外部告警；不画自动跨机交易故障转移。
12. `aiq-host-control` 是第一个独立于四个阶段业务 Compose project 的持久宿主 project。它只包含 `rate-budget-service`、专用 PostgreSQL database `aiq_host_rate_control` 和 Unix domain socket（UDS）`/run/ai-quant-rate/rate.sock`；不承载业务事实，也不读取 Binance secret。
13. `aiq-binance-egress` 是第二个持久宿主 project。它只包含唯一 `binance-egress-gateway` 与 UDS `/run/ai-quant-egress/gateway.sock`；gateway 无 API secret、无签名能力，却是宿主上唯一可解析 Binance 域名、建立 socket/TLS、发送 REST/WS API/market-stream control 和 relay 入站流量的组件。
14. 所有业务容器必须没有 Binance 网络路由。业务先向 allocator 发出 `ReserveRequest`，再将有界请求交给 gateway；gateway 从实际 host/method/path/params/wire/operation facts 重算哈希并自行发出 `PermitConsumeRequest`，只有 `CONSUME_GRANTED` 后才发送一次。禁止业务消费 permit 后直连、旁路、批量复用或降级为进程内限流。
15. 只有 `execution-service` 可读取 Binance API secret，并负责生成短时、不可变的预签名私有请求；gateway 只能校验、消费 permit 和发送，不得读取 secret 或生成签名。
16. allocator、`aiq_host_rate_control`、任一 UDS、gateway、业务事实数据库或必需签名工件任一故障时，所有阶段都 fail-closed 为零新 Binance 出站。已建立的入站 relay 最多维持到自然断开，禁止新连接、重连、订阅、取消订阅和主动 ping/pong；只依赖已经在交易所生效的原生保护单。
17. 生产 Codex 首版固定 `gpt-5.6 + medium`、90 秒超时、60 秒输出 TTL、独立 `CODEX_HOME`、每周期 fresh ephemeral 会话、只读 sandbox 和 closed Schema；无 Binance secret/route、执行 UDS、数据库写、任意 shell/浏览器/网络或容器控制。
18. 月度研究每月 1 日 03:10 UTC 在独立研究环境登记 cycle：签名当前账户官方 Codex catalog → fresh selector 输出 `ModelSelectionDecision` → 销毁 selector → 另一 fresh analysis 会话。额度不足每日 03:10 延期，保持原 cycle/schedule/data cutoff，跨月 FIFO、全局并发 1，禁止弱模型 fallback。初始 90 天前仅 `OBSERVE_ONLY`；之后只有字段白名单候选可经离线、Shadow、0.10/0.50 灰度自动晋升，候选失败自动回滚且本 cycle 不重试。

本提示词中的 `UDS` 在 `rate.sock` 和 `gateway.sock` 语境下只指 Unix domain socket。Binance User Data Stream 必须写全称，避免两者混淆。

## 4. 第一页：生产架构

必须表现：

- 韩国 VPS 资源与非 HA 声明；
- `realtime-engine` 内的 Market Gateway → Local Order Book → Feature Engine → Decision Scheduler；Scheduler 连接 Codex Primary 与 Rule Fallback，经唯一 authority 输出到 Independent Risk；
- Codex 只连接固定 OpenAI HTTPS authority；与 Binance gateway、execution network 和 secret 完全隔离；
- Universe 的全市场轻量排名、Top 10/15 分钟、候补和持仓管理集；
- 已持久化 `OrderIntent` 通过窄 Unix domain socket 进入 `execution-service`；
- `execution-service` 生成短时不可变预签名请求并交 gateway；它不能直接连 Binance；
- `persistence-worker` 写 PostgreSQL；Redis 用虚线标为非事实缓存；
- `archive-service`、原始 L2、monitoring、control-service；
- 位于 `aiq-live` 边界外的两个独立持久 project：`aiq-host-control`（allocator + 专用 PG + `rate.sock`）与 `aiq-binance-egress`（唯一 gateway + `gateway.sock`）；
- 业务服务到 `rate.sock` 的 Reserve 关系、业务到 `gateway.sock` 的 request/relay 关系，以及 gateway 到 `rate.sock` 的 PermitConsume 关系；
- 只有 gateway 与 Binance 之间存在网络边；业务边界明确标注“零 Binance route”；
- allocator、专用 database、任一 socket、gateway 或业务事实数据库失效时“零新 Binance 出站；既有交易所原生保护单继续”的 fail-closed 关系；
- 操作员、本机签名 CLI、Telegram/飞书单向通知、外部心跳和固定 SFTP 的边界；
- 除执行服务外没有任何容器连接生产 secret。

异常语义以 fail-closed 为准：订单簿无效、时钟漂移、数据库不可写、未知订单、保护缺失或资源危险时不允许新仓；保护/账户/风险异常按规范升级为撤单和平仓。

## 5. 第二页：数据、研究与发布

必须表现生产数据流：

```text
全部有效 USDT 永续轻量数据
  → Universe Scorer（30% 流动性 + 30% 深度 + 20% 反向点差 + 10% 活跃度 + 10% 数据健康）
  → Top 10 / 候补 11–15 / 持仓管理集
  → 完整 L2 与成交
  → 小时 Parquet + Zstd + 明文 SHA-256
  → age v1/X25519 加密
  → 固定 SSH/SFTP 接收端
  → 远端解密、Parquet/行数/哈希检查和 Ed25519 签名逐对象回执
```

VPS 原始 L2 最多保留 72 小时或 80 GB，先到者；只有 `REMOTE_VERIFIED` 对象可以删除。远端同步失败时停止删除并告警。PostgreSQL 备份、WAL/PITR、订单/审计导出显示 RPO ≤1 小时、RTO ≤4 小时，恢复后固定 `RISK_LOCKED`。

生产/采集域还必须显示完整出站链：采集业务先 Reserve，再将 connect/subscribe/REST/control 请求交 gateway；gateway 自行 PermitConsume 后唯一发送。`aiq_host_rate_control` 必须与交易 PostgreSQL 分开标注，`aiq-binance-egress` 也必须与业务 project 分开；独立研究域不得连接两条 socket、宿主数据库或 gateway。控制面故障时采集域零新 Binance 出站，图中不得暗示研究机可代发请求。

研究域必须与生产写域隔离：独立回测机接收已校验数据，运行回放、回测、walk-forward、成本压力和 pgvector 派生索引；月度 Codex 无 Binance key、无生产 DB/Redis/UDS 写权。研究链分为自动白名单与人工工程两支：

```text
签名官方 Codex catalog
  → fresh selector / ModelSelectionDecision
  → fresh analysis（月度审计）
  ↘ quota insufficient → DEFERRED_QUOTA → 次日 03:10 / FIFO / 并发 1
  → 白名单 challenger
  → 离线统计门禁
  → Shadow
  → 0.10 / 24h
  → 0.50 / 24h
  → 自动晋升 1.00 或自动回滚

非白名单建议
  → EngineeringProposal
  → 独立人工工程审查
  → 常规 C2/C3 发布路径
```

只有完整月度白名单阶段证据能由确定性发布控制器改变 Active 版本；Codex 文本、Telegram、普通 REST、EngineeringProposal 或失败报告均不能。

## 6. 冠军冻结与统计时序

图中必须严格使用以下顺序，不得把 calibration 结束点画成 C0 冻结点：

1. `aiq-calibration` 采集并远端验证 `D_CAL_3D`，然后封存和退役。
2. 独立研究机仅生成不可变参数候选、校准结果和代码/配置/Schema/镜像/迁移等预发布素材；此时没有最终 CHAMPION `StrategyPackage`，也没有 C0 冻结点。
3. 创建全新的 `aiq-validation`，对两个独立 database 逐库迁移，建立行情连接、订单簿、归档/监控并完成 Shadow/Testnet 双 lane 预热。过渡数据不属于 calibration、OOS 或正式门禁。
4. 双 lane 健康后，操作员选择留有签名余量的未来精确 UTC `effective_at`。
5. 以该时刻生成最终不可变 C0 CHAMPION `StrategyPackage`，其 OOS 为 `[effective_at, effective_at+87d)`；组装最终 release，并生成短时 `StrategyApproval(action=FREEZE_CHAMPION)`。批准的 `effective_at` 与 OOS 起点必须逐字节相等。
6. 在 `effective_at`，单一数据库事务验证并消费一次性批准，同时追加 `StrategyApproval`、`CHAMPION_FROZEN`、`GATE_TIMER_STARTED`，并把该时刻写成 87 日 OOS 起点；事务提交后才处理门禁新事件。
7. 任一步失败、预热健康变化或错过 `effective_at`：不得产生部分记录；旧 package、release、challenge 和 approval 全部不可用，必须选择新的未来时刻整套重建、重签，禁止修改旧时间戳。
8. 固定 release 的连续 72 小时工程门禁与 87 日 OOS 从同一 `effective_at` 起算；72 小时 Paper/Testnet 只证明工程门禁，不证明盈利。
9. Day 90 完成揭盲、walk-forward、1.5 倍成本和参数 ±20% 稳定性。硬门槛失败时暂停新仓并人工复核。

## 7. 第三页：阶段项目与升级门禁

首台 VPS 的阶段链必须画为：

```text
aiq-testnet
  → seal / down / 受控退役
  → aiq-calibration
  → 远端验证 / 参数候选 / 预发布素材
  → 全新 aiq-validation 迁移和预热
  → effective_at 原子冻结 C0 + 启动 72h 门禁 / 87d OOS
  → 门禁签署 / down / 受控退役
  → 全新 aiq-live
```

- 四个阶段业务 Compose project 互斥，不并行常驻；目标阶段使用全新 network、volume、database、数据库角色、队列、Redis namespace 和事实命名空间。
- `aiq-host-control` 与 `aiq-binance-egress` 是仅有的两个跨阶段持久例外，均不属于任何阶段业务 project。图中必须把四阶段业务的 Reserve 汇聚到 `rate.sock`、请求汇聚到 `gateway.sock`，再由唯一 gateway PermitConsume 并出站；专用 `aiq_host_rate_control` 不是任何阶段业务 database。
- `aiq-testnet`、`aiq-calibration`、`aiq-validation` 和 `aiq-live` 无论使用公开行情、Testnet 还是生产端点，都没有 Binance route；每个新请求遵守 Reserve→gateway→PermitConsume→send，宿主链任一依赖故障时四阶段一致为零新 Binance 出站。
- `aiq-calibration` 固定为生产公开行情只读，无 execution-service、无 Testnet adapter、无任何 Binance key、无 `SignalCandidate`/`RiskDecision`/`OrderIntent`/`OrderEvent`。
- `aiq-validation` 是一个 Compose project 和一组总资源预算，但内部有隔离 Shadow/Testnet 双 lane。Shadow 使用生产公开行情和 Paper；Testnet 使用自身轻量规则/行情/用户流和 `testnet-probe-runner`。两 lane 不能共享 database、角色、队列、订单前缀或幂等键。
- `aiq-live` 使用全新的事实和资源命名空间，仅其 `execution-service` 挂生产 key；以 `RISK_LOCKED` 启动。首次实盘 24 小时 `risk_multiplier=0.10`，只能经本机签名人工切换 `1.00`，两者都显示 `EXPERIMENTAL_LIVE`。
- 初始 90 天通过后，月度白名单候选的自动灰度使用 `0.10 → 0.50 → 1.00`；这不改变冻结硬风险上限。任一候选阶段失败自动恢复上一 champion，保留已有持仓 owner，并禁止该 cycle 再晋升。额度延期发生在候选门禁前，不属于候选失败。

Binance 网络出口只能画成韩国 VPS 上唯一 gateway 直接连接签名 catalog 中的 authority。不得画出替代出口、出口配置入口，或由 Telegram/API/业务容器改变该路径的能力。

实盘后的 C2/C3 候选不得与 `aiq-live` 在 2 vCPU/12 GiB 生产主机并行验证，也不能用普通回测机替代韩国部署证据。升级前必须画出：

- 首选：经批准的韩国同区域、同 OS/架构、通过 cgroup 限制到等效 2 vCPU/12 GiB/约 200 GB 的临时 validation VPS；只持 Testnet key，Shadow 只读生产公开行情，无生产 key、生产 DB、生产卷或反向控制通道；生成绑定 runner、规格、真实 RTT、资源和 release 摘要的签名报告。
- 备选：账户已安全归零、生产项目完全停机、事实保全和恢复/容量演练通过后，在原主机互斥 validation；不得保留 live 并行。
- 两条路径均不可用：发布状态为 `BLOCKED`，旧 champion 继续运行或人工暂停。

临时 validation VPS 是部署前置基础设施，不是 Codex、生产模型或自动故障转移节点。

## 8. 视觉规则

- 横向主流程从左到右；外部系统放边缘，信任域使用有标题的大边框。
- 服务用蓝色，事实/归档用绿色，控制/审批用黄色，研究用紫色，执行密钥/阻断用红色，基础设施用灰色，升级验证用橙色，两个持久宿主 project、rate control、gateway 与 permit 门使用青绿色。
- 主路径用实线箭头；非事实、禁止直连或边界说明使用虚线。
- 红色只用于明确风险和权限边界，不把普通数据流画成告警。
- 每个节点控制在四行以内；复杂条件放注释节点，不缩小到不可读字号。
- SVG 采用通用中文字体栈，不依赖外部字体或图片资源。

## 9. 完成前自检

1. XML 解析成功，三个 diagram 均直接含 `mxGraphModel`。
2. SVG 解析成功，并存在三段清晰分区。
3. 页面名、服务名、四阶段项目名和箭头方向正确。
4. 图中冠军冻结只发生于 validation 预热完成后的 `effective_at` 原子事务。
5. 生产 secret 只与 `execution-service` 相连。
6. Codex/研究机没有指向生产写端点或实时交易链的直连箭头。
7. 临时等效 validation VPS 被标为升级门禁，不是 HA 或自动接管。
8. 未出现真实密钥、账户、地址或可执行连接配置。
9. 图例、README 和静态 SVG 已同步。
10. 三页均表达 `aiq-host-control` 与 `aiq-binance-egress` 两个独立持久 project、业务零 Binance route、Reserve→gateway→PermitConsume→唯一发送和任一依赖故障时零新出站的同一语义。
11. 图中未把 Unix domain socket 与 Binance User Data Stream 混称为同一 UDS；allocator 只分配/消费预算，不转发流量，只有 gateway 转发 Binance 流量。
