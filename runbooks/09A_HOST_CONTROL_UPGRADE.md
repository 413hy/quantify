# 09A host-control 独立升级与兼容回退手册

## 目的与边界

本手册只发布独立 host-control 列车：`rate-budget-service`、唯一 `binance-egress-gateway`、rate UDS Schema、endpoint cost catalog、rate-budget/egress policy，以及 `aiq_host_rate_control` 的数据库迁移。普通业务应用、策略、风险、订单/成交/持仓 Schema 和业务数据库不在本手册范围；它们使用 [09 业务升级手册](09_UPGRADE_ROLLBACK.md)。

`aiq-host-control` Compose 项目只含 allocator、独立 PostgreSQL 与 `host-attestation-signer`；唯一 gateway 使用独立 `aiq-binance-egress` Compose 项目。二者属于同一 host-control release manifest 和审批，但 gateway 没有 host-control DB credential，也没有 Binance API secret。`execution-service` 是唯一生产 secret 持有者，`testnet-probe-runner` 是唯一 Testnet secret 持有者；二者创建短期不可变预签名请求，gateway 只复算请求事实/hash、原子消费 permit 后发送一次；allocator 不建立 Binance 连接。

本流程不承诺无中断。升级期间固定 `RISK_LOCKED`、全部新 Binance REST/WS API/market-stream control egress 为零，并依赖升级前已经在交易所确认的原生保护。不得使用本地应急追踪、预发 emergency lease、既有 causation/exchange ID 或稍后回填绕过该规则。

## 1. 强制不变量

- host-control release manifest 必须精确绑定 allocator/gateway 镜像 digest、UDS Schema、endpoint catalog、rate/egress policy、host-control migration head、兼容矩阵、测试报告、审批和回退二进制 digest。
- business 与 host-control 使用不同 Compose 上下文、DSN、角色、Alembic 配置、`version_table`、revision namespace、head、备份/WAL 链和审批 challenge。
- 业务容器没有 Binance 外部路由；主机任一时刻最多一个 gateway 可建立 Binance socket/TLS。gateway 只接受 closed IPC 和 catalog 中的固定 authority/endpoint，不接受任意地址或 raw socket 请求。
- 任一发送前，gateway 必须复算 authority、endpoint、method、规范参数、operation facts 和 request hash，调用 `PermitConsumeRequest`；只有 `CONSUME_GRANTED` 可发送一次。
- 窗口 counter、reservation、permit/capability nonce 消费、fencing epoch、header 观测和 429/418 `blocked_until` 只可单调前进。禁止通过旧备份、PITR、downgrade、删行、换卷或清库回拨。
- 业务数据库不可写，或 allocator、host-control DB、UDS、fencing、gateway 任一不可用时，保持零新 Binance egress 并升级 P0；账户所有者只从 Binance 官方控制面处置。

## 2. 前置条件与人工门禁

1. 变更至少按 C3 审批；若属正在利用的安全事件，可按 C4 先控险，但仍不得跳过独立 manifest、备份、签名和本手册不变量。
2. 在无生产 secret 的等效 Testnet/Shadow 环境完成并发 reservation/consume、重复/过期 permit、capability nonce 重放、发送 definite/unknown/not-sent、header 乱序、429/418、allocator fencing、gateway 崩溃、第二 gateway、裸业务 Binance egress 和 schema 双版本兼容测试。
3. 当前与目标 allocator/gateway 都已证明支持业务调用方正在使用的 UDS 版本；若业务调用方需要先升级，先按 09 完成兼容消费者发布，不能在本窗口临时混发不兼容协议。
4. 当前 host-control startup evidence 有效，业务数据库可写，全量对账零差异，所有持仓均有交易所已确认的原生保护。
5. 当前 host-control 基础备份、连续 WAL、counter/consume/fencing/blocked-state 导出可在隔离实例恢复；该备份只用于灾难恢复验证，不是升级回滚点。
6. 账户所有者已签署维护窗口、目标 release hash、当前状态 hash、兼容回退 digest 和“永不回滚权威数据状态”确认。

## 3. 固定发布上下文

