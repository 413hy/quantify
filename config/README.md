# 配置使用说明

## 1. 文件与优先级

| 文件 | 内容 |
|---|---|
| `system.example.yaml` | 运行、账户模式、服务、存储、市场数据和研究隔离基线 |
| `risk.example.yaml` | 风险硬上限、首日倍率、连亏锁和 Kill Switch |
| `universe.example.yaml` | Top 10 排名、候补、持仓管理集、防抖和回测防幸存者偏差 |
| `price-action.example.yaml` | 首版 PA 的封闭 `UNVALIDATED_ENGINEERING_BASELINE`；仅使用 bar/ATR/ratio/bps，不参与三日 OF 优化 |
| `strategy-orchestration.example.yaml` | Codex 30 分钟主分析、每周期全新 ephemeral 会话、规则 20 分钟后备、单 authority、90 秒超时、60 秒 TTL、工具与通知边界 |
| `auto-iteration.example.yaml` | 每月 1 日 03:10 UTC、两阶段自主选模、额度每日延期/FIFO、初始 90 天 observe-only、字段白名单、统计门槛、灰度与回滚 |
| `rate-budget.example.yaml` | Binance REST/WS API 的 IP/account 共享限额、优先级保留、header 对账和 429/418 持久化规则 |
| `binance-endpoint-cost-catalog.example.json` | endpoint/参数成本、operation class 与首次 `/time`/`exchangeInfo` 保守 bootstrap 快照；文档示例不可直接上线 |
| `host-control.env.example` | 独立常驻 `aiq-host-control` launcher 的非敏感路径、专用数据库、UDS 与证据目录模板；不得挂业务/交易 secret |
| `gateway.env.example` | 独立常驻 `aiq-binance-egress` launcher 的非敏感 UDS、frame 上限、信任根和唯一出站边界；不得挂 API secret 或数据库凭据 |
| `network-egress.example.yaml` | 应用 endpoint 与主机 egress 双层 allowlist、DNS/NTP、运行/维护目的地模板 |
| `*.schema.json` | 对应 YAML 的结构验证规则 |
| `.env.example` | 非敏感变量和 secret file 路径名 |
| `validation.env.example` | 单一 dual-validation Compose launcher 的非敏感 Shadow/Testnet 路径、database、队列与订单前缀模板；实施时复制到受控路径并注入各自 secret file |
| `calibration.env.example` | `aiq-calibration` 三日公开行情采集 launcher；`APP_ENV=shadow`、`RuntimeState=SHADOW`、无执行服务和任何 Binance secret |
| `calibration-data-quality.example.yaml` | 三日窗口“首个连续合格”的封闭机械裁决：时钟、L2 连续性、覆盖、归档、稳定性、资源和失败语义 |
| `environment-variables.md` | 消费者、访问矩阵、轮换和失败行为 |

运行配置的裁决优先级是：签名且未过期的环境专用配置 → 已批准基线 → 内置安全默认。内置默认只能更保守，不能突破风险硬上限或放宽安全门禁。任何未知字段、未知 Schema 主版本、签名不匹配或配置 hash 与审批工件不一致，都必须以 `RISK_LOCKED` 启动。

## 2. 结构与语义验证

JSON Schema 负责结构、固定值和可由单文件表达的十进制上限；实现还必须执行以下跨字段、跨文件语义检查：

