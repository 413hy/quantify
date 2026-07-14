# 环境变量与秘密注入清单

## 1. 原则

环境变量只承载非敏感运行参数和“秘密文件的路径”。真实秘密不得直接作为环境变量值，不得进入 `.env`、Compose、镜像层、命令行、日志、事故截图或 Codex 上下文。

[`.env.example`](.env.example) 是键名契约，不是可直接上线的配置。实现必须在启动时检查文件存在、权限、所有者和类型；任何秘密文件权限宽于 `0400`、来自符号链接、位于仓库目录或无法读取时，服务必须失败关闭。

Binance WebSocket 的精确 `base_url`、`stream_role`、流族和轮换参数只能来自已签名且 hash 已绑定 release 的 `system.yaml.market_data.websocket_routes`；禁止再用 `BINANCE_WS_URL` 一类环境变量覆盖。launcher 只声明允许启用哪些角色，并必须证明实际连接 URL 与签名配置逐字一致。未路由的根 `/ws`、`/stream` 即使某些 public 流仍可能返回数据，也一律视为配置错误。

环境—端点 allowlist 逐字固定：Shadow/Paper/Production 使用 `https://fapi.binance.com`、`wss://fstream.binance.com/{public,market,private}` 与默认关闭的 `wss://ws-fapi.binance.com/ws-fapi/v1`；Testnet 使用 `https://demo-fapi.binance.com`、`wss://fstream.binancefuture.com/{public,market,private}` 与默认关闭的 `wss://testnet.binancefuture.com/ws-fapi/v1`。WS API 是独立可选连接，不得与 market streams 混用；redirect 改 host 或生产/Testnet 混用均拒绝。

## 2. 非敏感变量

