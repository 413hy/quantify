# 03 Shadow/Paper + Testnet 72 小时验证运行手册

## 目的

在 Binance 生产公开行情负载下运行 Shadow/Paper，同时以 Binance Testnet 自身的轻量行情、规则和用户流运行隔离的协议探针，证明 2 vCPU/12 GiB VPS 的数据、策略、风险、执行协议、资源和故障响应达到连续 72 小时门禁。两个 lane 不是同一市场，不比较信号、价格、成交或 PnL。完整阈值见 [测试与验收](../docs/09_TESTING_AND_ACCEPTANCE.md)。

说明：文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约；实现前只定义自动化验收行为，不能以手工改数据库代替。

## 前置条件与人工门禁

- [02A 三日校准](02A_CALIBRATION_3D.md)已结束：`CalibrationDatasetManifest.signed_payload.data_quality.status=QUALIFIED`，`aiq-calibration` 已签名封存、停止并退役，参数候选、校准证据、代码、Schema、配置和镜像预发布素材已固定；用于选择参数的三日窗口不得计入本手册的 72 小时。此时尚未创建最终 C0、最终 release 或 `FREEZE_CHAMPION` 批准。
- `D_OOS_87D` 只从本手册中预热完成后的未来 `effective_at` 开始封存；本门禁只证明固定 release 的工程与安全性质，不证明策略盈利。

- [02 Testnet](02_TESTNET.md)全部通过，零开放 P0/P1；独立 `aiq-testnet` 已零持仓/零挂单停止，database catalog、卷清单和证据已签名封存，远端恢复与退役容量门禁已经证明下一项目可在 200 GB 内保留至少 30 GB 空闲。
- Shadow 只写 Paper 账本，不持有任何交易 key，只消费 Binance 生产公开行情。Testnet 只持有 Testnet key，只消费 Testnet 自身轻量 book/mark price/`exchangeInfo` 和用户流。二者在单一 `aiq-validation` 项目中只共享监控、控制、归档和 PostgreSQL/Redis **进程**；不共享行情事件、规则快照或交易事实，并使用不同 database/角色、耐久队列、订单前缀、Redis ACL/key 前缀和审计流。
- `aiq-validation` 的两个 database、队列和事实窗口必须全新创建；不得挂载、复制或导入 `aiq-testnet` 或 `aiq-calibration` 的卷或事实。Testnet 预检、三日校准的事实和小时数均不计入本门禁，正式计时窗口与 `D_CAL_3D` 不重叠。
- 参数候选、配置、交易规则快照和故障计划均已固定并生成摘要；最终 release 与策略包在预热后绑定未来 `effective_at` 生成，计时窗口内不得改参数或任何绑定内容。
- Top 10 固定每 15 分钟刷新，候补 11–15 和持仓管理集完整订阅；新标的预热后才能交易。
- Prometheus/日志、age v1/X25519 远端 L2 归档、30 秒出站签名心跳和证据目录可用；时钟偏移 ≤50 ms。
- 已按 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)恢复并验签共享限额权威；两个 validation lane 都只能经同一 UDS 取得一次性 permit，业务 database/角色隔离不能生成新限额 scope。

## 启动命令示例

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export VALIDATION_ENV_PATH="<VALIDATION_ENV_PATH>"
export EXPECTED_ALEMBIC_HEAD="<EXPECTED_ALEMBIC_HEAD>"
export PREREGISTERED_PA_CONFIG_FILE="<PREREGISTERED_PA_CONFIG_FILE>"
export PA_SCHEMA_FILE="config/price-action.schema.json"
export PREREGISTERED_OF_SEARCH_PLAN="<PREREGISTERED_OF_SEARCH_PLAN>"
export TESTNET_PROTOCOL_PROBE_PLAN_FILE="<TESTNET_PROTOCOL_PROBE_PLAN_FILE>"
export HOST_RATE_STARTUP_EVIDENCE="<HOST_RATE_STARTUP_EVIDENCE>"
cd "$PROJECT_DIR"
DC=(docker compose -p aiq-validation -f deploy/compose.yaml --env-file "$VALIDATION_ENV_PATH")
"${DC[@]}" --profile dual-validation config --quiet
quantctl host-rate require-ready --evidence "$HOST_RATE_STARTUP_EVIDENCE" --max-age-seconds 300
quantctl contract validate \
  --schema contracts/testnet-protocol-probe-plan.schema.json \
  --instance "$TESTNET_PROTOCOL_PROBE_PLAN_FILE" \
  --verify-jcs-hash --verify-signature
