# 06 重启与交易所对账手册

## 目的

在计划重启、进程崩溃、主机重启或恢复后，避免重复下单并把 PostgreSQL 事实投影与 Binance 订单、成交、持仓、全仓账户和原生保护重新收敛。所有重启默认进入 `RISK_LOCKED`。

说明：文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约；在对账与幂等语义实现前，不能用重发订单作为恢复办法。

## 前置条件

- 能访问当前/上一 release manifest、数据库和审计；若数据库损坏，转 [08 数据恢复](08_DATA_RECOVERY.md)。
- 记录最后已持久化事件、普通订单 `clientOrderId/orderId`、条件保护 `clientAlgoId/algoId/actualOrderId`、用户流序列/更新时间和当前策略版本。
- 计划重启前先暂停新仓并确认原生保护；计划外重启则把交易所事实视为最终外部事实源。
- 不得通过重发“最后一条意图”恢复；未知下单状态必须先查询。
- 任何重启均先完整执行 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)。必须先恢复专用 `aiq_host_rate_control`、fencing/counter/429/418 和 UDS，再允许本手册的 `exchangeInfo`、listenKey、查询或对账请求。

## 计划重启

```bash
quantctl pause-new-entries --environment production --reason "planned restart" --idempotency-key "<COMMAND_ID>"
quantctl cancel-pending --environment production --exclude-protective --dry-run --output "<PREVIEW_FILE>"
quantctl reconcile --environment production --full --read-only --fail-on-difference
quantctl protection verify --environment production --all-positions
quantctl persistence drain --timeout 60 --fail-if-pending
quantctl state snapshot --environment production --output "<PRE_RESTART_SNAPSHOT>"
```

只有对账零差异、所有持仓保护健康后才进行容器重启：

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export RUNTIME_ENV_PATH="<RUNTIME_ENV_PATH>"
cd "$PROJECT_DIR"
DC=(docker compose -p aiq-live -f deploy/compose.yaml --env-file "$RUNTIME_ENV_PATH")
"${DC[@]}" config --quiet
"${DC[@]}" restart "<SERVICE_NAME>"
"${DC[@]}" ps
curl --fail --silent --show-error http://127.0.0.1:8080/health/live
```

同一恢复会话后续 Compose `exec/ps/restart/up/down` 必须复用 `DC`。禁止校验一个 env 后用省略 `--env-file` 的命令重启另一套运行时。

不得同时无序重启 PostgreSQL、执行服务和实时引擎。数据库先恢复读写，执行服务先恢复用户流/查询能力，实时引擎最后完成订单簿重建和预热。

## 计划外重启/启动序列

1. 系统启动即写入 `RISK_LOCKED`，保持 Binance egress 闸门关闭，不消费可能产生新仓的遗留队列。
2. 离线验证 release/config/Schema/catalog 摘要、签名和有效期；恢复业务数据库只为本地校验，不发送探针。
3. 完整执行 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)：先恢复独立 `aiq_host_rate_control`、未决 permit/allocation、fencing、429/418，再启动 allocator/UDS。其 startup evidence 未通过前连 `/time`、`exchangeInfo` 都不得发送。
4. 经一次性 permit 验证时钟 ≤50 ms、刷新当前 `exchangeInfo`，再为 replacement 创建全新 listenKey，连接 `/private` 并确认 `ORDER_TRADE_UPDATE/ACCOUNT_UPDATE/ALGO_UPDATE` 水位；随后从 REST 拉取 ordinary open orders、open Algo orders、近期成交、收入、持仓、余额和账户模式。Algo 历史不假设存在未签名清单以外的“全量历史”端点：只从追加式本地账本、`ALGO_UPDATE` 和未闭合 `algoId/clientAlgoId` 集合逐 ID 查询 `GET /fapi/v1/algoOrder`，以 `(algoId, actualOrderId, updateTime)` 去重；任一已知 ID 在签名查询时间窗内无法闭合即保持 `RISK_LOCKED`。
5. STANDARD 用确定性 `clientOrderId/orderId` 匹配本地意图；ALGO 用 `clientAlgoId/algoId` 匹配 parent，并逐一保留 `TRIGGERING/TRIGGERED/FINISHED`。`TRIGGERED` 缺 `actualOrderId` 或 `FINISHED` 尚无 child 终态时立即查询 Algo/ordinary order；只有实际 ordinary 订单/成交事件才能裁决 `FILLED/CANCELED`。交易所存在而本地缺失的事实只追加恢复事件；本地存在而交易所未知的意图标记待调查，禁止重发或造 ID。
6. 检查每个持仓的原生 Algo 保护、触发状态和覆盖数量；缺失时停止全部新仓，并按 [05](05_PAUSE_CANCEL_FLATTEN.md)保护或退出。
7. 重建订单簿，验证 `U/u/pu` 连续并预热；在此之前无信号可执行。
8. 若 monitoring 进程重启，生成新的随机 `boot_id`，`sequence` 从 1 开始；接收端必须先验签再接受新 boot，并拒绝随后出现的旧 boot 或旧 sequence。计划外网络重试不得复用 sequence。
9. 生成差异报告，差异归零后保持 `RISK_LOCKED` 等待人工复核。

安全命令示例：

```bash
quantctl risk lock --environment production --reason "startup reconciliation"
quantctl release verify --manifest "<SIGNED_RELEASE_MANIFEST>"
quantctl database verify --read-write --migration-head "<EXPECTED_ALEMBIC_HEAD>"
quantctl host-rate require-ready --evidence "<HOST_RATE_STARTUP_EVIDENCE>" --max-age-seconds 300
quantctl clock verify --max-offset-ms 50 --require-one-shot-permit
quantctl exchange refresh-rules --source exchangeInfo --environment production
quantctl account verify-mode --environment production --dual-side-position false --require-um-cm-shared-setting-evidence
quantctl account verify-margin --environment production --symbols active,candidates,managed --expected CROSSED --per-symbol --read-only
quantctl user-stream replace --environment production --new-listen-key --route private --events 'ORDER_TRADE_UPDATE/ACCOUNT_UPDATE/ALGO_UPDATE' --require-overlap-watermark
quantctl reconcile --environment production --full \
  --include-standard-orders --include-open-algo-orders --include-algo-history \
  --resolve-actual-orders --include-user-events ORDER_TRADE_UPDATE,ALGO_UPDATE \
  --recover-missing-events --no-order-retry --output "<RECON_REPORT>"