以下 `quantctl` 子命令是实现阶段必须提供的受控 CLI 契约。所有 Compose 操作必须复用数组中的同一 project/file/`--env-file`，禁止另行拼接：

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export HOST_CONTROL_ENV_PATH="<HOST_CONTROL_ENV_PATH>"
export GATEWAY_ENV_PATH="<GATEWAY_ENV_PATH>"
export TARGET_HOST_CONTROL_MANIFEST="<SIGNED_TARGET_HOST_CONTROL_MANIFEST>"
export CURRENT_HOST_CONTROL_MANIFEST="<SIGNED_CURRENT_HOST_CONTROL_MANIFEST>"
export OWNER_APPROVAL="<SIGNED_OWNER_APPROVAL>"
export HOST_BACKUP_EVIDENCE_DIR="<HOST_BACKUP_EVIDENCE_DIR>"
export HOST_RESTORE_VERIFY_PATH="<ISOLATED_HOST_RESTORE_VERIFY_PATH>"
export SIGNED_BOOTSTRAP_PLAN="<SIGNED_HOST_CONTROL_BOOTSTRAP_PLAN>"
cd "$PROJECT_DIR"
HC=(docker compose -p aiq-host-control -f deploy/host-control.compose.yaml --env-file "$HOST_CONTROL_ENV_PATH")
EG=(docker compose -p aiq-binance-egress -f deploy/binance-egress.compose.yaml --env-file "$GATEWAY_ENV_PATH")
"${HC[@]}" config --quiet
"${EG[@]}" config --quiet
```

环境文件只用于 Compose 插值，不整份注入容器。`HOST_CONTROL_ENV_PATH` 只能引用专用 DB credential、配置验签公钥、host attestation 私钥和 UDS 路径；`GATEWAY_ENV_PATH` 不得含数据库凭据、API secret、bot token、SFTP/心跳 secret 或 catalog 外网络配置。

## 4. 发布前验证

```bash
quantctl host-control release verify --manifest "$CURRENT_HOST_CONTROL_MANIFEST"
quantctl host-control release verify --manifest "$TARGET_HOST_CONTROL_MANIFEST"
quantctl release scope-verify --manifest "$TARGET_HOST_CONTROL_MANIFEST" \
  --expected host-control \
  --require-components rate-budget-service,binance-egress-gateway,rate-budget-uds,endpoint-cost-catalog,rate-policy,egress-policy,host-control-migrations \
  --deny-business-components --deny-business-dsn --deny-binance-secret
quantctl host-control compatibility verify \
  --current "$CURRENT_HOST_CONTROL_MANIFEST" \
  --target "$TARGET_HOST_CONTROL_MANIFEST" \
  --require-current-business-callers --require-old-and-new-uds-read \
  --require-schema-compatible-binary-fallback
quantctl network egress-verify \
  --require-single-binance-gateway --require-zero-business-binance-route \
  --require-catalog-only --deny-arbitrary-url --deny-raw-socket
quantctl secret exposure-verify \
  --service binance-egress-gateway --deny-api-secret --deny-secret-mount \
  --deny-business-db --deny-host-control-db --deny-core-dump --deny-request-persistence
quantctl host-rate require-ready \
  --require-gateway --require-single-gateway --require-zero-business-binance-route
```

任一验证失败即停止；不得为赶维护窗口放宽 endpoint、permit、secret 或路由边界。

## 5. 进入 `RISK_LOCKED` 并归零新 egress

先在现有健康链路上完成保护与对账，再关闭新 egress：

```bash
quantctl pause-new-entries --environment production \
  --reason "host-control upgrade" --idempotency-key "<COMMAND_ID>"
quantctl protection verify --environment production --all-positions
quantctl reconcile --environment production --full --no-order-retry --fail-on-difference
quantctl state require --environment production --business-db-writable
quantctl risk force-lock --environment production --reason "host-control maintenance"
quantctl host-rate quiesce --all-new-egress --approval "$OWNER_APPROVAL"
quantctl host-rate drain --require-all-send-outcomes --fail-on-unknown --timeout-seconds 60
quantctl network egress-verify \
  --require-zero-new-binance-egress --require-zero-business-binance-route \
  --allow-existing-inbound-relay-until-disconnect