export TESTNET_PROTOCOL_PROBE_PLAN_HASH="$(quantctl contract field --instance "$TESTNET_PROTOCOL_PROBE_PLAN_FILE" --pointer /plan_hash --raw)"
quantctl protocol-probe verify-plan \
  --environment testnet --plan "$TESTNET_PROTOCOL_PROBE_PLAN_FILE" \
  --require-plan-hash "$TESTNET_PROTOCOL_PROBE_PLAN_HASH" --require-testnet-market
quantctl contract validate --schema contracts/calibration-dataset-manifest.schema.json --instance "<CALIBRATION_DATASET_MANIFEST>" --verify-jcs-hash --verify-signature
export PA_SCHEMA_HASH="$(quantctl artifact sha256 --input "$PA_SCHEMA_FILE" --raw)"
export PA_CONFIG_HASH="$(quantctl config canonical-hash --input "$PREREGISTERED_PA_CONFIG_FILE" --format RFC8785_JCS --safe-yaml --raw)"
export OF_SEARCH_PLAN_HASH="$(quantctl contract field --instance "$PREREGISTERED_OF_SEARCH_PLAN" --pointer /plan_hash --raw)"
quantctl config validate --schema "$PA_SCHEMA_FILE" --instance "$PREREGISTERED_PA_CONFIG_FILE" --cross-field-rules price-action-v1 --deny-default-injection
quantctl strategy parameter-candidate-verify \
  --candidate "<C0_PARAMETER_CANDIDATE>" --calibration-manifest "<CALIBRATION_DATASET_MANIFEST>" \
  --search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --require-price-action-config-hash "$PA_CONFIG_HASH" \
  --require-price-action-schema-hash "$PA_SCHEMA_HASH" --deny-price-action-difference \
  --schema contracts/of-parameter-candidate.schema.json --verify-jcs-hash --verify-signature \
  --require-exact-scope-map --require-all-values-on-preregistered-grid \
  --require-short-less-than-medium-less-than-long --replay-candidate-selection
quantctl config validate-dual \
  --launcher "$VALIDATION_ENV_PATH" --deny-production-secrets --deny-shared-facts \
  --require-price-action-config-hash "$PA_CONFIG_HASH" \
  --require-identical-price-action-hash-across-lanes
quantctl access-matrix verify --compose deploy/compose.yaml --project aiq-validation --env-file "$VALIDATION_ENV_PATH" --allow-testnet-secret-only testnet-execution-adapter --deny-shadow-secret-access --deny-production-secrets --deny-whole-secret-directory-mount --redact
quantctl isolation verify-seal --source-project aiq-testnet --target-project aiq-validation --deny-shared-volumes --deny-shared-databases --evidence "<SEALED_TESTNET_EVIDENCE>"
quantctl isolation verify-seal --source-project aiq-calibration --target-project aiq-validation --deny-shared-volumes --deny-shared-databases --evidence "<SEALED_CALIBRATION_EVIDENCE>"
quantctl deployment capacity-gate --next-project aiq-validation --disk-capacity-bytes 200000000000 --max-active-allocation-bytes 170000000000 --min-free-bytes 30000000000 --include-retired-seals
quantctl deployment prepare-project --project aiq-validation --fresh-network --fresh-volumes --fresh-databases aiq_shadow,aiq_testnet --deny-source-project aiq-testnet,aiq-calibration --source-seals "<SEALED_TESTNET_EVIDENCE>,<SEALED_CALIBRATION_EVIDENCE>"
"${DC[@]}" --profile dual-validation up -d --wait postgres redis monitoring
for LANE in shadow testnet; do
  DB="aiq_${LANE}"
  "${DC[@]}" --profile dual-validation run --rm --no-deps app-migrations \
    alembic -x "lane=${LANE}" upgrade head
  quantctl database migration-verify \
    --project aiq-validation --database "$DB" \
    --expected-migration-role "${LANE}_migration_owner" \
    --expected-head "$EXPECTED_ALEMBIC_HEAD" --read-write --deny-cross-database
