# 09 升级与回滚运行手册

## 目的

以可验证、可回退的方式发布业务应用、策略、配置、依赖、镜像或业务数据库变更，并在异常时安全回滚。本文**不操作 host-control**；allocator、gateway、UDS/Schema/catalog/rate/egress policy 或 `aiq_host_rate_control` 迁移必须使用 [09A host-control 独立升级手册](09A_HOST_CONTROL_UPGRADE.md)。详细版本归属、迁移和 SLO 见 [维护、升级与回滚](../docs/12_MAINTENANCE_UPGRADE_ROLLBACK.md)。

说明：文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约；发布、迁移、签名和回滚功能未通过 Testnet/Shadow 验证前不得用于生产。实盘启动后的 C2/C3 Testnet/Shadow 默认在另行批准的韩国同区域 validation VPS 上运行，并用 cgroup 精确限制为 2 CPU/12 GiB、配置约 200 GB NVMe/配额；该主机无生产 key/卷/数据库。若采用生产主机互斥验证，必须先安全归零、完全停止 live 并完成事实保全/恢复与容量门禁。禁止在 `aiq-live` 运行时同机并行验证，也禁止用普通回测机报告替代真实 RTT/资源门禁。

## 前置条件与人工门禁

- 变更已分类；交易、风险、策略、Schema、密钥和风险倍率属于高风险变更，要求账户所有者签名。
- 目标 release manifest 和上一已知良好 rollback manifest 均完整，镜像以 digest 固定，依赖有 hash/SBOM/漏洞报告。
- 单元、属性、回放、集成、故障、安全、资源、Testnet 和适用 Shadow 门禁已通过；签名测试报告与 `ValidationEquivalenceProfile` 绑定目标 release、逐字段差异、runner 身份、Ubuntu/kernel/architecture、韩国区域/网络路径、精确 2 CPU/12 GiB cgroup、约 200 GB NVMe/配额、24h RTT/丢包/断连、clock/CPU steal/disk I/O、资源曲线、零生产凭据以及 Testnet 凭据撤销/临时 secret 清理证明。
- PostgreSQL 基础备份/WAL、订单/审计导出已完成，并在隔离卷恢复验证。
- 已盘点挂单/持仓及其 `owner_strategy_version`；该值在首次非零仓位时固化，旧策略包可继续运行到该 position episode 归零。
- 维护窗口已通知；30 秒出站签名心跳、age/X25519 归档与签名回执、告警和紧急平仓可用。
- 已按 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)恢复并封存 startup evidence；升级/回滚只操作业务项目。`aiq-host-control` 与唯一 `binance-egress-gateway` 保持运行，禁止更新其镜像/配置、停止、重建、迁移、回滚专用数据库状态或开放业务容器 Binance 路由。

## 1. 发布前验证

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export RUNTIME_ENV_PATH="<RUNTIME_ENV_PATH>"
export VALIDATION_EQUIVALENCE_PROFILE="<SIGNED_VALIDATION_EQUIVALENCE_PROFILE>"
export HOST_RATE_STARTUP_EVIDENCE="<HOST_RATE_STARTUP_EVIDENCE>"
cd "$PROJECT_DIR"
DC=(docker compose -p aiq-live -f deploy/compose.yaml --env-file "$RUNTIME_ENV_PATH")
quantctl release verify --manifest "<TARGET_RELEASE_MANIFEST>"
quantctl release verify --manifest "<ROLLBACK_RELEASE_MANIFEST>"
quantctl release scope-verify --manifest "<TARGET_RELEASE_MANIFEST>" \
  --expected business --deny-components rate-budget-service,binance-egress-gateway,rate-budget-uds,endpoint-cost-catalog,rate-policy,egress-policy,host-control-migrations \
  --deny-dsn-database aiq_host_rate_control
quantctl release scope-verify --manifest "<ROLLBACK_RELEASE_MANIFEST>" \
  --expected business --deny-components rate-budget-service,binance-egress-gateway,rate-budget-uds,endpoint-cost-catalog,rate-policy,egress-policy,host-control-migrations \
  --deny-dsn-database aiq_host_rate_control
