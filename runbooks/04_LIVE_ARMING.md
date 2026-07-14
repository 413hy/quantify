# 04 实验实盘启用与风险倍率解锁手册

## 目的

在全部人工门禁通过后，把已验证 release 从 Shadow/Testnet 晋升到 `EXPERIMENTAL_LIVE`：首阶段风险倍率固定 `0.10`；从首个真实订单被交易所接受起连续 24 小时通过门禁后，才可由账户所有者再次人工批准恢复 `1.00`。本操作高风险，不能通过 Telegram 执行。

说明：文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约；该 CLI、签名、挑战和审计未通过验收前，禁止用临时命令启用生产交易。

## 前置条件

- [03 Shadow 72h](03_SHADOW_72H.md)报告已签名：连续至少 72 个健康小时，零未解决订单差异、重复订单、无保护持仓和开放 P0/P1。
- `shadow-72h` gate 已停止；进入 live 前必须让 validation Testnet 零持仓、零挂单，封存 `aiq-validation` 最终证据并将项目完整 `down`。2 vCPU/12 GiB 主机上禁止 `aiq-validation` 与 `aiq-live` 并行运行。
- 账户所有者已复核韩国 VPS、实名账户、实际地区、Futures API 资格和当时条款；静态 IP 白名单生效，24 小时 RTT/丢包基线通过。
- release manifest 绑定镜像、依赖、契约、迁移、C0 策略包、`CalibrationDatasetManifest` 根、参数候选、风险配置和回滚版本；备份已在隔离卷恢复。
- live 只读挂载的 PA YAML/Schema hash 必须逐字等于 C0 中的 `price_action_config_hash`/`price_action_schema_hash`，并等于 T0 前签署的 OF 搜索计划；`baseline_origin` 保持工程来源标签，不通过改 YAML 晋升。
- 生产账户只读核验为 USDⓈ-M Futures、单向持仓、全仓保证金；当前挂单/持仓/余额已形成双边快照。
- 生产 API Key 禁止提现且仅有必要交易权限，只读挂载到 execution-service；其他服务不可见。
- 30 秒出站 Ed25519 签名心跳、Telegram/飞书单向通知、交易所原生保护、本机/私网紧急平仓和 [06 重启对账](06_RESTART_RECONCILIATION.md)均已演练；心跳和聊天接收端不存在反向控制通道。
- 最近的 L2/备份样本已在远端使用 age X25519 identity 成功解密，明文 hash/Parquet 校验和 Ed25519 签名回执有效；VPS 上不存在 age 解密私钥。
- 当前 `StrategyPackage` 已有连续追加的 `FREEZE_CHAMPION → APPROVE_SHADOW → APPROVE_TESTNET` StrategyApproval 前驱链，且 package/release/72h evidence 摘要和 Ed25519 签名均有效。`ARM_EXPERIMENTAL_LIVE` 不得提前签署；它必须等 live 以 `RISK_LOCKED` 启动并完成运行时字节证明后才生成。
- [03 手册](03_SHADOW_72H.md)生成的签名 `PLANNED_VALIDATION_TO_LIVE_CUTOVER` 计划可用，最长 14,400 秒且只能消费一次；没有该计划不得停止 validation。
- 状态与所有报告显著显示 `EXPERIMENTAL_LIVE`；不得作统计稳健或盈利保证。
- `aiq-host-control` 已按 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)独立恢复并通过验收，validation→live 切换不会停止、重建或回拨其 counter、fencing 与 429/418；任何生产账户/规则查询都必须在该门禁之后。

## 从 validation 切换到 live（首次进入）