done
quantctl access-matrix verify-job \
  --project aiq-validation --service app-migrations \
  --require-lane-scoped-dsn --deny-cross-database --deny-runtime-secret-retention
"${DC[@]}" --profile dual-validation up -d --wait
quantctl access-matrix verify-runtime --project aiq-validation --allow-testnet-secret-only testnet-execution-adapter --deny-shadow-secret-access --deny-production-secrets --redact
quantctl environment assert --environment shadow --expected shadow --paper-only --market-source BINANCE_PRODUCTION_PUBLIC --project aiq-validation
quantctl environment assert --environment testnet --expected testnet --deny-production-endpoints --market-source BINANCE_TESTNET --project aiq-validation
quantctl isolation verify --project aiq-validation --environments shadow,testnet --deny-cross-database --deny-shared-order-prefix --deny-shared-market-sequence --deny-shared-facts
quantctl database assert-fresh --project aiq-validation --databases aiq_shadow,aiq_testnet --deny-import-from aiq-testnet,aiq-calibration
quantctl preflight run --gate shadow-72h --project aiq-validation --environments shadow,testnet
quantctl prewarm await --project aiq-validation --environments shadow,testnet --require-orderbook-ready --require-user-stream-ready testnet --require-archive-ready --require-monitoring-ready --output "<VALIDATION_PREWARM_EVIDENCE>"
quantctl strategy prewarm-verify \
  --evidence "<VALIDATION_PREWARM_EVIDENCE>" --price-action "$PREREGISTERED_PA_CONFIG_FILE" \
  --require-maximum-lookback-covered --require-config-hash "$PA_CONFIG_HASH"