| 变量 | 消费者 | 允许值/约束 | 缺失行为 |
|---|---|---|---|
| `APP_ENV` | 全部服务 | `shadow`、`paper`、`testnet`、`production` | 拒绝启动 |
| `APP_CONFIG_FILE` | 全部服务 | 绝对路径，须通过 `system.schema.json` | 拒绝启动 |
| `RISK_CONFIG_FILE` | 交易引擎、执行、控制 | 绝对路径，须通过 `risk.schema.json` 和 Decimal 上限语义校验 | `RISK_LOCKED` |
| `UNIVERSE_CONFIG_FILE` | 行情、交易引擎 | 绝对路径，须通过 `universe.schema.json` | 禁止开新仓 |
| `PRICE_ACTION_CONFIG_FILE` | 交易引擎、回放器 | 绝对路径，须通过 `price-action.schema.json`；hash 必须匹配策略包和外部门禁证据 | `RISK_LOCKED` |
| `STRATEGY_ORCHESTRATION_CONFIG_FILE` | decision-scheduler、codex-orchestrator、规则引擎 | 绝对路径，须通过 `strategy-orchestration.schema.json`，只读且摘要绑定 release | 禁用 AI authority，规则引擎接管；交易环境告警 |
| `AUTO_ITERATION_CONFIG_FILE` | 独立月度研究与自动发布控制器 | 绝对路径，须通过 `auto-iteration.schema.json`；生产 VPS 只读发布阶段字段 | 月度 cycle 失败，不影响现行 champion |
| `CODEX_AUTH_MODE` | codex-orchestrator、独立月度 runner | `CHATGPT_ACCOUNT` 或 `OPENAI_API_KEY`；必须使用 Codex 官方支持的认证方式，release 固定；不得由模型修改 | AI 不启动；实时规则接管/月度延期或事故 |
| `CODEX_LIVE_RUN_ROOT` | codex-orchestrator | 固定独立绝对路径，如 `/var/lib/ai-quant/codex-runs/live`；每周期创建全新空白子目录并清理 | AI 不启动，规则接管 |
| `CODEX_RESEARCH_RUN_ROOT` | 独立月度 runner | 研究机绝对路径，如 `/var/lib/ai-quant/codex-runs/research`；selector/analysis 每 attempt 使用不同新目录 | 月度 cycle fail-closed |
| `CODEX_MODEL_CATALOG_DIR` | 独立月度 runner | 研究机只读/追加证据目录；只保存规范化签名 catalog，不保存 auth 或 transcript | 不启动 selector |
| `CODEX_MONTHLY_LEDGER_DIR` | 独立月度 runner | 研究机追加式 cycle/attempt/FIFO 账本目录；并发锁和数据截止点以事实库为权威 | 不启动月度任务 |
| `RATE_BUDGET_CONFIG_FILE` | `rate-budget-service`、所有 Binance REST/WS API 客户端 | 绝对路径，须通过 `rate-budget.schema.json`；hash 必须匹配 release；不得由环境变量覆盖其限额语义 | 阻断全部新的 Binance egress；交易环境进入 `RISK_LOCKED` |
| `NETWORK_EGRESS_POLICY_FILE` | 所有出站服务与主机验证器 | 绝对路径，须通过 `network-egress.schema.json`；只读签名 policy | 拒绝未列出站；交易环境停止新仓 |
| `ENDPOINT_COST_CATALOG_FILE` | `aiq-host-control` | 绝对路径，须通过 closed `binance-endpoint-cost-catalog.schema.json`，JCS hash、签名与有效期匹配 release/rate config；示例基线不可直接上线 | 零 Binance egress |
| `HOST_RATE_CONTROL_POSTGRES_DB/USER/PASSWORD_FILE/DATA` | `aiq-host-control` | database 固定 `aiq_host_rate_control`；password 仅 file reference；独立卷/WAL，不得复用业务 PostgreSQL | allocator 不启动、零 Binance egress |
| `HOST_RATE_CONTROL_SOCKET` | allocator 与获准 caller | 固定 `/run/ai-quant-rate/rate.sock`；通过 UID/GID、`SO_PEERCRED` 与 ACL 鉴权 | 零 Binance egress |
| `HOST_RATE_MIGRATION_HEAD/HOST_RATE_STARTUP_EVIDENCE_DIR` | 宿主启动/恢复工具 | 签名迁移 head；root/受控服务可写证据目录 | 启动门禁失败 |
| `LOG_LEVEL` | 全部服务 | 生产仅 `INFO`、`WARNING`、`ERROR` | 使用 `INFO` |
| `TZ` | 全部服务 | 只能是 `UTC` | 拒绝启动 |
| `POSTGRES_HOST/PORT/DB/USER` | 需访问事实库的服务 | 内部网络 DNS、合法端口、最小权限用户 | 拒绝启动或禁止开新仓 |
| `REDIS_HOST/PORT` | 使用非事实缓存的服务 | 内部网络 DNS、合法端口 | 降级；不得影响事实恢复 |
| `ARCHIVE_SSH_HOST/PORT/USER` | 归档服务 | 固定接收端；host key 必须预置 | 停止删除并告警 |
| `ARCHIVE_AGE_RECIPIENT_SHA256` | 归档服务 | `ARCHIVE_AGE_RECIPIENT_FILE` 规范化单行内容的小写 SHA-256 | 停止上传与删除 |
| `ARCHIVE_RECEIPT_KEY_ID` | 归档服务 | 受控接收端回执签名 key ID | 远端对象不得进入 `REMOTE_VERIFIED` |
| `HEARTBEAT_INSTANCE_ID` | `monitoring` | 不含账户信息的稳定实例 ID，1–64 字符 | 禁止实盘解锁 |
| `HEARTBEAT_KEY_ID` | `monitoring` | 接收端预置的 Ed25519 公钥 ID | 禁止实盘解锁 |
| `HEARTBEAT_INTERVAL_SECONDS` | `monitoring` | 固定 `30` | 禁止实盘解锁 |
| `HEARTBEAT_MAX_AGE_SECONDS` | 外部接收端/测试器 | 固定 `120`；更严值需版本化 | 过期包拒绝 |

## 3. dual-validation launcher 契约

[`.env.example`](.env.example) 只描述单 lane 运行时。`dual-validation` 使用一个仅供 Compose 变量插值的 `<VALIDATION_ENV_PATH>`；它不是应用配置，也不得整份作为 `env_file` 注入任一容器。实施仓库必须提供受版本控制的脱敏模板，运行文件仅保存以下键的非敏感值或 secret 文件路径：