```

`drain` 有未知发送、未消费 permit 或无法证明的 gateway 请求时，不执行升级；保持 `RISK_LOCKED` 并按 P0/P1 调查。quiesce 后发生风险事件时，不重开 egress，依赖原生保护并由账户所有者使用 Binance 官方控制面。

## 6. 独立备份与 expand 迁移

```bash
quantctl backup create \
  --scope host-control-postgres --database aiq_host_rate_control \
  --include-wal --include-counter-consume-fencing-blocked-export \
  --deny-business-dsn --output "$HOST_BACKUP_EVIDENCE_DIR"
quantctl backup verify \
  --scope host-control --restore-target "$HOST_RESTORE_VERIFY_PATH" \
  --require-counter-nondecreasing --require-consume-nonce-set \
  --require-fencing-epoch --require-blocked-state
quantctl database migrate \
  --scope host-control --database aiq_host_rate_control \
  --alembic-config migrations/host_control/alembic.ini \
  --phase expand --release-manifest "$TARGET_HOST_CONTROL_MANIFEST" \
  --approval "$OWNER_APPROVAL" --deny-business-dsn
quantctl database verify \
  --scope host-control --database aiq_host_rate_control \
  --alembic-config migrations/host_control/alembic.ini \
  --migration-head "<EXPECTED_HOST_CONTROL_ALEMBIC_HEAD>" \
  --require-counter-nondecreasing --require-consume-nonce-set \
  --require-fencing-epoch --require-blocked-state
```

只允许 expand。若迁移失败，保留当前数据库状态并前向修复；不得执行 downgrade、恢复刚才的备份、删除 revision/行或重新初始化卷。

## 7. allocator 与 gateway 切换

先切 allocator writer，再切唯一 gateway：

```bash
"${HC[@]}" pull rate-budget-service
quantctl host-control allocator stage \
  --manifest "$TARGET_HOST_CONTROL_MANIFEST" --read-old-and-new-schema \
  --no-permit-issuance
quantctl host-control allocator cutover \
  --manifest "$TARGET_HOST_CONTROL_MANIFEST" \
  --acquire-higher-fencing-epoch --fence-old-writer --atomic
quantctl host-control allocator verify \
  --require-single-writer --require-higher-fencing-epoch \
  --require-counter-nondecreasing --require-blocked-state-preserved \
  --require-quiesced

"${EG[@]}" stop binance-egress-gateway
quantctl network egress-verify --require-zero-binance-socket-owner
"${EG[@]}" pull binance-egress-gateway
quantctl host-control gateway deploy \
  --manifest "$TARGET_HOST_CONTROL_MANIFEST" --approval "$OWNER_APPROVAL" \
  --require-no-secret --require-quiesced
"${EG[@]}" up -d --no-deps binance-egress-gateway
quantctl host-control gateway verify \
  --require-single-gateway --require-no-secret --require-no-persistence \
  --require-hash-recompute --require-atomic-permit-consume \
  --require-send-once --deny-arbitrary-forwarding --require-quiesced
```

旧 allocator 在更高 fencing epoch 生效后只能排空并退出，绝不能再发 permit。旧 gateway 停止且确认零 socket owner 后才启动新 gateway；禁止蓝绿并行两个可发送实例。整个切换期间 business container 的 Binance route 始终为零。

## 8. 受控 bootstrap、对账与保持锁定

只有业务数据库仍可写、host-control 数据状态单调且网络边界通过时，才解除 host-control quiesce，并仅通过签名 bootstrap plan、allocator 和 gateway 执行允许的 `/time`/`exchangeInfo` 等探针：

```bash
quantctl state require --environment production --risk-locked --business-db-writable
quantctl host-control bootstrap-plan verify \
  --plan "$SIGNED_BOOTSTRAP_PLAN" --manifest "$TARGET_HOST_CONTROL_MANIFEST" \
  --require-expiry --require-exact-endpoints --require-no-trade
quantctl host-rate unquiesce-bootstrap-only \
  --plan "$SIGNED_BOOTSTRAP_PLAN" --approval "$OWNER_APPROVAL"
quantctl host-control bootstrap execute \
  --plan "$SIGNED_BOOTSTRAP_PLAN" --through-allocator-and-gateway \
  --require-permit-consume --send-once