```

`app-migrations` 是一次性任务；两个 database 任一迁移失败或 head 不符时，不得启动其余服务。本 Bash 会话后续任何 Compose `pull/up/exec/ps/restart/down` 都必须复用同一 `DC` 数组。`<VALIDATION_ENV_PATH>` 仅供 Compose 插值，不能整份注入容器；其键与隔离规则见 [环境变量契约](../config/environment-variables.md#3-dual-validation-launcher-契约)。

`testnet-protocol-probe-plan.schema.json` 是 closed Schema；每次启动新的 `aiq-validation` 都必须重新执行上述结构校验、RFC 8785 JCS `plan_hash` 重算和 Ed25519 签名验证，不能用独立 `aiq-testnet` 阶段的历史通过结果代替。任一校验失败，或 `verify-plan` 发现生产 endpoint/secret/价格、非 Testnet 规则输入或 hash 不一致，必须 fail-closed：停止两个 lane 的门禁计时、禁止启用探针订单并产生事故证据；修复后以新一轮 72 小时重新开始。

## 预热后生成最终 C0 并原子开始计时

预热证据生成后，在受信发布工作站选择一个至少留出 5 分钟签名余量的未来 UTC `effective_at`，并机械计算 `oos_end=effective_at+87d`。先生成最终不可变 CHAMPION `StrategyPackage`，再组装并签名最终 release，最后生成短时 `FREEZE_CHAMPION` 批准；顺序不可交换：

```bash
export EFFECTIVE_AT="<FUTURE_RFC3339_UTC>"
export OOS_END="<EFFECTIVE_AT_PLUS_EXACTLY_87_DAYS>"
export APPROVAL_EXPIRES_AT="<RFC3339_AFTER_EFFECTIVE_AT>"
quantctl time window-verify --start "$EFFECTIVE_AT" --end "$OOS_END" --exact-days 87 --require-future-seconds 300
quantctl time approval-window-verify --effective-at "$EFFECTIVE_AT" --expires-at "$APPROVAL_EXPIRES_AT" --require-effective-before-expiry
quantctl strategy package-create \
  --role CHAMPION --package-id "<C0_PACKAGE_ID>" \
  --parameter-candidate "<C0_PARAMETER_CANDIDATE>" \
  --price-action-config "$PREREGISTERED_PA_CONFIG_FILE" \
  --price-action-schema "$PA_SCHEMA_FILE" \
  --of-search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --require-price-action-config-hash "$PA_CONFIG_HASH" \
  --require-price-action-schema-hash "$PA_SCHEMA_HASH" \
  --require-of-search-plan-hash "$OF_SEARCH_PLAN_HASH" \
  --training-dataset "<CALIBRATION_DATASET_MANIFEST>" \
  --oos-start "$EFFECTIVE_AT" --oos-end "$OOS_END" --oos-purpose OOS_FORWARD_87D \
  --limitations "THREE_DAY_ALL_OF_ALPHA_OPTIMIZATION;NOT_STATISTICALLY_ROBUST" \
  --output "<C0_STRATEGY_PACKAGE>"
quantctl release assemble \
  --strategy-package "<C0_STRATEGY_PACKAGE>" \
  --parameter-candidate "<C0_PARAMETER_CANDIDATE>" \
  --price-action-config "$PREREGISTERED_PA_CONFIG_FILE" \
  --price-action-schema "$PA_SCHEMA_FILE" \
  --of-search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --calibration-manifest "<CALIBRATION_DATASET_MANIFEST>" \
  --prewarm-evidence "<VALIDATION_PREWARM_EVIDENCE>" \
  --bind config,schema,image,migration,tests,drills \
  --output "<UNSIGNED_RELEASE_MANIFEST>"
quantctl release sign --manifest "<UNSIGNED_RELEASE_MANIFEST>" --key "<RELEASE_SIGNING_KEY_PATH>" --output "<SIGNED_RELEASE_MANIFEST>"
quantctl strategy approval-challenge \
  --package "<C0_STRATEGY_PACKAGE>" --action FREEZE_CHAMPION \
  --effective-at "$EFFECTIVE_AT" --expires-at "$APPROVAL_EXPIRES_AT" \
  --release "<SIGNED_RELEASE_MANIFEST>" \
  --evidence "<VALIDATION_PREWARM_EVIDENCE>,<CALIBRATION_RUN_EVIDENCE>" \
  --output "<C0_APPROVAL_CHALLENGE>"
quantctl approval sign --challenge "<C0_APPROVAL_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<C0_STRATEGY_APPROVAL>"
quantctl strategy approval-verify \
  --package "<C0_STRATEGY_PACKAGE>" --approval "<C0_STRATEGY_APPROVAL>" \
  --require-action FREEZE_CHAMPION --require-effective-at "$EFFECTIVE_AT" \
  --require-issued-before-effective --require-effective-before-expiry
quantctl release binding-verify \
  --manifest "<SIGNED_RELEASE_MANIFEST>" --strategy-package "<C0_STRATEGY_PACKAGE>" \
  --calibration-dataset "<CALIBRATION_DATASET_MANIFEST>" \
  --prewarm-evidence "<VALIDATION_PREWARM_EVIDENCE>" --require-migration-head "$EXPECTED_ALEMBIC_HEAD" \
  --require-price-action-config-hash "$PA_CONFIG_HASH" \
  --require-price-action-schema-hash "$PA_SCHEMA_HASH" \
  --require-of-search-plan-hash "$OF_SEARCH_PLAN_HASH"
