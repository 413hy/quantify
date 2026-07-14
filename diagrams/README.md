# 架构图交付物

本目录提供 AI 量化交易系统的可编辑架构图与静态预览。图用于帮助 VPS Codex、开发者、测试者和运维人员快速理解关系，不替代规范文档、契约或人工审批。

可使用用户指定的 [DayuanJiang/next-ai-draw-io](https://github.com/DayuanJiang/next-ai-draw-io) 打开或继续维护 `.drawio` 源文件；该仓库只是制图工具，不进入生产、实时交易或部署运行时。

## 文件

| 文件 | 用途 |
|---|---|
| `AI_QUANT_SYSTEM_ARCHITECTURE.drawio` | 三页、未压缩 `mxGraphModel` XML，可由 diagrams.net/draw.io 或 next-ai-draw-io 继续编辑 |
| `AI_QUANT_SYSTEM_ARCHITECTURE.svg` | 三页内容纵向合并的静态 SVG，适合文档预览和评审 |
| `NEXT_AI_DRAW_IO_PROMPT.md` | 交给 VPS Codex/next-ai-draw-io 的完整修改约束 |
| `README.md` | 编辑、导出、图例、权威边界和校验说明 |

## 权威关系

架构图是以下资料的派生视图：

1. [VPS Codex 执行入口](../VPS_CODEX_START_HERE.md)
2. [系统架构与 ADR](../docs/02_SYSTEM_ARCHITECTURE_AND_ADR.md)
3. [数据与数据库](../docs/05_DATA_AND_DATABASE.md)
4. [Codex 实盘分析与研究流程](../docs/07_CODEX_RESEARCH_WORKFLOW.md)
5. [Docker/VPS 部署](../docs/10_DOCKER_VPS_DEPLOYMENT.md)
6. [安全、运维与灾备](../docs/11_SECURITY_OPERATIONS_AND_DR.md)
7. [维护、升级与回滚](../docs/12_MAINTENANCE_UPGRADE_ROLLBACK.md)
8. [AI 策略编排与自动迭代](../docs/14_AI_STRATEGY_ORCHESTRATION_AND_AUTO_ITERATION.md)

出现差异时，用户冻结决策、当前外部官方事实、`VPS_CODEX_START_HERE.md` 和编号规范文档高于本图。图中的节点、箭头或注释不能授予实盘、密钥、发布或解锁权限。

冠军冻结时序以 [研究流程第 6.3–6.4 节](../docs/07_CODEX_RESEARCH_WORKFLOW.md)为准：三日 calibration 封存后只能产生参数候选和预发布素材；全新 `aiq-validation` 完成逐库迁移与双 lane 预热、选定未来 `effective_at` 后，才生成最终不可变 C0 `StrategyPackage`、release 和短时批准。在该精确时刻以单一事务消费批准，并同时追加 `CHAMPION_FROZEN`、`GATE_TIMER_STARTED` 和 87 日 OOS 起点。任一步失败、健康变化或错过时刻，都必须整套作废并使用新的未来时刻重建、重签。

## draw.io 页面

| 页面 | 内容 |
|---|---|
| `01 生产架构` | 韩国生产 VPS、市场热路径、Codex 30m + 规则 20m 单 authority、独立风险/执行、事实存储、唯一 Binance gateway、通知、归档和监控 |
| `02 数据·研究·发布` | Top 10、L2 归档/PITR、独立研究机、签名官方 Codex catalog、fresh selector→fresh analysis、额度延期/FIFO、字段白名单、离线/Shadow/0.10/0.50 自动晋升与回滚、非白名单人工工程路径 |
| `03 阶段·信任边界` | 四阶段互斥项目、两个常驻宿主 project、初始 C0 时序、人工工程发布与月度白名单自动灰度、网络/密钥边界 |

静态 SVG 将三页按上述顺序纵向组合。它是人工维护的结构一致摘要，不是 draw.io 自动导出的逐像素副本；语义、分区和主要箭头必须与 `.drawio` 保持一致。

## 图例

| 颜色/线型 | 语义 |
|---|---|
| 蓝色 | 实时行情、订单簿、特征和基础服务 |
| 靛蓝/紫色 | PA+OF、风险、验证、研究、策略包与回测 |
| 绿色 | PostgreSQL 事实、归档、备份、校验和数据传输 |
| 黄色/橙色 | 人工审批、控制、阶段门禁和发布证据 |
| 红色 | 交易密钥边界、执行服务、实盘、阻断或最后安全防线 |
| 青绿色 | `aiq-host-control`、`rate-budget-service`、`aiq-binance-egress`、唯一 gateway 和一次性 permit 出站门禁 |
| 灰色 | 监控、网络、非事实缓存和基础设施说明 |
| 虚线 | 非事实、禁止直连、边界或辅助关系 |
| 实线箭头 | 允许且有方向的主数据/控制流 |

## 编辑方法

### diagrams.net / draw.io

1. 用 diagrams.net 或 draw.io Desktop 打开 `AI_QUANT_SYSTEM_ARCHITECTURE.drawio`。
2. 在页面标签中选择目标页，不要把三页合并为一个单页源文件。
3. 修改前完整阅读本 README、`NEXT_AI_DRAW_IO_PROMPT.md` 及上方权威文档。
4. 保留稳定页面名和节点 ID；新增节点使用页前缀 `p1-`、`p2-`、`p3-`。
5. 保存后确认每个 `<diagram>` 直接包含 `<mxGraphModel>`，不得替换为压缩/编码文本。
6. 更新同名 SVG，使三页的节点、方向、信任边界和阶段顺序同步。

### next-ai-draw-io

将 `.drawio` 文件和 [继续修改提示词](NEXT_AI_DRAW_IO_PROMPT.md)一并交给工具。要求工具先复述计划修改的页面和规范依据，再修改源 XML；不得仅生成截图或新建一套不关联的图。

## SVG 导出

当前 SVG 是可独立打开的复合静态图。若使用 draw.io CLI，可先分别导出每页进行视觉复核：

```powershell
drawio --export --format svg --page-index 0 --crop AI_QUANT_SYSTEM_ARCHITECTURE.drawio
drawio --export --format svg --page-index 1 --crop AI_QUANT_SYSTEM_ARCHITECTURE.drawio
drawio --export --format svg --page-index 2 --crop AI_QUANT_SYSTEM_ARCHITECTURE.drawio
```

CLI 对多页文件的输出命名会因版本不同而变化，因此这些命令用于逐页复核；仓库要求的 `AI_QUANT_SYSTEM_ARCHITECTURE.svg` 仍须是三页合并、可读且 XML 合法的静态交付物。不要在导出中嵌入真实主机名、IP、账户标识、密钥或 Bot 凭据。

## 不可变架构事实

- 生产环境是韩国 2 vCPU、12 GiB、约 200 GB NVMe 的单 VPS，不构成 HA。
- 市场数据和执行热路径完全确定性。Codex 每 30 分钟以新 process/thread/workspace 和 ephemeral 语义输出一个 `TradePlan/NO_TRADE`，规则 PA+OF 每 20 分钟热备；每个 epoch 只有一个 authority，Codex 失败立即由规则接管。Top 10 每 15 分钟刷新。
- 只有 `execution-service` 持 Binance API secret；它生成短时、不可变的预签名私有请求。生产 secret 不得进入 gateway、其他容器、研究机或 Codex。
- `aiq-host-control` 是第一个跨阶段持久宿主 project：只含 `rate-budget-service`、专用 PostgreSQL database `aiq_host_rate_control` 和 `/run/ai-quant-rate/rate.sock`，不承载业务事实，也不读取 Binance secret。
- `aiq-binance-egress` 是第二个跨阶段持久宿主 project：只含唯一 `binance-egress-gateway` 和 `/run/ai-quant-egress/gateway.sock`。它不持 API secret、不签名，是宿主上唯一可以解析 Binance 域名、建立 socket/TLS、发送 REST/WS API/market-stream control 并 relay 入站流量的组件。
- 所有业务容器零 Binance 路由。业务先向 allocator 发出 `ReserveRequest`，再把有界请求交给 gateway；gateway 重算实际 host/method/path/params/hashes/operation facts 并自行 `PermitConsume`，只有 `CONSUME_GRANTED` 后才发送一次。业务不能消费 permit 后自行直连。
- allocator、`aiq_host_rate_control`、任一 UDS、gateway、业务事实数据库或必需签名工件任一不可用时，系统 fail-closed 为零新 Binance 出站；不得旁路、复用 permit 或退化为进程内限流。已建立的入站 relay 只能维持到自然断开，禁止新连接、重连、订阅、取消订阅、ping/pong；交易所既有原生保护单不被撤销。
- PostgreSQL/TimescaleDB 是事实源；Redis 只作可丢弃缓存、通知和唤醒。
- 生产 VPS 只运行受限 Codex 分析适配器和签名只读知识卡；无 Binance key、无执行网络、无任意工具。重回测、pgvector 和月度研究只在独立研究机。
- `aiq-testnet → aiq-calibration → aiq-validation → aiq-live` 在首台 VPS 的阶段业务项目互斥，每阶段使用新的网络、卷、database、角色、队列和事实命名空间。跨阶段持久例外只有 `aiq-host-control` 与 `aiq-binance-egress`；前者的专用数据库不是业务事实库复用，后者不持久化交易事实。
- calibration 只有生产公开行情、无任何 Binance key、无执行服务、无订单意图。
- validation 使用单一项目下隔离的 Shadow/Testnet 双 lane；72 小时工程门禁不证明盈利。
- 实盘后 C2/C3 候选默认在经批准的韩国同区域同规格临时 validation VPS 验证；若不存在该主机，只能在账户安全归零、live 完全停机并完成事实保全后在原主机互斥验证。两条路径均不可用时发布阻断。
- VPS 原始 L2 最多保留 72 小时或 80 GB；只有远端逐对象验证成功才可删除。
- 公网入站只有 SSH；FastAPI、监控、PostgreSQL 和 Redis 不对公网发布。
- Telegram/飞书仅接收脱敏通知，无入站命令或交易确认。
- 初始 90 天前月度任务只观察；之后先从当前账户签名官方 Codex catalog 以全新 selector 会话选模，再由另一新会话分析。额度不足每日 03:10 UTC 延期、保持原数据截止点、跨月 FIFO/并发 1，不换弱模型；仅字段白名单可经离线门禁、Shadow、0.10/24h、0.50/24h 自动晋升，候选失败自动回滚。硬风险、执行和基础设施只生成工程提案。
- Binance 网络出口固定为韩国 VPS 上唯一 gateway 直连签名 catalog 中的 authority；图中不得出现可配置替代出口或由 Bot/API 修改出口的路径。

## XML 与敏感信息校验

在 PowerShell 中可执行只读解析检查：

```powershell
[xml]$drawio = Get-Content -Raw -Encoding utf8 .\AI_QUANT_SYSTEM_ARCHITECTURE.drawio
[xml]$svg = Get-Content -Raw -Encoding utf8 .\AI_QUANT_SYSTEM_ARCHITECTURE.svg
@($drawio.mxfile.diagram).Count
@($drawio.mxfile.diagram | Where-Object { $_.mxGraphModel -ne $null }).Count
```

两个计数都应为 `3`。发布前还必须确认 XML/SVG 可解析、静态图可读、相对链接有效、未出现未完成占位符，且敏感信息扫描为零。