- `configured_limits` 每一项大于等于零且不超过对应 `hard_caps`；
- 首 24 小时 `risk_multiplier` 只能是 `0.10`；切换 `1.00` 必须消费本机人工签名工件；
- `strategy-orchestration.yaml` 必须固定实盘 `gpt-5.6 + medium`、`:00/:30` Codex、`:00/:20/:40` 规则、90 秒超时、60 秒计划 TTL、单并发和单 authority；每个 Codex 周期和恢复 dry-run 都使用新 process/thread/workspace、ephemeral、禁止 resume/thread reuse/历史 transcript/memory/reasoning。Codex 不可用时立即规则接管，恢复必须 cooldown 后连续 3 次全新会话 dry-run 成功。
- Codex 认证可使用当前官方支持的 ChatGPT 账户或 OpenAI API 方式，由 `CODEX_AUTH_MODE` 和 release 固定；不是所有 Codex 自动化都必须使用 API key。两种认证不得同时注入，同一 runner 的临时 workspace 不得包含认证状态。
- Codex 工具集合必须逐字等于 Schema 中 7 个只读工具；禁止能力至少包含 Binance secret/网络、execution UDS、数据库写、任意 shell/浏览器/网络、容器控制和 secret 环境读取。任何新增工具都属于非白名单工程变更，不能由月度流程自动应用。
- Telegram/飞书配置必须是 outbound-only，`inbound_handlers_enabled=false`、`trade_confirmation_required=false`；不得出现 webhook、长轮询或命令 handler 配置。
- `auto-iteration.yaml` 固定每月唯一 `YYYY-MM` cycle 和普通漏跑补一次；先用 `codex debug models` 的签名当前账户 catalog 启动全新 selector，再用被选 OpenAI Codex 官方模型启动另一个全新分析会话。selector/分析额度不足进入 `DEFERRED_QUOTA`，每天 `03:10 UTC` 重试、保持原数据截止时间、跨月 FIFO、并发 1，禁止 resume 或静默换弱模型。初始 90 天前只能 `OBSERVE_ONLY`。通过后候选只可修改白名单字段，必须依次通过离线门槛、72h Shadow、`0.10/24h`、`0.50/24h`，候选失败自动回滚且同一 cycle 不再晋升。
- 五项标的池权重用 Decimal 求和必须严格等于 `1.00`；
- `price-action.example.yaml` 必须通过 `price-action.schema.json`，并额外验证 compression short < long、pullback min < max、range efficiency ceiling 小于 trend floor、最大 lookback 已完整预热；缺键、额外键或实现内补默认值均阻断启动。
- `active_size=10`、`standby_size=5`、`refresh_seconds=900`；
- 首版资格门槛必须从签名 `universe.yaml` 读取，禁止硬编码：15 分钟成交名义额至少 `1,000,000.00 USDT`、15 分钟 median spread 不高于 `10.00 bps`、mid ±`10.00 bps` 内双侧较小深度至少 `50,000.00 USDT`、排名输入完整率至少 `99.50%`。提高成交额/深度/完整率下限或降低点差上限属于收紧；反向变化属于放宽，必须按 C2 生成新配置包、回放/Shadow 证据和人工签名，且不能在当前门禁窗口内原地修改。
- active、standby 和 managed 三集合中，不允许同一标的以不一致角色重复；持仓管理身份优先保留完整流；
- 配置中的路径必须是绝对路径且位于批准目录；不得从网络 URL 加载配置；
- 容器服务可在隔离网络内监听 `0.0.0.0`，但 Docker 宿主机发布地址必须严格为 `127.0.0.1` 或 `::1`；缺少显式 host IP 的端口映射必须被配置检查拒绝；
- `universe.yaml.baseline_origin` 固定为 `UNVALIDATED_ENGINEERING_BASELINE`，只记录不可变来源，不是可晋升的运行状态；不得把它改写成“已验证”或“已签名”。生产加载器必须在 YAML 外部验证：72 小时验证 evidence 已签名，`APPROVE_SHADOW -> APPROVE_TESTNET -> ARM_EXPERIMENTAL_LIVE` 的 `StrategyApproval` 链完整，且 evidence、每个批准工件与待加载文件的 RFC 8785 规范 hash 完全相同。任一条件缺失或 hash 不同均以 `RISK_LOCKED` 启动。
- 72 小时通过后仍须原样、只读挂载同一个 `universe.yaml`；验证结论和批准只追加到外部 evidence/approval 账本，禁止回写 YAML。任何语义字段变更都必须生成新文件与新 hash，并重新完成对应验证和批准链，不能沿用旧证据。
- PA 采用同一不可变资格模型：`baseline_origin=UNVALIDATED_ENGINEERING_BASELINE` 永不回写。账户所有者必须在 calibration `T0` 前签署 `price_action_config_hash=SHA-256(RFC8785 JCS(安全解析 YAML))` 和 `price_action_schema_hash=SHA-256(Schema 精确 UTF-8 文件字节)`；OF 搜索计划、参数候选、StrategyPackage、release 与门禁证据逐层绑定二者。三日窗口只优化 closed OF alpha 参数，PA 变化必须新建数据集计划并重新计时。
- `.env` 的 `APP_ENV` 必须逐字等于 `system.yaml.environment`；system/risk/universe/price-action/rate-budget 五份配置的 `schema_version` major 必须兼容且目标环境一致。
- `system.yaml.market_data.universe_l1_collection` 管物理 route、连接、H=40 资源和 rate-budget 绑定，`universe.yaml.l1_sampler` 管排名窗口与输入语义；加载器必须按 Schema 中的同名/映射字段逐项证明一致，并把两份规范 hash 同时绑定 release。任一 15-bar、1 秒/900 槽、896 槽、depth20@500ms、H=40、deep priority、±10 bps、shard 或预算引用不一致即 `RISK_LOCKED`，不得按任一文件“优先”猜测。
- `APP_CONFIG_FILE`、`RISK_CONFIG_FILE`、`UNIVERSE_CONFIG_FILE`、`PRICE_ACTION_CONFIG_FILE`、`RATE_BUDGET_CONFIG_FILE`、`NETWORK_EGRESS_POLICY_FILE` 的已解析绝对路径必须分别等于实际载入文件和 `system.config_files` 声明；禁止符号链接逃逸批准目录。
- `rate-budget.example.yaml` 必须通过 closed Schema：`aiq-host-control` PostgreSQL 是跨业务 project 的耐久原子权威，Redis 明确不是；REQUEST_WEIGHT 按 endpoint authority + 出站 IP 合并 REST/WS API/UM/CM，订单计数按 endpoint authority + account 合并 UM/CM，environment/project 不是 scope key。class ceilings 固定为 normal 70%、reconciliation/UDS/normal-exit 80%、protection/cancel 90%、emergency 100%，Universe snapshot 另受总限额 60% 子上限；所有窗口按 floor 取整、全有或全无。缺失 header、UNKNOWN 与已授未发送 permit 保守计费；429/418 状态跨重启/阶段保存且 block 覆盖所有 class。首版无预发 emergency lease，allocator/authority/UDS 不可用时阻断全部新 Binance egress request 并依赖既有原生保护。
- endpoint cost catalog 必须通过 closed Schema且逐字 hash 匹配 rate config/release。首次空库只可使用未过期、已签名的保守 bootstrap snapshot 为 `/time` 与 `/fapi/v1/exchangeInfo` 发放一次性 permit；响应后立即用 exchangeInfo/header 单调收敛。catalog 缺失、过期、签名/hash 不符或 endpoint/参数组合未知时零 Binance egress，禁止把成本常量藏在代码中。
- `ENDPOINT_COST_CATALOG_FILE` 的 resolved absolute path 必须逐字等于解析后 `rate-budget.yaml.limit_sources.endpoint_cost_catalog.path`（canonical `/etc/ai-quant/host-control/binance-endpoint-cost-catalog.json`）；拒绝 symlink escape、相对路径和 env 覆盖。
- network egress policy 的所有 `BINANCE_*` destination 必须且只能允许 `binance-egress-gateway`；realtime/execution/Testnet adapter 无 Binance DNS/路由。gateway 只接受 catalog 中精确 authority/host/transport/method/path/parameter 组合，重算 canonical hash，并在 `PermitConsumeDecision=CONSUME_GRANTED` 后发送一次；`execution-service` 独占生产 API secret，`testnet-probe-runner` 独占 Testnet secret，二者只向 gateway 交付短时不可变预签名请求。
- gateway IPC 的外层 JSON/frame 上限固定为 `16,777,216` bytes，base64 解码后的 Binance payload 上限固定为 `12,582,912` bytes；`gateway.env`、`host-control.env` 与 `binance-gateway-ipc.schema.json` 三处必须逐字一致。超限在解码/分配大对象前拒绝并审计，不能截断、分片绕过或调高后热生效。
- 配置签名的 `target_environment` 必须等于 `APP_ENV`；任一环境、路径、版本或 hash 不一致时以 `RISK_LOCKED` 启动。
- 磁盘与资源阈值按 `combine_rule=ANY` 解释：原始区 60/72/80 GB 对应告警/停新/高优先级归档事故，文件系统 85%/90%/95% 对应告警/停新/P0，可用空间低于 30/20/10 GB 对应告警/停新/P0；任一条件达到即执行，多项命中取最严重动作，不等待所有条件同时满足。
- 容量单位必须显式区分：文档中的磁盘 `GB` 采用 SI（`1 GB = 1,000,000,000 bytes`），内存 `GiB` 采用二进制（`1 GiB = 1,073,741,824 bytes`）；Schema 中所有 `*_bytes` 常量按对应单位精确换算。
- `outbound_allowlist_mode=true` 必须加载通过 `network-egress.schema.json` 的签名 policy，并同时验证应用 scheme/host/port 与主机 egress default-deny；运行目的地、维护目的地和唯一消费者不能互相扩权。维护目的地默认关闭，禁止跨 host redirect、永久硬编码业务 IP 或配置第二条 Binance 网络出口。
- `validation.env` 只供 Compose launcher 做变量分发；Shadow 与 Testnet 各自挂载 system/risk/universe/price-action 与绑定的 egress policy，PA 规范 hash 必须相同，各容器只接收所属 lane 的 database/角色/队列/订单前缀。两 lane 的 rate-budget path 必须解析为**同一份**宿主只读文件且 JCS hash 等于 `aiq-host-control` startup evidence；lane 无 host-control DB credential。Testnet key 只能进入 `testnet-probe-runner`，`execution-service`、Shadow lane 和共享组件永远不能看到；两 lane 只共享 UDS/socket 与不含业务事实的 startup evidence。
- `calibration.env`、通用 `.env` 同样只能挂载 canonical host rate config、UDS 与 evidence，不能出现 `HOST_RATE_CONTROL_POSTGRES_*`。rate ceiling/catalog 变更只能走独立 host-control release train，业务 project 切换不得暗改。
- `calibration.env` 固定 `project=aiq-calibration`、`project_purpose=CALIBRATION_3D`、`APP_ENV=shadow`、`RuntimeState=SHADOW` 和生产公开行情；不得新增公共 Environment/RuntimeState 枚举。launcher 必须排除 execution-service、订单意图 sink、所有聊天入站处理和所有 Binance key/secret。
- 校准数据质量 profile 必须在 T0 前通过 `calibration-data-quality.schema.json`、计算规范 hash 并签名预登记；任一 L2 缺口/陈旧决策帧/未消除乱序/重复作用、质量时钟偏移 >50 ms、覆盖或 `REMOTE_VERIFIED` 不足、collector/config/image/schema/profile 变化、资源硬阈值或数据库写失败都使窗口失败。失败窗口保留，使用新 dataset ID 并选择下一个首个合格窗口；禁止事后放宽阈值。
- Shadow 行情源必须是 Binance 生产公开行情，用于 Top 10、PA/OF 和 Paper 账本；Testnet 协议探针必须独立使用 Testnet 的 book/mark price/`exchangeInfo`。不得把生产价格、过滤器或订单参数直接发往 Testnet。
- 归档加密固定为 age v1/X25519；VPS 只持受指纹约束的 recipient。`REMOTE_VERIFIED` 必须同时验证密文 SHA-256、远端解密后明文 SHA-256/Parquet 可读性与接收端 Ed25519 签名回执；缺一项都冻结删除。
- 外部心跳固定每 30 秒由 VPS 主动出站推送 Ed25519 签名包，不开公网健康端口。接收端按 `(instance_id, boot_id, sequence)` 去重并拒绝超过 120 秒的包；连续 3 个间隔（约 90 秒）缺失告警，心跳凭据不具备交易、解锁或配置权限。
- `system.example.yaml` 中重复的 `a`/`b` 形式 64 位指纹和 `replace-with-...` 值只是明显无效的文档占位符。部署预检必须从实际 age recipient、回执 Ed25519 公钥和已批准心跳接收端重算/替换配置并由人工签署；任一占位符仍存在时配置加载器必须拒绝进入任何可交易状态。