quantctl release compare --from "<ROLLBACK_RELEASE_MANIFEST>" --to "<TARGET_RELEASE_MANIFEST>"
export RELEASE_ID="$(quantctl release field --manifest "<TARGET_RELEASE_MANIFEST>" --pointer /release_id --raw)"
export TARGET_MANIFEST_HASH="$(quantctl artifact sha256 --input "<TARGET_RELEASE_MANIFEST>" --raw)"
"${DC[@]}" config --quiet
quantctl contract validate \
  --schema contracts/validation-equivalence-profile.schema.json \
  --instance "$VALIDATION_EQUIVALENCE_PROFILE" --verify-jcs-hash --verify-signature
quantctl validation attestation-verify \
  --profile "$VALIDATION_EQUIVALENCE_PROFILE" --target-release "<TARGET_RELEASE_MANIFEST>" \
  --require-ubuntu 24.04 --require-target-architecture \
  --require-country KR --require-approved-region --require-same-network-path \
  --require-cgroup-cpu 2 --require-cgroup-memory-bytes 12884901888 \
  --require-nvme-bytes-between 180000000000,220000000000 \
  --require-network-observation-hours 24 --require-rtt-loss-disconnect \
  --require-clock --require-cpu-steal --require-disk-io --require-resource-curve
quantctl validation equivalence-verify \
  --profile "$VALIDATION_EQUIVALENCE_PROFILE" --target-release "<TARGET_RELEASE_MANIFEST>" \
  --exact code_commit,architecture,image_digests,dependency_lock,sbom,openapi,domain_events,migration_head,strategy_package,system_config,risk_config,universe_config,price_action_schema,price_action_config,rate_budget_config,rate_budget_schema,endpoint_cost_catalog,endpoint_cost_catalog_schema,network_egress_policy,testnet_protocol_probe_plan,order_flow,execution,normalized_compose,cgroup_limits \
  --require-target-manifest-file-map --recompute-each-mounted-file \
  --allowed-differences-from-signed-profile --deny-wildcards --fail-on-unlisted-difference \
  --require-unapproved-difference-count 0 --output "<VALIDATION_EQUIVALENCE_VERIFY_REPORT>"
quantctl validation isolation-verify \
  --profile "$VALIDATION_EQUIVALENCE_PROFILE" \
  --deny-production-key --deny-production-database --deny-production-volume --deny-production-control-channel
quantctl validation gate-verify \
  --profile "$VALIDATION_EQUIVALENCE_PROFILE" --class "<C2_OR_C3>" \
  --require-c2-short-shadow --require-c3-shadow-hours 72
quantctl validation cleanup-verify \
  --profile "$VALIDATION_EQUIVALENCE_PROFILE" \
  --require-runner-stopped --require-testnet-credential-revoked --require-ephemeral-secrets-destroyed
quantctl test verify-bundle \
  --report "<SIGNED_TEST_REPORT>" \
  --for-release-manifest "<TARGET_RELEASE_MANIFEST>" \
  --equivalence-profile "$VALIDATION_EQUIVALENCE_PROFILE" \
  --allowed-differences-from-signed-profile --fail-on-unlisted-difference \
  --require-off-production-or-stopped-live \
  --require-korea-equivalent-runner --require-resource-equivalence \
  --require-no-production-credentials
quantctl backup create --scope business-postgres,ledger,audit,config --deny-database aiq_host_rate_control --output "<BACKUP_EVIDENCE_DIR>"
quantctl backup verify --latest --scope business --restore-target "<ISOLATED_VERIFY_PATH>"
quantctl archive crypto-verify --format age-v1 --require-x25519 --recipient-file "$ARCHIVE_AGE_RECIPIENT_FILE" --verify-key "$ARCHIVE_RECEIPT_VERIFY_KEY_FILE"
quantctl heartbeat contract-verify --outbound-only --algorithm Ed25519 --interval-seconds 30 --missing-intervals 3 --max-age-seconds 120
quantctl strategy ownership --include-open-orders --include-open-positions
quantctl host-rate require-ready --evidence "$HOST_RATE_STARTUP_EVIDENCE" --max-age-seconds 300 \
  --require-gateway --require-single-gateway --require-zero-business-binance-route