export RELEASE_ID="$(quantctl release field --manifest "<SIGNED_RELEASE_MANIFEST>" --pointer /release_id --raw)"
export RELEASE_MANIFEST_HASH="$(quantctl artifact sha256 --input "<SIGNED_RELEASE_MANIFEST>" --raw)"
export C0_PACKAGE_HASH="$(quantctl contract field --instance "<C0_STRATEGY_PACKAGE>" --pointer /package_hash --raw)"
quantctl runtime stage-release \
  --project aiq-validation --environments shadow,testnet \
  --manifest "<SIGNED_RELEASE_MANIFEST>" --strategy-package "<C0_STRATEGY_PACKAGE>" \
  --inactive-until "$EFFECTIVE_AT" --deny-early-activation
quantctl runtime binding-attest \
  --project aiq-validation --environments shadow,testnet \
  --release-id "$RELEASE_ID" --manifest-hash "$RELEASE_MANIFEST_HASH" \
  --package-hash "$C0_PACKAGE_HASH" --require-byte-identical --require-both-lanes \
  --require-price-action-config-hash "$PA_CONFIG_HASH" \
  --require-price-action-schema-hash "$PA_SCHEMA_HASH" \
  --output "<RUNTIME_BINDING_ATTESTATION>"
quantctl isolation verify-window-disjoint \
  --calibration-manifest "<CALIBRATION_DATASET_MANIFEST>" \
  --oos-start "$EFFECTIVE_AT" --exclude-prewarm
export CONFIRM_TOKEN="$(quantctl gate confirmation-token \
  --action ARM_C0 --release-id "$RELEASE_ID" --effective-at "$EFFECTIVE_AT" \
  --manifest-hash "$RELEASE_MANIFEST_HASH" --format safe-token --raw)"
printf '输入 %s 继续: ' "$CONFIRM_TOKEN"
read -r CONFIRM
test "$CONFIRM" = "$CONFIRM_TOKEN" || exit 1
quantctl gate arm-atomic \
  --name shadow-72h --project aiq-validation --release "<SIGNED_RELEASE_MANIFEST>" \
  --strategy-package "<C0_STRATEGY_PACKAGE>" --calibration-manifest "<CALIBRATION_DATASET_MANIFEST>" \
  --approval "<C0_STRATEGY_APPROVAL>" --effective-at "$EFFECTIVE_AT" \
  --runtime-attestation "<RUNTIME_BINDING_ATTESTATION>" \
  --activate-staged-release-on-both-lanes --require-byte-identical \
  --operator-confirmation "$CONFIRM" --release-manifest-hash "$RELEASE_MANIFEST_HASH" \
  --consume-approval-once --append-events CHAMPION_FROZEN,GATE_TIMER_STARTED \
  --transaction-isolation serializable --fail-if-health-changes --fail-if-missed