先建立两套不可混用的 Compose 上下文并只做渲染校验。`<VALIDATION_ENV_PATH>` 与 `<RUNTIME_ENV_PATH>` 必须是不同文件，目标配置不得引用 validation 的网络、卷、database、角色、队列或 Redis 前缀：

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export VALIDATION_ENV_PATH="<VALIDATION_ENV_PATH>"
export RUNTIME_ENV_PATH="<RUNTIME_ENV_PATH>"
export HOST_RATE_STARTUP_EVIDENCE="<HOST_RATE_STARTUP_EVIDENCE>"
cd "$PROJECT_DIR"
VDC=(docker compose -p aiq-validation -f deploy/compose.yaml --env-file "$VALIDATION_ENV_PATH")
LDC=(docker compose -p aiq-live -f deploy/compose.yaml --env-file "$RUNTIME_ENV_PATH")
"${VDC[@]}" --profile dual-validation config --quiet
"${LDC[@]}" config --quiet
quantctl host-rate require-ready --evidence "$HOST_RATE_STARTUP_EVIDENCE" --max-age-seconds 300
quantctl isolation verify-transition \
  --source-project aiq-validation \
  --source-compose deploy/compose.yaml \
  --source-env "$VALIDATION_ENV_PATH" \
  --target-project aiq-live \
  --target-compose deploy/compose.yaml \
  --target-env "$RUNTIME_ENV_PATH" \
  --deny-shared-network \
  --deny-shared-volumes \
  --deny-shared-databases \
  --deny-shared-facts
```

停止 gate 和两个 lane 的新动作；Testnet 仍有仓位时必须先按 [05](05_PAUSE_CANCEL_FLATTEN.md)完成二次确认平仓，再重新执行以下断言。不得把“已发平仓请求”当成零持仓：

```bash
quantctl gate status --name shadow-72h --assert-stopped --require-valid-hours 72
quantctl oos cutover-begin \
  --plan "<CUTOVER_PLAN>" --approval "<CUTOVER_PLAN_APPROVAL>" \
  --reason PLANNED_VALIDATION_TO_LIVE_CUTOVER --single-use \
  --output "<CUTOVER_START_EVIDENCE>"
quantctl pause-new-entries --project aiq-validation --environment shadow --reason "validation to live transition"
quantctl pause-new-entries --project aiq-validation --environment testnet --reason "validation to live transition"
quantctl cancel-pending --project aiq-validation --environment shadow --exclude-protective
quantctl cancel-pending --project aiq-validation --environment testnet --exclude-protective
quantctl reconcile --project aiq-validation --environment testnet --full --fail-on-difference
quantctl positions list --project aiq-validation --environment testnet --assert-flat
quantctl protection cleanup-stale --project aiq-validation --environment testnet --require-flat
quantctl orders list --project aiq-validation --environment testnet --status open --assert-empty
quantctl persistence freeze-writers --project aiq-validation --reason validation-to-live-cutover
quantctl persistence drain --project aiq-validation --require-zero-inflight --require-zero-unflushed
quantctl evidence export --project aiq-validation --gate shadow-72h --output "<VALIDATION_FINAL_EVIDENCE_DIR>"
quantctl backup create \
  --project aiq-validation --scope database,wal,ledger,audit,config,gate-report \
  --encrypt age-x25519 --remote-required --output "<VALIDATION_FINAL_BACKUP>"
quantctl backup remote-verify \
  --backup "<VALIDATION_FINAL_BACKUP>" --require-ciphertext-hash \
  --require-remote-decrypt --require-plaintext-hash --require-signed-receipt \
  --reject-replay --output "<VALIDATION_FINAL_BACKUP_REMOTE_EVIDENCE>"
quantctl backup verify \
  --backup "<VALIDATION_FINAL_BACKUP>" --restore-target "<ISOLATED_VALIDATION_RESTORE_PATH>" \
  --verify-ledger-projection --verify-audit-chain --output "<VALIDATION_RESTORE_EVIDENCE>"
"${VDC[@]}" --profile dual-validation down --remove-orphans
if docker ps --all --quiet --filter label=com.docker.compose.project=aiq-validation | grep --quiet .; then
  echo "aiq-validation 仍有残留容器，禁止启动 aiq-live" >&2
  exit 1