| 键组 | 强制语义 |
|---|---|
| `VALIDATION_PROJECT_NAME` | 固定 `aiq-validation`；与独立预检项目 `aiq-testnet` 不同 |
| `SHADOW_APP_CONFIG_FILE/RISK_CONFIG_FILE/UNIVERSE_CONFIG_FILE/PRICE_ACTION_CONFIG_FILE` + `SHADOW_RATE_BUDGET_CONFIG_FILE` | 前四份为 Shadow lane；rate path 必须解析为 canonical host-control 同一只读文件，hash 等于 startup evidence |
| `TESTNET_APP_CONFIG_FILE/RISK_CONFIG_FILE/UNIVERSE_CONFIG_FILE/PRICE_ACTION_CONFIG_FILE` + `TESTNET_RATE_BUDGET_CONFIG_FILE` | 前四份为 Testnet lane；PA hash 与 Shadow 相同；rate path/hash 必须与 Shadow 和 host-control 逐字一致，只按 endpoint authority 分账 |
| `SHADOW_POSTGRES_DB/USER/PASSWORD_FILE` | 独立 database、最小权限角色和口令文件路径；不得指向预检 `aiq-testnet` 的卷 |
| `TESTNET_POSTGRES_DB/USER/PASSWORD_FILE` | 另一独立 database/角色；禁止跨 database `JOIN` 或共用交易事实表 |
| `HOST_RATE_CONTROL_SOCKET` / `HOST_RATE_STARTUP_EVIDENCE_FILE` | 指向固定 host-level 权威 `aiq_host_rate_control` 的 UDS 与只读启动证据；业务 launcher 不获得 host-control 数据库凭据，project 切换不得重建宿主权威 |
| `SHADOW_QUEUE_ROOT/ORDER_PREFIX/REDIS_ACL_USER/REDIS_KEY_PREFIX` | Shadow 独立耐久队列与命名空间；订单前缀固定 `s` |
| `TESTNET_QUEUE_ROOT/ORDER_PREFIX/REDIS_ACL_USER/REDIS_KEY_PREFIX` | Testnet 独立耐久队列与命名空间；订单前缀固定 `t` |
| `SHADOW_MARKET_SOURCE` | 固定 `BINANCE_PRODUCTION_PUBLIC`；运行 Top 10、PA/OF 与 Paper 账本 |
| `TESTNET_MARKET_SOURCE` | 固定 `BINANCE_TESTNET`；只用 Testnet 自身 book/mark price/`exchangeInfo` 构造协议探针 |
| `SHADOW_WS_ROUTING_CONFIG_SOURCE/ALLOWED_ROLES/PRIVATE_ENABLED` | 固定 `SIGNED_SYSTEM_YAML`、`PUBLIC_HIGH_FREQUENCY,MARKET_REGULAR`、`false`；不能启用 `/private` |
| `TESTNET_WS_ROUTING_CONFIG_SOURCE/ALLOWED_ROLES/PRIVATE_ENABLED` | 固定 `SIGNED_SYSTEM_YAML`、三角色、`true`；Testnet host 的 `/public`、`/market`、`/private` 精确 URL仍从 Testnet `system.yaml` 读取 |
| `TESTNET_PROTOCOL_PROBE_PLAN_FILE` | 预注册、有摘要的协议测试计划绝对路径；不得用生产价格下 Testnet 订单 |
| `TESTNET_BINANCE_API_KEY_FILE/API_SECRET_FILE` | 只向 `testnet-probe-runner` 挂载；`execution-service`、Shadow 和共享组件不可见 |

launcher 必须拒绝：出现 `production` 或生产 secret 路径、两 lane 的 database/角色/队列/前缀任一相同、任一配置环境不匹配、Testnet 配置指向生产公开行情、任一连接使用未路由 `/ws|/stream`，或流族与 `/public|/market|/private` 角色不匹配。三路分别维护健康、重连和 24 小时前重叠轮换；一个路由健康不得掩盖另一路失效。共享的只能是 Compose 项目内的 PostgreSQL/Redis **进程**、监控、控制服务和归档进程，以及独立 host-control 项目的限频权威；后者不得包含或暴露任何交易事实、行情序列、规则快照、策略或账户明文。