`minimum_residence_seconds=3600`、`replacement_score_delta=5.00`、`warmup_seconds=120` 和 `minimum_trade_events=1000` 是首版工程默认，不是从参考策略或历史 Binance 数据证明的 alpha 参数。它们可以在 Shadow 阶段按数据健康和切换稳定性校准，但更改必须版本化并保存前后证据。

## 3. 配置变更等级

| 等级 | 示例 | 生效要求 |
|---|---|---|
| C0 | 纯文字、图或注释，运行摘要不变 | 文档、链接、Schema 与清单复核；不重置 72 小时/OOS |
| C1 | 通知文案、非关键指标，事件选择/时序不变 | 单元/集成、部署 smoke、人工复核；不重置 72 小时/OOS |
| C2 | 依赖、运行时、可观测性或非交易配置，固定回放 decision root 完全一致 | 全套 CI、回放、Testnet、短期 Shadow、exact parity；未完成的 72 小时从零重启，OOS 仅在 parity 可证时继续 |
| C3 | 策略参数、标的筛选、成本/执行模型、风险、订单/事件语义、交易数据选择、凭据或 Kill Switch | 普通路径：新策略包、完整 Testnet/Shadow 72 小时、样本隔离、本机 CLI/人工签名；初始 90 天后月度字段白名单可走自动离线/Shadow/0.10/0.50/回滚子路径 |
| C4 | 已利用安全缺陷、密钥泄露或保护失效的紧急控险 | 先暂停/隔离/撤销，随后按实际影响完成 C3 验证与重置，保留全部补录证据 |