```

本维护会话后续所有 Compose `pull/up/exec/ps/restart/down` 必须复用 `DC`；project、Compose 文件和 `--env-file` 构成同一个受审发布上下文，禁止分开重拼。

`verify-bundle` 只验证部署前已经封存的外部/互斥 validation 证据，不会在生产项目启动 Testnet 或 Shadow。profile 中只允许固定枚举的差异路径，实际逐字段 diff 必须全部落入该签名 allowlist；规格/网络/资源不等效的报告只能作为功能证据，不能过 C2/C3 资源门禁。任一摘要、测试、runner、清理或恢复证明失败即停止。

## 2. 进入维护状态

```bash
quantctl pause-new-entries --environment production --reason "release <RELEASE_ID>" --idempotency-key "<COMMAND_ID>"
quantctl cancel-pending --environment production --exclude-protective --dry-run --output "<CANCEL_PREVIEW_FILE>"
quantctl protection verify --environment production --all-positions
quantctl reconcile --environment production --full --fail-on-difference
quantctl persistence drain --timeout 60 --fail-if-pending
quantctl state snapshot --environment production --output "<PRE_DEPLOY_SNAPSHOT>"
```

若新旧策略不能并存，不得继续。账户所有者必须选择等待旧持仓自然退出，或按 [05](05_PAUSE_CANCEL_FLATTEN.md)二次确认 reduce-only 平仓。

## 3. 高风险二次确认

```bash
quantctl release challenge \
  --target "<TARGET_RELEASE_MANIFEST>" \
  --rollback "<ROLLBACK_RELEASE_MANIFEST>" \
  --pre-state "<PRE_DEPLOY_SNAPSHOT>" \
  --expires-in 300 \
  --output "<CHALLENGE_FILE>"
quantctl approval sign --challenge "<CHALLENGE_FILE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<APPROVAL_FILE>"
export DEPLOY_CONFIRM_TOKEN="$(quantctl release confirmation-token --action DEPLOY --release-id "$RELEASE_ID" --manifest-hash "$TARGET_MANIFEST_HASH" --raw)"
printf '输入 %s 继续: ' "$DEPLOY_CONFIRM_TOKEN"
read -r CONFIRM
test "$CONFIRM" = "$DEPLOY_CONFIRM_TOKEN" || exit 1
```

Telegram 无发布、风险或策略审批权限。

## 4. 发布顺序

数据库只对业务 DSN 使用 `migrations/business/alembic.ini` 执行向前兼容的 expand 迁移，然后更新非热路径、执行服务和实时引擎；本流程不得读取 host-control migration credential 或连接 `aiq_host_rate_control`，每步失败都停止推进：

```bash
quantctl database migrate --scope business --alembic-config migrations/business/alembic.ini \
  --phase expand --release "<RELEASE_ID>" --approval "<APPROVAL_FILE>" \
  --deny-dsn-database aiq_host_rate_control
"${DC[@]}" pull
quantctl release deploy --manifest "<TARGET_RELEASE_MANIFEST>" --approval "<APPROVAL_FILE>" --risk-locked
"${DC[@]}" ps
curl --fail --silent --show-error http://127.0.0.1:8080/health/live
quantctl database verify --scope business --alembic-config migrations/business/alembic.ini \
  --read-write --migration-head "<EXPECTED_BUSINESS_ALEMBIC_HEAD>" \
  --deny-dsn-database aiq_host_rate_control
quantctl host-rate require-ready --evidence "$HOST_RATE_STARTUP_EVIDENCE" --max-age-seconds 300 \
  --require-gateway --require-single-gateway --require-zero-business-binance-route