`system.yaml.market_data.universe_l1_collection` 还必须是唯一的 Universe 采集权威：全 UM `depth20@500ms`、`!bookTicker`、`kline_1m`，以及最多 H=40 的 diff-depth + REST limit1000 deep fallback。H=40、896/900、双侧 ±10 bps、每 shard 250 和 Universe 60% 子上限均禁止由 launcher/env 覆盖；normal、中优先级、保护/撤单、紧急请求的累计 70%/80%/90%/100% ceiling 与绝对 endpoint cost 只来自 hash 绑定的 `RATE_BUDGET_CONFIG_FILE`/官方 catalog。要求全 eligible exact depth 时必须使用独立扩容 collector；不能添加环境变量绕过 H=40 或将未覆盖深度补零。

### 3.1 calibration launcher 契约

[`calibration.env.example`](calibration.env.example) 只供 `aiq-calibration` Compose 插值。它固定使用已有 `APP_ENV=shadow` 和 canonical RuntimeState `SHADOW`，另以 `CALIBRATION_PROJECT_PURPOSE=CALIBRATION_3D` 区分用途，不能新增 `calibration` Environment。必须满足：

- project 名逐字为 `aiq-calibration`，市场源逐字为 `BINANCE_PRODUCTION_PUBLIC`；
- WebSocket 路由只从签名 system 配置读取，仅允许 `/public` 的 depth/bookTicker 与 `/market` 的 aggTrade/markPrice/kline；`/private` 必须关闭，未路由 `/ws|/stream` 必须拒绝；
- dataset ID、plan、`CALIBRATION_DATA_QUALITY_PROFILE_FILE` 和 `CALIBRATION_PRICE_ACTION_CONFIG_FILE` 在 `T0` 前预登记；PA 文件必须通过 closed Schema，其 config/schema hash 与 OF 搜索计划相同且已签名，配置、collector release/image/schema 与规则哈希全部固定；
- `CALIBRATION_RATE_BUDGET_CONFIG_FILE` 必须通过 closed Schema、hash 纳入 release/profile，并指向同一 host-level allocator socket；calibration 只能消费生产公开 endpoint authority 的预算，项目切换不得清空 429/418 或窗口计数；
- 所有业务 launcher 只获得 `HOST_RATE_CONTROL_SOCKET` 与只读 `HOST_RATE_STARTUP_EVIDENCE_FILE`，不得获得 `HOST_RATE_CONTROL_POSTGRES_*`；唯一宿主 rate config 只能在独立 host-control 升级流程变更。
- execution-service 关闭、`OrderIntent` emission 禁用、控制面只读；Compose 和所有容器均不得出现任何 Binance API key/secret 路径；
- 使用全新的 database、卷、队列、Redis ACL/key prefix，与 `aiq-testnet`、`aiq-validation`、`aiq-live` 均不共享事实；
- egress policy 以 `environment=shadow, project_purpose=CALIBRATION_3D` 验证，只允许生产公开行情、归档和必要监控通道。

启动时执行 `quantctl config validate-calibration --launcher "$CALIBRATION_ENV_PATH" --require-purpose CALIBRATION_3D --deny-all-binance-secrets --deny-execution --deny-order-intents --deny-shared-facts`；任一条件失败则拒绝启动。

## 4. 受控与秘密文件路径