Telegram 和普通 REST 均不得执行 C2/C3/C4 发布或批准。月度自动发布器只能消费通过 Schema 与签名校验的白名单 `AutoIterationReport`，不能模拟人工命令。风险值只能收紧或在不超过冻结硬上限的范围内恢复；任何提高硬上限的请求都属于新需求，必须重新进行需求、安全和统计审查。最终分类与重置以 [VPS Codex 审查与迭代](../docs/13_VPS_CODEX_AUDIT_AND_ITERATION.md) 为唯一矩阵。

## 4. 发布工件

每次配置发布必须保存：原始不可变 YAML、规范 JSON、Schema 版本、SHA-256、生成者、审阅者、UTC 生效窗、目标环境、关联策略包、数据库迁移版本、回滚 hash 和 detached signature。规范化规则固定如下：YAML 以安全模式解析并拒绝重复键、alias 循环、非 JSON 类型和非有限数；Decimal 与时间仍保留字符串；解析结果使用 RFC 8785 JCS、UTF-8、无 BOM 生成签名字节。LF/CRLF、键顺序和 YAML 表面写法因此不影响规范 hash，但任何语义值变化都会改变 hash。

多文件配置包的签名不直接覆盖可变目录：先对每个批准相对路径的原始文件计算 SHA-256，按 UTF-8 路径字节升序生成 `{path,sha256}` JCS manifest，再签 manifest 的 SHA-256、`target_environment`、有效期和 nonce。路径不得为绝对路径、包含 `..`、反斜杠或符号链接逃逸。容器启动时只读取只读挂载，不允许控制服务直接覆写生产文件。

相关流程见 [项目结构与开发流程](../docs/08_PROJECT_STRUCTURE_AND_DEVELOPMENT.md)、[安全与灾备](../docs/11_SECURITY_OPERATIONS_AND_DR.md) 和 [升级回滚](../docs/12_MAINTENANCE_UPGRADE_ROLLBACK.md)。