fi
quantctl deployment assert-project-stopped \
  --project aiq-validation \
  --assert-no-containers \
  --assert-no-published-ports \
  --assert-no-attached-networks \
  --allow-sealed-volumes
quantctl evidence seal-project \
  --project aiq-validation --require-project-stopped \
  --include database-catalog,volume-inventory,compose-context,gate-report,final-backup,remote-backup-receipt,cutover-start \
  --gate-report "<SIGNED_GATE_REPORT>" --final-backup "<VALIDATION_FINAL_BACKUP>" \
  --deny-reuse-by aiq-live --output "<SEALED_VALIDATION_EVIDENCE>"
sha256sum "<SEALED_VALIDATION_EVIDENCE>"/*
```

`down` 故意不带 `-v`，防止未经核验整卷删除；validation 卷绝不挂给 live。接着必须按 [磁盘与归档事故手册 B2](07_DISK_ARCHIVE_INCIDENT.md#b2-阶段互斥项目的退役容量回收)执行签名退役，固定替换为 `RETIRED_PROJECT=aiq-validation`、`NEXT_PROJECT=aiq-live`、`RETIRED_PROJECT_SEAL=<SEALED_VALIDATION_EVIDENCE>`、`RESTORE_EVIDENCE=<VALIDATION_RESTORE_EVIDENCE>`。B2 先清理逐对象 `REMOTE_VERIFIED` 的大体积 L2/可重建缓存；若仍不足，只有完整事实备份远端验证、隔离恢复、精确 volume allowlist 和二次签名全部通过，才可退役这个已停止非生产项目的本地 PostgreSQL/WAL 卷。事实远端加密副本继续按保留策略存在，本机保留小型只读 catalog/seal/回执；任何未验证唯一副本或 `capacity-gate` 失败都要求先扩容并阻断 live。

确认源项目完全停止、受控退役完成且 200 GB 总盘仍可为新项目保留至少 30 GB 空闲后，才初始化全新的 live 资源并以 `RISK_LOCKED` 启动；若 `aiq-live` 已存在于一次较早的合法实盘尝试，则必须保留并对账其事实，绝不能用“重新初始化”清除历史：

```bash
quantctl deployment prepare-project \
  --project aiq-live \
  --initialize-if-absent \
  --fresh-network \
  --fresh-volumes \
  --fresh-database \
  --deny-source-project aiq-validation \
  --source-seal "<SEALED_VALIDATION_EVIDENCE>" \
  --never-reset-existing-facts
quantctl host-rate require-ready --evidence "$HOST_RATE_STARTUP_EVIDENCE" --max-age-seconds 300
"${LDC[@]}" up -d --wait postgres redis monitoring
"${LDC[@]}" run --rm --no-deps app-migrations alembic upgrade head
quantctl database migration-verify --project aiq-live --database aiq_live --expected-head "<EXPECTED_ALEMBIC_HEAD>" --read-write
"${LDC[@]}" up -d --wait
"${LDC[@]}" ps
quantctl environment assert --project aiq-live --expected production --runtime-state RISK_LOCKED
quantctl isolation verify-transition \
  --source-project aiq-validation \
  --source-seal "<SEALED_VALIDATION_EVIDENCE>" \
  --target-project aiq-live \
  --require-source-stopped \
  --deny-shared-volumes \
  --deny-shared-databases \
  --deny-shared-facts
quantctl prewarm await \
  --project aiq-live --environment production --runtime-state RISK_LOCKED \
  --require-orderbook-ready --require-user-stream-ready --require-archive-ready --require-monitoring-ready
quantctl release binding-attest \
  --project aiq-live --environment production --manifest "<SIGNED_RELEASE_MANIFEST>" \
  --strategy-package "<C0_STRATEGY_PACKAGE>" \
  --exact image,config,schema,migration,strategy,risk,universe,price-action,order-flow,execution \
  --require-byte-identical --require-risk-locked --output "<LIVE_RUNTIME_BINDING_ATTESTATION>"
quantctl oos cutover-end \
  --plan "<CUTOVER_PLAN>" --start-evidence "<CUTOVER_START_EVIDENCE>" \
  --live-runtime-attestation "<LIVE_RUNTIME_BINDING_ATTESTATION>" \
  --max-duration-seconds 14400 --record-missing-objects --count-as-zero-exposure \
  --deny-backfill --fail-and-mark-oos-data-quality \
  --output "<CUTOVER_FINAL_EVIDENCE>"
```

`app-migrations` 是一次性任务；迁移或 head 校验失败时 execution-service 不得启动。已有合法 `aiq-live` 事实时，迁移只能向前执行且必须具备已验证备份，绝不能以重建 database 代替迁移。本切换会话的 validation 操作只能复用 `VDC`，live 操作只能复用 `LDC`；两者都不得省略 project、Compose 文件或 `--env-file`。只有最终 `verify-transition`、运行时 binding attestation 和 cutover-end 全部通过，才能继续。计划缺口超过 14,400 秒、出现第二个计划缺口或发生未登记缺失时，`D_OOS_87D` 标为 `OOS_DATA_QUALITY_FAILED`，live 保持 `RISK_LOCKED`，90 天自动通过资格取消；禁止延长计划、回填或静默删除这段墙钟时间。

## 第一阶段：以 0.10 启用

先执行只读预检：

```bash
quantctl release verify --manifest "<SIGNED_RELEASE_MANIFEST>"
quantctl release binding-verify \
  --manifest "<SIGNED_RELEASE_MANIFEST>" \
  --strategy-package "<C0_STRATEGY_PACKAGE>" \
  --calibration-dataset "<CALIBRATION_DATASET_MANIFEST>" \
  --parameter-candidate "<C0_PARAMETER_CANDIDATE>" \
  --require-migration-head "<EXPECTED_ALEMBIC_HEAD>" \
  --require-explicit-price-action-config-hash \
  --require-explicit-price-action-schema-hash --require-of-search-plan-hash
quantctl gate verify --name shadow-72h --report "<SIGNED_GATE_REPORT>"
quantctl strategy lifecycle-chain-verify \
  --package "<C0_STRATEGY_PACKAGE>" \
  --approvals "<C0_STRATEGY_APPROVAL>,<SHADOW_STRATEGY_APPROVAL>,<TESTNET_STRATEGY_APPROVAL>" \
  --require-stages CHAMPION_FROZEN,SHADOW_APPROVED,TESTNET_APPROVED \
  --require-release "<SIGNED_RELEASE_MANIFEST>" --require-evidence "<SIGNED_GATE_REPORT>"
quantctl release binding-attestation-verify \
  --attestation "<LIVE_RUNTIME_BINDING_ATTESTATION>" \
  --project aiq-live --manifest "<SIGNED_RELEASE_MANIFEST>" \
  --strategy-package "<C0_STRATEGY_PACKAGE>" --require-byte-identical --require-risk-locked \
  --require-price-action-bindings --require-of-search-plan-binding
quantctl oos cutover-verify \
  --plan "<CUTOVER_PLAN>" --evidence "<CUTOVER_FINAL_EVIDENCE>" \
  --max-duration-seconds 14400 --require-zero-exposure --deny-backfill --require-data-quality-pass
quantctl compliance verify --approval "<COMPLIANCE_APPROVAL_FILE>"
quantctl secrets inspect-metadata --service execution-service --expected-scope production
quantctl access-matrix verify --compose deploy/compose.yaml --project aiq-live --env-file "$RUNTIME_ENV_PATH" --allow-binance-secret-only execution-service --require-production-scope --deny-testnet-secrets --deny-whole-secret-directory-mount --redact
quantctl access-matrix verify-runtime --project aiq-live --allow-binance-secret-only execution-service --require-production-scope --deny-testnet-secrets --redact
quantctl exchange verify-account --environment production --expected-position-mode one-way --expected-margin-mode cross --read-only
quantctl reconcile --environment production --full --read-only --fail-on-difference
quantctl backup verify --latest --restore-target "<ISOLATED_VERIFY_PATH>"
quantctl archive verify --scope recent --require-age-v1-x25519 --require-remote-decrypt --require-signed-receipt
quantctl heartbeat verify --outbound-only --algorithm Ed25519 --interval-seconds 30 --missing-intervals 3 --max-age-seconds 120 --require-replay-drill
quantctl preflight run --gate experimental-live-0.10
```

任何命令失败都停止。此时才允许基于 live runtime attestation 生成 `StrategyApproval(action=ARM_EXPERIMENTAL_LIVE)`；随后再生成一次性的 `OperatorApproval(action=LIVE_ARM)`。前者批准生命周期晋升，后者授权把这个精确生产运行时以 `0.10` 启用。两者必须分别验签、绑定同一 package/release/gate/runtime/cutover/pre-state，任一缺失、过期或摘要不一致都拒绝；它们不能互相替代。

```bash
export RELEASE_ID="$(quantctl release field --manifest "<SIGNED_RELEASE_MANIFEST>" --pointer /release_id --raw)"
export STRATEGY_PACKAGE_SHA256="$(quantctl contract field --instance "<C0_STRATEGY_PACKAGE>" --pointer /package_hash --raw)"
export LIVE_RUNTIME_ATTESTATION_HASH="$(quantctl artifact sha256 --input "<LIVE_RUNTIME_BINDING_ATTESTATION>" --raw)"
export PRECONDITION_STATE_HASH="$(quantctl state hash --project aiq-live --environment production --require-risk-locked --raw)"
export ARM_EFFECTIVE_AT="$(quantctl time future --lead-seconds 300 --format rfc3339 --raw)"
export ARM_EXPIRES_AT="$(quantctl time add --at "$ARM_EFFECTIVE_AT" --seconds 120 --format rfc3339 --raw)"
quantctl strategy approval-challenge \
  --package "<C0_STRATEGY_PACKAGE>" --action ARM_EXPERIMENTAL_LIVE \
  --resulting-stage EXPERIMENTAL_LIVE --target-environment production \
  --predecessor "<TESTNET_STRATEGY_APPROVAL>" --release "<SIGNED_RELEASE_MANIFEST>" \
  --evidence "<SIGNED_GATE_REPORT>,<LIVE_RUNTIME_BINDING_ATTESTATION>,<CUTOVER_FINAL_EVIDENCE>" \
  --effective-at "$ARM_EFFECTIVE_AT" --expires-at "$ARM_EXPIRES_AT" \
  --output "<LIVE_ARM_STRATEGY_CHALLENGE>"
quantctl approval sign \
  --schema contracts/strategy-approval.schema.json \
  --challenge "<LIVE_ARM_STRATEGY_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" \
  --output "<LIVE_ARM_STRATEGY_APPROVAL>"
quantctl strategy approval-verify \
  --package "<C0_STRATEGY_PACKAGE>" --approval "<LIVE_ARM_STRATEGY_APPROVAL>" \
  --require-action ARM_EXPERIMENTAL_LIVE --require-predecessor "<TESTNET_STRATEGY_APPROVAL>" \
  --require-release-id "$RELEASE_ID" --require-effective-at "$ARM_EFFECTIVE_AT" \
  --require-evidence "<SIGNED_GATE_REPORT>,<LIVE_RUNTIME_BINDING_ATTESTATION>,<CUTOVER_FINAL_EVIDENCE>"
quantctl live challenge \
  --operator-action LIVE_ARM \
  --release "$RELEASE_ID" \
  --strategy-approval "<LIVE_ARM_STRATEGY_APPROVAL>" \
  --strategy-package-hash "$STRATEGY_PACKAGE_SHA256" \
  --runtime-attestation-hash "$LIVE_RUNTIME_ATTESTATION_HASH" \
  --precondition-state-hash "$PRECONDITION_STATE_HASH" \
  --effective-at "$ARM_EFFECTIVE_AT" --expires-at "$ARM_EXPIRES_AT" \
  --risk-multiplier 0.10 \
  --label EXPERIMENTAL_LIVE \
  --output "<LIVE_ARM_CHALLENGE_FILE>"
quantctl approval sign \
  --schema contracts/operator-approval.schema.json \
  --expected-action LIVE_ARM \
  --challenge "<LIVE_ARM_CHALLENGE_FILE>" \
  --key "<OWNER_SIGNING_KEY_PATH>" \
  --output "<LIVE_ARM_OPERATOR_APPROVAL_FILE>"
export LIVE_ARM_CONFIRM_TOKEN="$(quantctl live confirmation-token \
  --release-id "$RELEASE_ID" --effective-at "$ARM_EFFECTIVE_AT" \
  --precondition-state-hash "$PRECONDITION_STATE_HASH" --risk-multiplier 0.10 --raw)"
printf '输入 %s 继续: ' "$LIVE_ARM_CONFIRM_TOKEN"
read -r CONFIRM
test "$CONFIRM" = "$LIVE_ARM_CONFIRM_TOKEN" || exit 1
quantctl time await --at "$ARM_EFFECTIVE_AT" --not-after "$ARM_EXPIRES_AT" --fail-if-late
quantctl live arm \
  --challenge "<LIVE_ARM_CHALLENGE_FILE>" \
  --strategy-approval "<LIVE_ARM_STRATEGY_APPROVAL>" \
  --operator-approval "<LIVE_ARM_OPERATOR_APPROVAL_FILE>" \
  --runtime-attestation "<LIVE_RUNTIME_BINDING_ATTESTATION>" \
  --precondition-state-hash "$PRECONDITION_STATE_HASH" \
  --effective-at "$ARM_EFFECTIVE_AT" --operator-confirmation "$CONFIRM" \
  --append-strategy-approval --consume-operator-approval-once --atomic --fail-if-state-changed
```

立即核验：

```bash
quantctl status --environment production
quantctl risk show --effective --assert-multiplier 0.10
quantctl reconcile --environment production --full --fail-on-difference
curl --fail --silent --show-error -H "Authorization: Bearer <SHORT_LIVED_LOCAL_SESSION>" -H "X-Correlation-ID: <UUID_OR_ULID>" http://127.0.0.1:8080/v1/status | jq -e '.environment == "production" and .runtime_state == "EXPERIMENTAL_LIVE" and .risk_multiplier == "0.10" and .risk_locked == false and .new_entries_allowed == true and (.release_id | length > 0)'
```

预期为 `environment=production`、`runtime_state=EXPERIMENTAL_LIVE`、`risk_multiplier=0.10`，且 `release_id` 与批准工件逐字一致。倍率同时应用于仓位和所有货币风险限额；绝不能仅缩小某一项。

## 首单接受后的连续 24 小时

- 风险倍率 `0.10` 生效后等待首个真实订单被交易所接受；以首个生产 `OrderEvent(state=ACKNOWLEDGED)` 且 `exchange_status=NEW`（或交易所等价的明确接受状态）的 `occurred_at` UTC 时间为门禁起点，连续计时 24 小时。
- 观测缺口超过 5 分钟、进入 `RISK_LOCKED` 或发生任一 P0/P1 都使本轮失败；修复并重新审批后，从下一次符合条件的真实订单被接受时重新开始，禁止累计拼接。
- 每 2 小时检查订单差异、保护、PnL、连亏、日损/回撤、杠杆、资源、归档和时钟；任何 P0/P1 立即停止并使解锁资格失效。
- 运行状态、本机查询、Telegram 通知和日报始终显示 `EXPERIMENTAL_LIVE / 0.10`。
- 前三天 Order Flow 参数优化属于明确高过拟合例外；当前 champion 在预定点冻结，后续 87 天样本外数据不能反向修改 champion。

```bash
quantctl gate status --name live-first-24h --show-continuous-hours
quantctl reconcile --environment production --full --read-only --fail-on-difference
quantctl risk show --effective --assert-multiplier 0.10
quantctl archive verify --scope recent --require-age-v1-x25519 --require-remote-decrypt --require-signed-receipt
quantctl heartbeat status --outbound-only --interval-seconds 30 --missing-intervals 3 --max-age-seconds 120
quantctl evidence export --gate live-first-24h --output "<EVIDENCE_DIR>"
```

## 第二阶段：人工切换至 1.00

`1.00` 只恢复 [风险设计](../docs/04_RISK_AND_EXECUTION.md)中的完整硬上限，不代表目标仓位或统计验证完成。必须从首个真实订单被接受起连续通过 24 小时、零开放 P0/P1、零差异、零无保护持仓，并重新验证备份/资源/合规。原 `StrategyApproval` 和首次 `OperatorApproval(action=LIVE_ARM)` 都不能授权这次切换；必须生成全新、一次性且 action 固定为 `SET_RISK_MULTIPLIER` 的 `OperatorApproval`。

```bash
quantctl gate verify --name live-first-24h --require-continuous-hours 24 --fail-on-open-p0-p1 --max-observability-gap 5m
quantctl reconcile --environment production --full --read-only --fail-on-difference
export RISK_PRECONDITION_STATE_HASH="$(quantctl state hash --project aiq-live --environment production --raw)"
export RISK_EFFECTIVE_AT="$(quantctl time future --lead-seconds 300 --format rfc3339 --raw)"
export RISK_EXPIRES_AT="$(quantctl time add --at "$RISK_EFFECTIVE_AT" --seconds 120 --format rfc3339 --raw)"
quantctl live challenge \
  --operator-action SET_RISK_MULTIPLIER \
  --release "$RELEASE_ID" \
  --strategy-package-hash "$STRATEGY_PACKAGE_SHA256" \
  --precondition-state-hash "$RISK_PRECONDITION_STATE_HASH" \
  --effective-at "$RISK_EFFECTIVE_AT" --expires-at "$RISK_EXPIRES_AT" \
  --risk-multiplier 1.00 \
  --keep-label EXPERIMENTAL_LIVE \
  --output "<RISK_MULTIPLIER_CHALLENGE_FILE>"
quantctl approval sign \
  --schema contracts/operator-approval.schema.json \
  --expected-action SET_RISK_MULTIPLIER \
  --challenge "<RISK_MULTIPLIER_CHALLENGE_FILE>" \
  --key "<OWNER_SIGNING_KEY_PATH>" \
  --output "<RISK_MULTIPLIER_OPERATOR_APPROVAL_FILE>"
export RISK_CONFIRM_TOKEN="$(quantctl live confirmation-token \
  --action SET_RISK_MULTIPLIER --release-id "$RELEASE_ID" \
  --effective-at "$RISK_EFFECTIVE_AT" --precondition-state-hash "$RISK_PRECONDITION_STATE_HASH" \
  --risk-multiplier 1.00 --raw)"
printf '输入 %s 继续: ' "$RISK_CONFIRM_TOKEN"
read -r CONFIRM
test "$CONFIRM" = "$RISK_CONFIRM_TOKEN" || exit 1
quantctl time await --at "$RISK_EFFECTIVE_AT" --not-after "$RISK_EXPIRES_AT" --fail-if-late
quantctl live set-risk-multiplier \
  --value 1.00 \
  --challenge "<RISK_MULTIPLIER_CHALLENGE_FILE>" \
  --operator-approval "<RISK_MULTIPLIER_OPERATOR_APPROVAL_FILE>" \
  --effective-at "$RISK_EFFECTIVE_AT" --precondition-state-hash "$RISK_PRECONDITION_STATE_HASH" \
  --operator-confirmation "$CONFIRM" --consume-once --atomic --fail-if-state-changed
quantctl risk show --effective --assert-multiplier 1.00
```

Telegram 没有上述命令权限；任何远程聊天消息均不能替代本机签名。

## 验收

- release/config/策略/风险/迁移/镜像摘要与 72h 报告完全一致。
- `StrategyApproval(ARM_EXPERIMENTAL_LIVE)` 的生命周期链与 `OperatorApproval(LIVE_ARM)` 分别有效且同时被首次 arm 消费；切换 `1.00` 使用另一份 `OperatorApproval(SET_RISK_MULTIPLIER)`，不存在跨 action 或重复消费。
- `aiq-validation` 的 Testnet 为零持仓/零挂单，gate 已停止；writers 已冻结/排空，最终 DB/WAL/ledger/audit 备份已远端验证并隔离恢复，停止后 seal 绑定 gate report 和该备份；没有 validation 容器、端口或附着网络与 live 并存。`aiq-live` 使用全新网络、卷、database 和事实命名空间，未复用或导入 validation 事实。
- 唯一计划 cutover 不超过 14,400 秒，实际起止、缺失对象和零暴露进入 OOS/Day 90 证据；live runtime 与签名 release/package 字节一致且 arm 前保持 `RISK_LOCKED`。
- 首阶段所有有效风险限额均乘 `0.10`；24 小时从首个真实订单被接受起连续计算，不健康事件使本轮失败而非暂停累计。
- `1.00` 切换有独立挑战、签名、二次确认和完整审计；标签仍为 `EXPERIMENTAL_LIVE`。
- 所有生产持仓有交易所原生保护，账本与交易所零差异，资源/时钟/归档健康。
- 归档 `REMOTE_VERIFIED` 均有远端解密/结构校验与有效签名回执；30 秒出站心跳持续可验签，连续 3 个间隔（约 90 秒）缺失告警和 120 秒防重放窗口均已取证。
- 90 天门禁尚未通过时，不显示“正式验证”“稳定盈利”等结论。

## 停止与升级条件

任一 P0/P1、合规变化、摘要不符、密钥权限过大、订单 `UNKNOWN` 达到 5 秒、首 fill 后保护未在 1,000 ms 内确认、时钟 >100 ms、原始区 ≥72 GB、文件系统 ≥90%、可用磁盘 <20 GB 或其他资源超限，立即停止新仓并转 `RISK_LOCKED`。业务库不可写时出口网关阻断全部新的 Binance REST、WS API 与 market-stream control 请求；V1 没有本地应急日志或延后回填例外，只依赖已确认的交易所原生保护并升级 P0，由账户所有者通过 Binance 官方控制面处置。不得自动恢复 `0.10` 或 `1.00`；修复、复测和新人工签名后再开始。

## 证据留存

保存合规审批、RTT、静态 IP/key fingerprint、72h 报告、validation Testnet 清仓/撤单/对账、writers freeze/drain、最终备份/远端回执/隔离恢复、停止后 seal、`VDC/LDC` 上下文指纹、validation 无残留资源和 live 新网络/卷/database/事实隔离证明、cutover plan/批准/实际起止/缺失对象/零暴露、live runtime binding attestation、release manifest、`StrategyApproval` predecessor 链与验签结果、`LIVE_ARM` 和 `SET_RISK_MULTIPLIER` 两份独立 `OperatorApproval` 的 payload hash/签名/`effective_at`/一次性消费记录、账户只读快照、age recipient/回执验签公钥指纹、最近远端解密回执、心跳接收端 last-seen 与防重放演练、挑战摘要、确认时间、0.10/1.00 生效配置、24 健康小时明细、每次对账和事故记录。证据加密、SHA-256 校验并同步独立审计端。