quantctl reconcile --environment production --full --no-order-retry --fail-on-difference
quantctl protection verify --environment production --all-positions
quantctl heartbeat verify --outbound-only --algorithm Ed25519 --interval-seconds 30 --missing-intervals 3 --max-age-seconds 120 --require-new-boot-after-restart --reject-old-boot
quantctl archive verify --scope recent --require-age-v1-x25519 --require-remote-decrypt --require-signed-receipt
quantctl replay smoke --dataset "<FIXED_SMOKE_DATASET>" --expected-digest "<EXPECTED_EVENT_DIGEST>"
```

服务启动后保持 `RISK_LOCKED`。新策略只处理新信号；旧持仓仍由原版本管理，全局风险取更严格约束。

## 5. 人工恢复与观察

只有对账零差异、保护健康、订单簿预热、资源/时钟正常、固定回放一致且无 P0/P1，才生成解锁挑战并由本机签名。C1/C2/C3 分别观察至少 30 分钟/2 小时/24 个健康小时。期间监控热路径、订单差异、保护、CPU/内存、数据库 backlog、归档和告警。

```bash
quantctl release verify-runtime --manifest "<TARGET_RELEASE_MANIFEST>"
export RISK_UNLOCK_PRESTATE="$(quantctl state hash --environment production --require-risk-locked --raw)"
export RISK_UNLOCK_EFFECTIVE_AT="$(quantctl time future --lead-seconds 300 --format rfc3339 --raw)"
export RISK_UNLOCK_EXPIRES_AT="$(quantctl time add --at "$RISK_UNLOCK_EFFECTIVE_AT" --seconds 120 --format rfc3339 --raw)"
quantctl risk unlock-challenge --operator-action RISK_UNLOCK --reason "release verified" \
  --report "<POST_DEPLOY_REPORT>" --precondition-state-hash "$RISK_UNLOCK_PRESTATE" \
  --effective-at "$RISK_UNLOCK_EFFECTIVE_AT" --expires-at "$RISK_UNLOCK_EXPIRES_AT" \
  --bind-current-runtime --output "<UNLOCK_CHALLENGE>"
quantctl approval sign --schema contracts/operator-approval.schema.json --expected-action RISK_UNLOCK \
  --challenge "<UNLOCK_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<UNLOCK_APPROVAL>"
quantctl time await --at "$RISK_UNLOCK_EFFECTIVE_AT" --not-after "$RISK_UNLOCK_EXPIRES_AT" --fail-if-late
quantctl risk unlock --challenge "<UNLOCK_CHALLENGE>" --approval "<UNLOCK_APPROVAL>" \
  --effective-at "$RISK_UNLOCK_EFFECTIVE_AT" --precondition-state-hash "$RISK_UNLOCK_PRESTATE" \
  --consume-once --atomic --fail-if-state-changed
quantctl release observe --release "<RELEASE_ID>" --profile "<C1_OR_C2_OR_C3>"
```

### 5.1 观察完成后的镜像容量回收

本机至少保留当前 active digest 与一个已签名 last-known-good rollback digest。只有观察期完成、目标 release 被签名确认为 active，其他旧镜像可从 registry 按 digest 重取、manifest/SBOM/签名已远端归档，且不在事故/取证保全中时，才生成精确 allowlist：

```bash
quantctl image prune-plan \
  --retain-active "<ACTIVE_RELEASE_MANIFEST>" \
  --retain-rollback "<ROLLBACK_RELEASE_MANIFEST>" \
  --require-registry-digest-available \
  --require-remote-manifest-sbom \
  --exclude-incident-hold \
  --output "<IMAGE_PRUNE_PLAN>"
quantctl image prune-verify --plan "<IMAGE_PRUNE_PLAN>" --deny-wildcards --deny-active --deny-rollback
quantctl image prune-challenge --plan "<IMAGE_PRUNE_PLAN>" --expires-in 300 --output "<IMAGE_PRUNE_CHALLENGE>"
quantctl approval sign --challenge "<IMAGE_PRUNE_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<IMAGE_PRUNE_APPROVAL>"
quantctl image prune-execute --plan "<IMAGE_PRUNE_PLAN>" --approval "<IMAGE_PRUNE_APPROVAL>"
quantctl deployment capacity-gate --include-image-inventory --min-free-bytes 30000000000
```

禁止 `docker image prune -a` 或按 tag 模糊删除。回收失败只影响容量，不得通过删除 active/rollback 镜像绕过门禁。

## 6. 回滚触发

重复订单、无保护持仓、订单 `UNKNOWN` 达到 5 秒、账本不一致、数据库写失败、热路径 p99 连续 5 分钟超过门槛 2 倍、CPU/内存/磁盘危险、摘要不符、账户模式错误或任一 P0/P1，立即暂停新仓并进入回滚评估。业务库不可写时严格执行 [00 第 5 节](00_HOST_RATE_CONTROL.md#5-故障语义)：立即 `RISK_LOCKED`，gateway 阻断全部新的 Binance REST/WS API/market-stream control egress；不存在本地耐久应急追踪、既有 causation/exchange ID 放行或稍后回填例外。此时只依赖已确认的交易所原生保护，升级 P0，并由账户所有者使用 Binance 官方控制面处置。

## 7. 回滚执行（高危、二次确认）

只有业务数据库已经恢复可写、host-control startup evidence 有效、唯一 gateway 健康且业务容器仍无 Binance 路由时，才进入本节并对账、确认旧应用兼容当前 Schema。否则不运行任何会产生 Binance 请求的 CLI，继续 `RISK_LOCKED`、依赖既有原生保护并由账户所有者使用官方控制面：

```bash
quantctl pause-new-entries --environment production --reason "rollback <RELEASE_ID>" --idempotency-key "<COMMAND_ID>"
quantctl reconcile --environment production --full --no-order-retry --fail-on-difference
quantctl protection verify --environment production --all-positions
quantctl release rollback-plan \
  --current "<TARGET_RELEASE_MANIFEST>" \
  --rollback "<ROLLBACK_RELEASE_MANIFEST>" \
  --output "<ROLLBACK_PLAN_FILE>"