quantctl host-rate require-ready \
  --require-gateway --require-single-gateway --require-zero-business-binance-route \
  --require-counter-nondecreasing --require-blocked-state-preserved \
  --require-egress-correlation --output "<NEW_HOST_RATE_STARTUP_EVIDENCE>"
quantctl reconcile --environment production --full --no-order-retry --fail-on-difference
quantctl protection verify --environment production --all-positions
```

完成后仍保持 `RISK_LOCKED`。恢复普通 egress 与新仓必须另行生成本机签名的风险解锁 challenge，证明零订单差异、保护健康、无 P0/P1、业务数据库可写和新 startup evidence 有效；本手册不自动解锁。

## 9. 失败处置与兼容二进制回退

### 9.1 迁移前失败

若尚未改变 host-control Schema/数据，可保持当前 release、撤销维护计划并继续 `RISK_LOCKED`。恢复新仓仍需重新完成 readiness、对账、保护和人工签名，不能复用已消费审批。

### 9.2 迁移后 allocator 失败

优先部署新的前向修复 release。只有兼容报告证明旧 allocator 二进制能读取**当前** Schema/数据、理解当前 UDS，并保持当前 counter/consume/fencing/blocked state 时，才可把二进制切回已签名 digest；它必须取得新的更高 fencing epoch。不得恢复旧数据库/WAL、降低 epoch、清除 nonce 或重置封禁。

### 9.3 gateway 失败

保持 allocator quiesced 和零 Binance egress。可回退到已签名且与当前 allocator/UDS/catalog/policy 兼容的 gateway 二进制，但必须先停止失败实例、确认零 Binance socket owner，再启动唯一回退实例。回退实例仍无 secret，仍须重算 hash、原子 `PermitConsume` 后发送一次；不得临时让 execution-service 或 realtime-engine 直连 Binance。

### 9.4 host-control 数据疑似损坏

立即 P0，保持 `RISK_LOCKED` 和零新 egress，冻结卷/WAL/审计。按 [00 宿主级控制面](00_HOST_RATE_CONTROL.md)与 [08 数据恢复](08_DATA_RECOVERY.md)执行独立灾难恢复：未知窗口按完全耗尽、可疑 418 按全 authority 阻断处理。该流程不是“升级回滚”，恢复后也不能比已知安全状态更少计数或更早解除封禁。

## 10. 验收

- [ ] host-control manifest 只含 allocator、gateway、UDS/Schema/catalog/rate/egress 和 host DB migrations；无业务组件、业务 DSN 或 Binance secret。
- [ ] business/host-control 的 Compose、DSN、角色、Alembic/version table/head、备份/WAL 和审批完全分离。
- [ ] host-control counter、permit/consume nonce、fencing、header 和 429/418 状态相对升级前无回拨；未恢复任何旧数据库状态。
- [ ] 只有一个 allocator writer、一个 gateway 和一个 Binance socket owner；所有业务容器无 Binance 外部路由。
- [ ] gateway 无 secret/业务 DB/持久请求日志/catalog 外地址接口，复算请求事实/hash、原子消费 permit 后最多发送一次；allocator 不建立 Binance 连接。
- [ ] 重复/过期 permit、nonce 重放、第二 gateway、裸业务客户端、任意 URL/CONNECT、发送未知与 429/418 故障演练均 fail-closed。
- [ ] bootstrap、全量对账、原生保护、startup evidence 和 P0/P1 检查通过；系统仍为 `RISK_LOCKED`，未由 Telegram 或升级脚本自动解锁。

任一项失败时保持 `RISK_LOCKED` 和零新 Binance egress，采用前向修复或经证明兼容当前数据状态的二进制回退；不得以恢复旧 counter/数据库状态完成验收。

## 11. 证据留存

保存前后 host-control manifest/digest、兼容矩阵、独立 Compose 指纹、UDS/Schema/catalog/rate/egress hashes、业务调用方版本、变更/审批、维护前对账与保护、quiesce/drain、独立备份/WAL/隔离恢复、迁移日志、前后 migration head、fencing epoch、counter/consume/blocked-state 摘要、旧 writer 退场、gateway socket owner、secret/路由/catalog 外地址扫描、bootstrap plan/结果、egress correlation、最终 readiness、失败与前向修复/二进制回退决定。全部证据脱敏、SHA-256 校验、使用 UTC，并同步独立审计端。