| 变量 | 文件内容 | 唯一允许消费者 | 权限边界 |
|---|---|---|---|
| `POSTGRES_PASSWORD_FILE` | PostgreSQL 应用口令 | 对应数据库客户端 | 每服务使用独立最小权限角色 |
| `REDIS_PASSWORD_FILE` | Redis 认证口令 | 需要 Redis 的服务 | Redis 不得成为事实源 |
| `BINANCE_API_KEY_FILE` | 生产交易 API Key | `execution-service` | 仅 production profile；静态 IP 白名单、无提现权限 |
| `BINANCE_API_SECRET_FILE` | 生产交易签名 Secret | `execution-service` | 仅 production profile；不得挂载到引擎、Testnet、控制、通知、研究服务 |
| `TESTNET_BINANCE_API_KEY_FILE` | Testnet API Key | `testnet-probe-runner` | 仅 testnet/dual-validation profile；不得进入 `execution-service` |
| `TESTNET_BINANCE_API_SECRET_FILE` | Testnet 签名 Secret | `testnet-probe-runner` | 与生产 secret 使用不同目录、所有者、生命周期和撤销证据 |
| `TELEGRAM_BOT_TOKEN_FILE` | Bot Token | `control-service` | 不能获得交易 API Key |
| `TELEGRAM_NOTIFICATION_CHAT_IDS_FILE` | 允许接收通知的 `chat_id`，每行一个 | `control-service` | 只用于出站目标；不得启用入站 update 处理 |
| `OPENAI_API_KEY_FILE` | OpenAI API 凭据（仅 `CODEX_AUTH_MODE=OPENAI_API_KEY`） | 对应 Codex runner | 可选认证方式，不是使用 Codex 的唯一方式；独立于 Binance/通知凭据，只能访问固定 OpenAI authority |
| `CODEX_AUTH_STATE_DIR` | Codex 官方账户认证状态（仅 `CODEX_AUTH_MODE=CHATGPT_ACCOUNT`） | 对应 Codex runner | 敏感目录，独立于每次临时 workspace；不得进入 prompt、日志、归档、镜像或跨主机复制；权限和官方登录流程须在部署时验证 |
| `FEISHU_WEBHOOK_SECRET_FILE` | 飞书通知签名 Secret | `control-service` | 飞书仅通知，不接收控制命令 |
| `FEISHU_WEBHOOK_URL_FILE` | 飞书敏感 webhook URL | `control-service` | 只读挂载；不得写入环境值、日志或文档 |
| `HEARTBEAT_RECEIVER_URL_FILE` | 外部心跳接收 URL | `monitoring` | 仅主动出站推送；URL 不写日志 |
| `HEARTBEAT_AUTH_TOKEN_FILE` | 心跳接收端认证 token | `monitoring` | 与交易凭据分离；可独立轮换 |
| `HEARTBEAT_SIGNING_KEY_FILE` | 心跳 Ed25519 签名私钥 | `monitoring` | 仅签监控 payload；接收端持公钥并防重放 |
| `ARCHIVE_AGE_RECIPIENT_FILE` | 回测机的 age v1/X25519 公共 recipient | `archive-service` | 内容为单个 `age1...`；VPS 不得存在解密私钥 |
| `ARCHIVE_RECEIPT_VERIFY_KEY_FILE` | 远端解密校验回执的 Ed25519 公钥 | `archive-service` | 公钥可读但必须只读、指纹固定；签名私钥仅在接收端 |
| `ARCHIVE_SSH_PRIVATE_KEY_FILE` | 只写归档账户私钥 | `archive-service` | 接收端账号不得获得 VPS shell/生产数据库权限 |
| `ARCHIVE_SSH_KNOWN_HOSTS_FILE` | 固定接收端 host key | `archive-service` | 禁止首次连接自动信任 |

## 5. 容器访问矩阵

| 服务 | Binance Key | 数据库口令 | Telegram/飞书 | 归档/心跳/OpenAI 凭据 |
|---|---:|---:|---:|---:|
| realtime-engine | 否 | 是 | 否 | 否 |
| execution-service | 是，仅 production | 是，仅生产交易账本角色 | 否 | 否 |
| testnet-probe-runner | 是，仅 Testnet | 是，仅 Testnet 探针账本角色 | 否 | 否 |
| control-service | 否 | 是，仅 control 角色 | 是 | 否 |
| codex-orchestrator | 否；无 Binance 路由 | 否；只经受限只读工具取得内容寻址上下文 | 否 | 否；按 `CODEX_AUTH_MODE` 只持一种官方认证；每次 workspace 不含认证状态 |
| persistence-worker | 否 | 是，仅写入角色 | 否 | 否 |
| archive-service | 否 | 是，仅清单角色 | 否 | SFTP 私钥、age recipient、回执验签公钥 |
| monitoring | 否 | 否或只读监控角色 | 否 | 心跳 URL/token/签名私钥 |
| 研究机 Codex/回测 | 否 | 否；只读同步副本另行授权 | 否 | 可持 age 解密私钥及一种独立官方 Codex 认证；selector/analysis workspace 不挂认证状态内容，不得回传 VPS |