```

人工核对 plan 后签名并执行：

```bash
quantctl release challenge --rollback-plan "<ROLLBACK_PLAN_FILE>" --expires-in 300 --output "<ROLLBACK_CHALLENGE>"
quantctl approval sign --challenge "<ROLLBACK_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<ROLLBACK_APPROVAL>"
read -r -p "输入 ROLLBACK-<ROLLBACK_RELEASE_ID> 继续: " CONFIRM
test "$CONFIRM" = "ROLLBACK-<ROLLBACK_RELEASE_ID>" || exit 1
quantctl release rollback --plan "<ROLLBACK_PLAN_FILE>" --approval "<ROLLBACK_APPROVAL>" --risk-locked
quantctl reconcile --environment production --full --no-order-retry --fail-on-difference
quantctl protection verify --environment production --all-positions
```

优先回滚业务应用/配置到已签名 business manifest。对已经写入生产数据的业务数据库迁移不直接执行破坏性 downgrade；旧应用不兼容时保持当前 Schema，部署前向兼容修复，或按 [08 数据恢复](08_DATA_RECOVERY.md)在隔离实例 PITR。业务 PITR 不得触碰 `aiq_host_rate_control`；其 counter、permit/consume nonce、fencing、header 和 429/418 状态永不随业务回滚。策略回滚只影响新信号；不安全旧持仓按风险退出。

## 验收

- 运行时镜像、依赖、契约、迁移、策略和配置摘要与目标或回滚 manifest 完全一致。
- business manifest、DSN、Alembic/version table/head 与 host-control 完全分离；allocator/gateway 版本及 `aiq_host_rate_control` 当前状态在业务发布前后未改变。
- 业务容器仍无 Binance 路由；唯一 gateway 无 API secret，只有哈希复算和原子 `PermitConsume` 成功的请求可发送一次，allocator 不建立 Binance 连接。
- age 工具/recipient/回执验签公钥和 Ed25519 心跳契约摘要与目标或回滚 manifest 一致；发布后新心跳 boot 生效，旧 boot/重放包未刷新 last-seen。
- 数据库、REST/用户流、订单/成交/持仓/保护对账差异为 0，零重复订单。
- 旧策略包继续管理其持仓，或已有二次确认的安全退出证据。
- 发布/回滚后保持 `RISK_LOCKED` 直到本机签名恢复；Telegram 未参与审批。
- 观察窗口满足 SLO，无开放 P0/P1；证据远端归档。

## 停止与升级条件

无法证明 Schema 兼容、备份不可恢复、保护缺失、交易所差异无法归零或 rollback manifest 不可信时，不继续尝试。保持 `RISK_LOCKED`，升级 P0/P1；必要时紧急平仓或转数据恢复。不得现场拼装未测试镜像、手工改数据库或删除迁移历史。

## 证据留存

保存变更申请、前后 manifest、完整 Compose 上下文指纹、镜像/依赖/SBOM、validation runner 身份/区域/规格/RTT/资源与零生产凭据证明、测试、备份恢复、age 工具/recipient/回执验签公钥指纹、最近远端解密回执、心跳新旧 boot/sequence 验证、持仓策略归属、预发布快照、挑战/签名/确认、迁移日志、健康检查、对账、保护、观察指标、镜像回收计划、回滚触发和事故时间线。所有证据脱敏、SHA-256 校验并同步独立审计端。