quantctl protection verify --environment production --all-positions
quantctl orderbook rebuild --universe active,candidates,managed --require-warmup
quantctl heartbeat verify --outbound-only --algorithm Ed25519 --interval-seconds 30 --missing-intervals 3 --max-age-seconds 120 --require-new-boot-after-restart --reject-old-boot
quantctl reconcile --environment production --full --fail-on-difference
```

`--recover-missing-events` 只追加交易所已证实的事实，不伪造成交或覆盖旧事件。

## 人工恢复新仓

只有差异为 0、保护健康、订单簿预热、数据库可写、资源/时间正常且无开放 P0/P1 时，生成短时效挑战并在本机签名：

```bash
export RISK_UNLOCK_PRESTATE="$(quantctl state hash --environment production --require-risk-locked --raw)"
export RISK_UNLOCK_EFFECTIVE_AT="$(quantctl time future --lead-seconds 300 --format rfc3339 --raw)"
export RISK_UNLOCK_EXPIRES_AT="$(quantctl time add --at "$RISK_UNLOCK_EFFECTIVE_AT" --seconds 120 --format rfc3339 --raw)"
quantctl risk unlock-challenge --operator-action RISK_UNLOCK --reason "post-restart reconciled" \
  --report "<RECON_REPORT>" --precondition-state-hash "$RISK_UNLOCK_PRESTATE" \
  --effective-at "$RISK_UNLOCK_EFFECTIVE_AT" --expires-at "$RISK_UNLOCK_EXPIRES_AT" \
  --bind-current-runtime --output "<CHALLENGE_FILE>"
quantctl approval sign --schema contracts/operator-approval.schema.json --expected-action RISK_UNLOCK \
  --challenge "<CHALLENGE_FILE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<APPROVAL_FILE>"
quantctl time await --at "$RISK_UNLOCK_EFFECTIVE_AT" --not-after "$RISK_UNLOCK_EXPIRES_AT" --fail-if-late
quantctl risk unlock --challenge "<CHALLENGE_FILE>" --approval "<APPROVAL_FILE>" \
  --effective-at "$RISK_UNLOCK_EFFECTIVE_AT" --precondition-state-hash "$RISK_UNLOCK_PRESTATE" \
  --consume-once --atomic --fail-if-state-changed
quantctl status --environment production
```

Telegram 无解锁权限。

## 验收

- 重启期间没有新仓或重复订单；遗留意图没有被盲重发。
- REST、`/private` 用户流和 PostgreSQL 的普通订单、Algo parent（含 TRIGGERING/TRIGGERED/FINISHED）、触发后 actual order、成交、持仓与账户投影差异为 0。
- 限频权威已恢复同一窗口的单调已用量、UNKNOWN 保留、429/418 阻断状态；重启前后无计数回拨，无 Binance 请求绕过 allocator。
- `dualSidePosition=false`，active/候补/managed 每个 symbol 的 `marginType=CROSSED`，所有持仓有可按 `clientAlgoId/algoId` 查询且状态正确的交易所原生保护。
- Top 10、候补和持仓管理集订单簿已重建并完成预热。
- 心跳恢复使用新的 `boot_id`；连续 3 个 30 秒间隔缺失时外部接收端已告警，旧 boot、重放、过期或伪造包均未刷新 last-seen。
- 解锁绑定对账报告摘要并由本机人工签名；完整事件链可重放。

## 停止与升级条件

订单 `UNKNOWN` 达到 5 秒、保护缺失、账户模式不符、数据库不可写、迁移摘要不符、时钟 >100 ms 或订单簿无法连续重建时不得解锁。业务库不可写时出口网关阻断全部新的 Binance REST、WS API 与 market-stream control 请求；V1 没有本地应急日志或延后回填例外，只依赖已确认的交易所原生保护并升级 P0 官方控制面处置。数据库损坏转 [08](08_DATA_RECOVERY.md)。

## 证据留存

保存重启原因、完整 Compose 上下文指纹、前后快照、容器/主机启动时间、release/配置/迁移摘要、普通/open Algo/all Algo/actual order REST 与 `ORDER_TRADE_UPDATE/ALGO_UPDATE` 原始响应（脱敏）、新旧 listenKey 重叠水位、差异/补录事件、保护检查、订单簿预热、时钟、心跳新旧 `boot_id/sequence` 接收与拒绝证据、人工挑战和签名。记录恢复耗时供 RTO/SLO 复核。