quantctl gate await-effective --name shadow-72h --effective-at "$EFFECTIVE_AT" --require-atomic-commit
quantctl gate status --name shadow-72h
```

`StrategyApproval.signed_payload.effective_at`、`StrategyPackage.content.oos_window.start`、`CHAMPION_FROZEN` 和 `GATE_TIMER_STARTED` 必须完全相等。若预热健康变化、签名/绑定失败或错过 `effective_at`，旧 package、release、challenge 和 approval 一律不可编辑或重用；选择新的未来时刻并重建、重签全部四项。禁止把预热开始、包创建或批准签发时间回填为 OOS 起点。

## 运行期操作

每班至少检查一次，命令均只读：

```bash
quantctl status --project aiq-validation --environment shadow
quantctl status --project aiq-validation --environment testnet
quantctl gate status --name shadow-72h --show-valid-hours
quantctl reconcile --environment testnet --read-only --summary
quantctl data-health --environment shadow --market-source BINANCE_PRODUCTION_PUBLIC --universe active,candidates,managed
quantctl data-health --environment testnet --market-source BINANCE_TESTNET --scope protocol-probes
quantctl archive status --show-oldest-unsynced --require-age-v1-x25519 --require-signed-receipt
quantctl heartbeat status --outbound-only --interval-seconds 30 --missing-intervals 3 --max-age-seconds 120
quantctl metrics check --profile shadow-72h-thresholds
```

窗口内以仿真或 Testnet 执行预注册演练，不在生产账户注入故障：

```bash
quantctl drill run --plan tests/drills/l2-sequence-gap.yaml --environment shadow
quantctl drill run --plan tests/drills/unknown-order-result.yaml --environment testnet
quantctl drill run --plan tests/drills/db-readonly.yaml --environment shadow
quantctl drill run --plan tests/drills/redis-loss.yaml --environment shadow
quantctl drill run --plan tests/drills/archive-unreachable.yaml --environment shadow
quantctl drill run --plan tests/drills/resource-and-clock.yaml --environment shadow
quantctl drill run --plan tests/drills/restart-with-open-state.yaml --environment testnet
quantctl drill run --plan tests/drills/telegram-replay.yaml --environment shadow
quantctl drill run --plan tests/drills/archive-age-and-receipt.yaml --environment shadow
quantctl drill run --plan tests/drills/heartbeat-loss-replay-forgery.yaml --environment shadow --missing-intervals 3
```

## 计时与中断规则

- 起点是 Shadow 的生产公开行情订单簿已重建/预热、Testnet 自身轻量行情/规则/用户流已健康、两个新 database 已证明没有旧事实或旧卷挂载，且在批准的 `effective_at` 由同一事务消费 `FREEZE_CHAMPION` 并写入 `CHAMPION_FROZEN` 与 `GATE_TIMER_STARTED`。这三个时刻和策略包 OOS 起点必须相同。
- P0/P1、重复订单、无保护持仓、未解释订单差异、计划外关键重启或关键观测缺失 >5 分钟，使本轮失败；修复后重新开始 72 小时。
- 不超过 5 分钟的观测缺口仍须补足等量健康时间，并证明交易事实未丢失。
- 参数、代码、Schema、风险或镜像摘要改变即产生新 release，本轮作废。
- 计划故障演练造成的 fail-closed 是预期行为，但若越过安全不变量则本轮失败。

## 验收：量化门槛

- 零未解决订单差异、零重复订单、零无原生保护持仓、零开放 P0/P1。
- 行情接收至特征 p99 ≤35 ms；特征至风险 p99 ≤25 ms；已持久化 `OrderIntent` 至执行 UDS 接收 p99 ≤5 ms。
- event-loop lag p99 ≤20 ms；风险决策与 `OrderIntent` 关键同步提交 p99 ≤10 ms、非关键批量事件持久化 p99 ≤2 秒；订单 `UNKNOWN` 首版时限 5 秒；首次 fill 到原生保护确认 ≤1,000 ms。
- CPU 5 分钟均值 <70%、p95 <85%；主机内存 <9 GiB；原始区不得达到 72 GB、文件系统不得达到 90%、可用磁盘不得低于 20 GB；原始区达到 60 GB、文件系统达到 85% 或可用低于 30 GB 必须告警；无 OOM/写失败。
- 时钟 ≤50 ms；L2 缺口 100% 检出，失效簿零次参与决策；小时片/远端日包校验 100% 通过。
- 所有强制演练产生预期 fail-closed、告警、审计和恢复证据。
- Shadow 与 Testnet 分别证明策略链和协议链；生产公开价格/过滤器/数量零次进入 Testnet，两个 lane 的交易事实交集为 0。
- 每个生产公开行情归档对象均以 age v1/X25519 加密，远端完成解密、明文 hash/Parquet 校验并返回有效 Ed25519 签名回执后才进入 `REMOTE_VERIFIED`；失败对象零删除。
- 心跳每 30 秒主动出站发送；连续 3 个间隔（约 90 秒）缺失即告警，超过 120 秒、重放或伪造包 100% 拒绝且不刷新 last-seen。

结束并生成候选门禁报告：

```bash
quantctl gate stop --name shadow-72h --require-valid-hours 72
quantctl reconcile --environment testnet --full --fail-on-difference
quantctl isolation verify --project aiq-validation --environments shadow,testnet --deny-shared-facts --deny-shared-market-sequence
quantctl isolation verify-seal --source-project aiq-testnet --target-project aiq-validation --deny-shared-volumes --deny-shared-databases --evidence "<SEALED_TESTNET_EVIDENCE>"
quantctl isolation verify-seal --source-project aiq-calibration --target-project aiq-validation --deny-shared-volumes --deny-shared-databases --evidence "<SEALED_CALIBRATION_EVIDENCE>"
quantctl evidence export --gate shadow-72h --output "<EVIDENCE_DIR>"
quantctl gate report --name shadow-72h --format signed-bundle --output "<REPORT_DIR>"
sha256sum "<REPORT_DIR>"/*
```

报告生成不等于批准。账户所有者必须独立复核原始指标、演练和摘要，再按前驱链依次追加 `APPROVE_SHADOW` 和 `APPROVE_TESTNET`；双 lane 是两个不同证明，不能用一份批准替代。每份批准的 `effective_at` 都是未来时刻，必须在有效窗内一次性追加：

```bash
export GATE_REPORT_HASH="$(quantctl evidence bundle-hash --input "<REPORT_DIR>" --raw)"
export SHADOW_APPROVAL_EFFECTIVE_AT="$(quantctl time future --lead-seconds 300 --format rfc3339 --raw)"
export SHADOW_APPROVAL_EXPIRES_AT="$(quantctl time add --at "$SHADOW_APPROVAL_EFFECTIVE_AT" --seconds 120 --format rfc3339 --raw)"
quantctl strategy approval-challenge \
  --package "<C0_STRATEGY_PACKAGE>" --action APPROVE_SHADOW \
  --resulting-stage SHADOW_APPROVED --target-environment shadow \
  --predecessor "<C0_STRATEGY_APPROVAL>" --release "<SIGNED_RELEASE_MANIFEST>" \
  --evidence-hash "$GATE_REPORT_HASH" \
  --effective-at "$SHADOW_APPROVAL_EFFECTIVE_AT" --expires-at "$SHADOW_APPROVAL_EXPIRES_AT" \
  --output "<SHADOW_APPROVAL_CHALLENGE>"
quantctl approval sign --challenge "<SHADOW_APPROVAL_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<SHADOW_STRATEGY_APPROVAL>"
quantctl strategy approval-append-at-effective \
  --approval "<SHADOW_STRATEGY_APPROVAL>" --package "<C0_STRATEGY_PACKAGE>" \
  --require-predecessor "<C0_STRATEGY_APPROVAL>" --consume-once

export TESTNET_APPROVAL_EFFECTIVE_AT="$(quantctl time future --lead-seconds 300 --format rfc3339 --raw)"
export TESTNET_APPROVAL_EXPIRES_AT="$(quantctl time add --at "$TESTNET_APPROVAL_EFFECTIVE_AT" --seconds 120 --format rfc3339 --raw)"
quantctl strategy approval-challenge \
  --package "<C0_STRATEGY_PACKAGE>" --action APPROVE_TESTNET \
  --resulting-stage TESTNET_APPROVED --target-environment testnet \
  --predecessor "<SHADOW_STRATEGY_APPROVAL>" --release "<SIGNED_RELEASE_MANIFEST>" \
  --evidence-hash "$GATE_REPORT_HASH" \
  --effective-at "$TESTNET_APPROVAL_EFFECTIVE_AT" --expires-at "$TESTNET_APPROVAL_EXPIRES_AT" \
  --output "<TESTNET_APPROVAL_CHALLENGE>"
quantctl approval sign --challenge "<TESTNET_APPROVAL_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<TESTNET_STRATEGY_APPROVAL>"
quantctl strategy approval-append-at-effective \
  --approval "<TESTNET_STRATEGY_APPROVAL>" --package "<C0_STRATEGY_PACKAGE>" \
  --require-predecessor "<SHADOW_STRATEGY_APPROVAL>" --consume-once
quantctl strategy lifecycle-chain-verify \
  --package "<C0_STRATEGY_PACKAGE>" \
  --approvals "<C0_STRATEGY_APPROVAL>,<SHADOW_STRATEGY_APPROVAL>,<TESTNET_STRATEGY_APPROVAL>" \
  --require-stages CHAMPION_FROZEN,SHADOW_APPROVED,TESTNET_APPROVED \
  --require-release "<SIGNED_RELEASE_MANIFEST>" --require-evidence-hash "$GATE_REPORT_HASH"
```

在停止 validation 前还必须预登记唯一允许的 OOS 计划缺口。首版只允许一次 `PLANNED_VALIDATION_TO_LIVE_CUTOVER`，最长 14,400 秒；该墙钟时间计入 87 日窗口但记为零暴露，缺失对象必须进入 Day 90 报告，禁止回填或静默排除：

```bash
quantctl oos cutover-plan-create \
  --window-id "<D_OOS_87D_ID>" --reason PLANNED_VALIDATION_TO_LIVE_CUTOVER \
  --max-duration-seconds 14400 --single-use --count-as-zero-exposure \
  --record-missing-objects --deny-backfill --output "<CUTOVER_PLAN>"
quantctl oos cutover-plan-challenge --plan "<CUTOVER_PLAN>" --expires-in 300 --output "<CUTOVER_PLAN_CHALLENGE>"
quantctl approval sign --challenge "<CUTOVER_PLAN_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<CUTOVER_PLAN_APPROVAL>"
quantctl oos cutover-plan-approve --plan "<CUTOVER_PLAN>" --approval "<CUTOVER_PLAN_APPROVAL>"
```

任一批准前驱 ID/hash、package/release/evidence hash、签名、nonce 或有效窗不符都阻断切换。`ARM_EXPERIMENTAL_LIVE` 生命周期批准不能在这里预签：它必须等 `aiq-live` 已以 `RISK_LOCKED` 启动并完成运行时字节绑定后再生成。

## 停止与升级条件

任一量化门槛失败、归档未确认却发生删除、原始区 ≥72 GB、文件系统 ≥90%、可用磁盘 <20 GB、数据库不可写、时钟 >100 ms 或合规状态变化时，停止新仓、标记本轮失败并升级对应事故。业务库不可写即进入 `RISK_LOCKED`，出口网关阻断全部新的 Binance REST、WS API 与 market-stream control 请求；V1 没有本地应急日志或延后回填例外，只依赖已确认的交易所原生保护并升级 P0 官方控制面处置。修复后不得从旧计时点续跑。

## 证据留存

保存开始/结束 UTC、有效小时、`aiq-testnet`/`aiq-calibration` 停止/封存/不复用证明、`CalibrationDatasetManifest`/C0 批准与窗口零交集证明、`aiq-validation` 新 database/卷目录、两个行情源与规则快照指纹、交易事实零交集证明、release/config/策略摘要、全量门禁时序指标、Top 10 快照、L2 manifest、age recipient 与回执验签公钥指纹、远端解密回执、30 秒心跳断流/过期/重放/伪造演练、订单对账、资源曲线、告警送达、事故与签名。至少分别包含一条 Shadow 正常策略链、一条 Testnet 自有行情协议链、一个未知订单错误流和一个持仓移出池加归档失败的边界流，按 [验收证据模板](../docs/09_TESTING_AND_ACCEPTANCE.md#12-验收证据模板)保存。