Compose 中必须以服务为单位显式挂载，禁止把整个 secrets 目录挂给所有服务。容器内进程使用非 root UID，宿主机秘密目录不得进入备份日志或归档数据集。

## 6. 轮换与失效

1. 创建新凭据，保持旧凭据暂时有效。
2. 在 Testnet 或只读探针验证新凭据和静态 IP。
3. 暂停新仓，确认普通挂单和保护单状态。
4. 以原子方式替换 secret 文件挂载并滚动重启唯一消费者。
5. 完成交易所对账后再撤销旧凭据。
6. 保存轮换审批 ID、旧/新凭据指纹、UTC 时间和验证报告；不得保存秘密明文。

凭据疑似泄露时按 P0 事故处理：立即暂停新仓、保持或验证交易所原生保护、撤销凭据、对账并依据 [安全与灾备](../docs/11_SECURITY_OPERATIONS_AND_DR.md) 执行。密钥轮换不得由 Telegram 发起。

## 7. 启动验证

实现仓库必须提供等价的只读验证命令：

```bash
quantctl config validate --system "$APP_CONFIG_FILE" --risk "$RISK_CONFIG_FILE" --universe "$UNIVERSE_CONFIG_FILE" --price-action "$PRICE_ACTION_CONFIG_FILE"
quantctl strategy orchestration-verify --config "$STRATEGY_ORCHESTRATION_CONFIG_FILE" --require-single-authority --deny-binance-secret --deny-arbitrary-tools --telegram-outbound-only
quantctl iteration policy-verify --config "$AUTO_ITERATION_CONFIG_FILE" --require-observe-only-before-90d --require-automatic-rollback
quantctl secrets inspect-permissions --paths-from-env --redact
quantctl access-matrix verify --compose deploy/compose.yaml
quantctl network egress-verify --policy "$NETWORK_EGRESS_POLICY_FILE" --environment "$APP_ENV" --phase runtime --app-and-host --deny-unlisted
quantctl market-data routing-verify --system "$APP_CONFIG_FILE" --require-routes public,market,private --deny-unrouted --require-independent-health --require-overlap-before-seconds 86400
quantctl binance endpoint-profile-verify --system "$APP_CONFIG_FILE" --exact-hosts --deny-cross-environment --deny-cross-host-redirects --ws-api-independent
quantctl binance startup-probe --system "$APP_CONFIG_FILE" --require time,exchangeInfo,user-data-stream,algo-order --redact-listen-key
quantctl account mode-verify --expect-dual-side-position false --shared-scope um-cm --expect-margin-type CROSSED --symbols top10,rank11-15,managed --read-only --retain-evidence-hash
quantctl universe collector-verify --system "$APP_CONFIG_FILE" --require-st 1 --require-current-um-whitelist --deep-cap 40 --required-slots 896 --deny-24h-ticker-delta --deny-zero-or-extrapolated-depth --capacity-plus-percent 20
quantctl heartbeat contract-verify --outbound-only --algorithm Ed25519 --interval-seconds 30 --max-age-seconds 120 --missing-intervals 3
quantctl archive crypto-verify --format age-v1 --recipient-file "$ARCHIVE_AGE_RECIPIENT_FILE" --require-x25519 --verify-key "$ARCHIVE_RECEIPT_VERIFY_KEY_FILE"
```

dual-validation 还必须执行等价的 `quantctl config validate-dual --launcher "$VALIDATION_ENV_PATH" --deny-production-secrets --deny-shared-facts --verify-signed-websocket-routing --deny-unrouted-websocket`，并输出两 lane 配置、database、角色、队列、前缀、行情源以及三路 URL/role 配置的**脱敏指纹**，不显示 listenKey 或 secret 文件内容。

验证输出只能显示变量名、文件指纹、所有者和权限，绝不回显内容。生产配置与 Schema、签名或环境不一致时只能以 `RISK_LOCKED` 启动。
