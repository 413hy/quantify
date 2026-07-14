# 02A 三日 L2 校准采集与参数候选手册

## 目的

在 `aiq-testnet` 预检完成后，以阶段互斥项目 `aiq-calibration` 从 Binance **生产公开行情**采集首个预登记、连续合格的 72 小时 L2 数据集 `D_CAL_3D`，远端验证并封存，再由独立回测机优化用户允许的全部 Order Flow 参数并生成参数候选。最终 CHAMPION `StrategyPackage`、release 与 `FREEZE_CHAMPION` 批准必须等全新 `aiq-validation` 迁移和预热健康后，绑定未来精确 `effective_at` 才生成。本阶段不连接生产账户、不持有任何 Binance API key、不产生 `OrderIntent`，也不计入固定 C0 的正式 72 小时工程门禁。

说明：`aiq-calibration` 使用已有 `APP_ENV=shadow`、canonical RuntimeState `SHADOW`，另以 `project_purpose=CALIBRATION_3D` 区分；它不是新的公共 Environment 或 RuntimeState。文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约。

## 前置条件与人工门禁

- [02 Testnet](02_TESTNET.md) 已通过，`aiq-testnet` 零仓位/零挂单、签名封存、停止并按 [07 B2](07_DISK_ARCHIVE_INCIDENT.md#b2-阶段互斥项目的退役容量回收)完成容量门禁；其卷、database 和事实不会挂入本项目。
- collector release、Git commit、镜像 digest、配置、Schema、Universe 和数据质量规则已签名冻结；本阶段不因观察收益改动 collector。
- `CALIBRATION_ENV_PATH` 由 [模板](../config/calibration.env.example)生成并通过校验；Compose 渲染结果不存在 execution-service、生产交易或 Testnet endpoint、Binance key/secret 路径和写控制能力；仅允许 Binance 生产**公开行情**端点。
- 独立回测机、age/X25519 加密 SFTP 接收端、Ed25519 回执验证和 90 天容量可用。
- 在查看本窗口策略收益前，已预登记 dataset ID、最早允许起点、`T0` 解析规则、恰好 72 小时窗口、数据质量规则、Order Flow 搜索空间、目标函数、成本模型和停止条件。
- 账户所有者已审阅 [PA v1 基线](../config/price-action.example.yaml)，其规范 config hash、[closed Schema](../config/price-action.schema.json) 文件 hash 和 detached signature 均在 OF 搜索计划与 dataset plan 中绑定；不接受该工程基线就必须在 `T0` 前给出另一份合法明确值，禁止 Codex 猜测。
- 已先执行 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)，恢复跨项目共享 counter/fencing/429/418 并取得 `HOST_RATE_STARTUP_EVIDENCE`；校准项目无权启动、停止或清空 `aiq-host-control`。

## 1. 预检并创建全新项目

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export CALIBRATION_ENV_PATH="<CALIBRATION_ENV_PATH>"
export NETWORK_EGRESS_POLICY_FILE="<CALIBRATION_NETWORK_EGRESS_POLICY_FILE>"
export CALIBRATION_DATASET_PLAN_FILE="<CALIBRATION_DATASET_PLAN_FILE>"
export CALIBRATION_DATASET_ID="<CALIBRATION_DATASET_ID>"
export CALIBRATION_DATA_QUALITY_PROFILE_FILE="<CALIBRATION_DATA_QUALITY_PROFILE_FILE>"
export PREREGISTERED_OF_SEARCH_PLAN="<PREREGISTERED_OF_SEARCH_PLAN>"
export PREREGISTERED_PA_CONFIG_FILE="<PREREGISTERED_PA_CONFIG_FILE>"
export PREREGISTERED_PA_CONFIG_SIGNATURE="<PREREGISTERED_PA_CONFIG_SIGNATURE>"
export PA_SCHEMA_FILE="config/price-action.schema.json"
export EXPECTED_ALEMBIC_HEAD="<EXPECTED_ALEMBIC_HEAD>"
export HOST_RATE_STARTUP_EVIDENCE="<HOST_RATE_STARTUP_EVIDENCE>"
cd "$PROJECT_DIR"
DC=(docker compose -p aiq-calibration -f deploy/compose.yaml --env-file "$CALIBRATION_ENV_PATH")
"${DC[@]}" --profile calibration config --quiet
quantctl release verify --manifest "<SIGNED_COLLECTOR_RELEASE_MANIFEST>"
quantctl contract validate \
  --schema contracts/calibration-dataset-plan.schema.json \
  --instance "$CALIBRATION_DATASET_PLAN_FILE" \
  --verify-jcs-hash --verify-signature --verify-registration-signature \
  --require-dataset-id "$CALIBRATION_DATASET_ID" --require-registration-before-t0
export DATASET_PLAN_HASH="$(quantctl contract field --instance "$CALIBRATION_DATASET_PLAN_FILE" --pointer /plan_hash --raw)"
export DATASET_REGISTRATION_PAYLOAD_HASH="$(quantctl contract field --instance "$CALIBRATION_DATASET_PLAN_FILE" --pointer /registration/payload_hash --raw)"
quantctl config validate \
  --schema "$PA_SCHEMA_FILE" --instance "$PREREGISTERED_PA_CONFIG_FILE" \
  --require-baseline-origin UNVALIDATED_ENGINEERING_BASELINE \
  --cross-field-rules price-action-v1 --deny-default-injection
export PA_SCHEMA_HASH="$(quantctl artifact sha256 --input "$PA_SCHEMA_FILE" --raw)"
export PA_CONFIG_HASH="$(quantctl config canonical-hash --input "$PREREGISTERED_PA_CONFIG_FILE" --format RFC8785_JCS --safe-yaml --raw)"
quantctl config detached-signature-verify \
  --config "$PREREGISTERED_PA_CONFIG_FILE" --schema "$PA_SCHEMA_FILE" \
  --signature "$PREREGISTERED_PA_CONFIG_SIGNATURE" \
  --config-hash "$PA_CONFIG_HASH" --schema-hash "$PA_SCHEMA_HASH" \
  --require-signed-before "<DATASET_PLAN_PREREGISTERED_AT>" --output "<PA_FREEZE_EVIDENCE>"
quantctl calibration plan-verify \
  --plan "$CALIBRATION_DATASET_PLAN_FILE" \
  --dataset-id "$CALIBRATION_DATASET_ID" \
  --of-search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --price-action-config-hash "$PA_CONFIG_HASH" \
  --price-action-schema-hash "$PA_SCHEMA_HASH" \
  --require-preregistered \
  --require-search-plan-hash-bound \
  --require-search-plan-signed-before-preregistered-at \
  --require-preregistered-at-before-t0 \
  --require-window-hours 72 \
  --require-first-qualified-window \
  --quality-profile "$CALIBRATION_DATA_QUALITY_PROFILE_FILE" \
  --deny-performance-based-selection
quantctl config validate \
  --schema config/calibration-data-quality.schema.json \
  --instance "$CALIBRATION_DATA_QUALITY_PROFILE_FILE" \
  --require-status PREREGISTERED_ENGINEERING_GATE --verify-signature
quantctl contract validate \
  --schema contracts/of-calibration-search-plan.schema.json \
  --instance "$PREREGISTERED_OF_SEARCH_PLAN" \
  --verify-jcs-hash --verify-signature --require-complete-parameter-set of-alpha-v1 \
  --require-field "/content/price_action_config_hash=$PA_CONFIG_HASH" \
  --require-field "/content/price_action_schema_hash=$PA_SCHEMA_HASH" \
  --require-signature-before "<DATASET_PLAN_PREREGISTERED_AT>"
quantctl config validate-calibration \
  --launcher "$CALIBRATION_ENV_PATH" \
  --require-purpose CALIBRATION_3D \
  --require-app-env shadow \
  --require-runtime-state SHADOW \
  --require-market-source BINANCE_PRODUCTION_PUBLIC \
  --deny-all-binance-secrets --deny-execution --deny-order-intents --deny-shared-facts
quantctl compose service-set-verify \
  --project aiq-calibration \
  --allow-only realtime-engine,persistence-worker,archive-service,monitoring,postgres,redis,control-readonly \
  --deny execution-service,testnet-execution-adapter,production-execution-adapter
quantctl access-matrix verify \
  --compose deploy/compose.yaml --project aiq-calibration --env-file "$CALIBRATION_ENV_PATH" \
  --deny-all-binance-secrets --deny-whole-secret-directory-mount --redact
quantctl network egress-verify \
  --policy "$NETWORK_EGRESS_POLICY_FILE" --environment shadow --project-purpose CALIBRATION_3D \
  --phase runtime --app-and-host --deny-unlisted
quantctl isolation verify-seal \
  --source-project aiq-testnet --target-project aiq-calibration \
  --deny-shared-volumes --deny-shared-databases --evidence "<SEALED_TESTNET_EVIDENCE>"
quantctl deployment capacity-gate \
  --next-project aiq-calibration --disk-capacity-bytes 200000000000 \
  --max-active-allocation-bytes 170000000000 --min-free-bytes 30000000000 --include-retired-seals
quantctl deployment prepare-project \
  --project aiq-calibration --fresh-network --fresh-volumes --fresh-database \
  --deny-source-project aiq-testnet --source-seal "<SEALED_TESTNET_EVIDENCE>"
quantctl host-rate require-ready --evidence "$HOST_RATE_STARTUP_EVIDENCE" --max-age-seconds 300
"${DC[@]}" --profile calibration up -d --wait postgres redis monitoring
"${DC[@]}" --profile calibration run --rm --no-deps app-migrations alembic upgrade head
quantctl database migration-verify \
  --project aiq-calibration --database aiq_calibration \
  --expected-head "$EXPECTED_ALEMBIC_HEAD" --read-write
"${DC[@]}" --profile calibration up -d --wait
quantctl access-matrix verify-runtime --project aiq-calibration --deny-all-binance-secrets --redact
quantctl event assert-disabled --project aiq-calibration --event-types SignalCandidate,RiskDecision,OrderIntent,OrderEvent
```

任一预检失败即停止。`app-migrations` 是一次性任务；迁移退出非零或 head 不符时不得启动其余服务。本 Bash 会话后续所有 Compose 命令必须复用同一个 `DC`，不得更换 project/file/`--env-file`。

## 2. 解析 T0 并连续采集 72 小时

只有全市场轻量流、Top 10/候补完整 L2、订单簿序列、时钟、归档、远端回执和资源全部健康后，才按预登记规则把首个合格时刻原子写成 `T0`；`T1=T0+72h` 为 exclusive end：

```bash
quantctl calibration start \
  --project aiq-calibration \
  --plan "$CALIBRATION_DATASET_PLAN_FILE" \
  --dataset-id "$CALIBRATION_DATASET_ID" \
  --resolve-t0 first-continuous-qualified-instant \
  --duration-hours 72 \
  --output "<CALIBRATION_START_EVIDENCE>"
quantctl calibration status --dataset-id "$CALIBRATION_DATASET_ID" --show-t0-t1 --show-valid-hours
```

每班至少执行一次只读检查：

```bash
quantctl data-health --project aiq-calibration --environment shadow --market-source BINANCE_PRODUCTION_PUBLIC --universe all-ranked,top10,standby
quantctl orderbook continuity --project aiq-calibration --all-subscribed
quantctl archive status --project aiq-calibration --show-oldest-unsynced --require-age-v1-x25519 --require-signed-receipt
quantctl metrics check --profile "$CALIBRATION_DATA_QUALITY_PROFILE_FILE"
quantctl event assert-zero --project aiq-calibration --event-types OrderIntent,OrderEvent
quantctl calibration status --dataset-id "$CALIBRATION_DATASET_ID" --show-valid-hours
```

任何会破坏确定性回放的 L2 缺口、时钟越界、清单损坏、collector/config/schema 变化、未验证远端对象或计划外关键观测缺失，都把本 dataset 标记为 `FAILED`；失败记录和原数据不能删除或改写。修复后必须生成、签署并登记一份**新** `CalibrationDatasetPlan`，使用新 plan ID、新 dataset ID 和不早于失败窗口之后的 `earliest_t0`；OF/PA/quality/collector 绑定不得放宽或暗改。`select_next_first_qualified_window` 表示在这份新 plan 下继续同一机械选择，不能复用绑定旧 dataset ID 的旧 plan，也不能挑选收益较好的日期。普通告警是否破坏窗口由预登记数据质量规则机械裁决，不能事后放宽。

## 3. 完成远端验证、停止并封存项目

达到 `T1` 后立即停止向该 dataset 追加数据，完成所有小时片和日清单并逐对象远端验证。封存顺序不可交换：关闭数据集 → 质量裁决 → 冻结写入并排空 → 最终备份及远端验证 → 停止项目 → 生成 project seal → 签名数据清单 → 隔离恢复。

```bash
quantctl calibration close --dataset-id "$CALIBRATION_DATASET_ID" --at-preregistered-t1 --no-extension
quantctl archive finalize --project aiq-calibration --dataset-id "$CALIBRATION_DATASET_ID"
quantctl archive verify \
  --dataset-id "$CALIBRATION_DATASET_ID" --scope all-objects \
  --require-age-v1-x25519 --require-remote-decrypt --require-plaintext-hash \
  --require-parquet --require-signed-receipt --reject-replay
quantctl calibration quality-evaluate \
  --dataset-id "$CALIBRATION_DATASET_ID" \
  --rules "$CALIBRATION_DATA_QUALITY_PROFILE_FILE" --rules-hash "<DATA_QUALITY_RULES_SHA256>" \
  --output "<CALIBRATION_QUALITY_REPORT>"
quantctl evidence export --project aiq-calibration --dataset-id "$CALIBRATION_DATASET_ID" --output "<CALIBRATION_EVIDENCE_DIR>"
quantctl persistence freeze-writers --project aiq-calibration --reason calibration-t1-reached
quantctl persistence drain --project aiq-calibration --require-zero-inflight --require-zero-unflushed
quantctl event assert-zero --project aiq-calibration --event-types OrderIntent,OrderEvent
quantctl backup create \
  --project aiq-calibration --scope database,catalog,wal,configuration \
  --encrypt age-x25519 --remote-required --output "<CALIBRATION_FINAL_BACKUP>"
quantctl backup remote-verify \
  --backup "<CALIBRATION_FINAL_BACKUP>" --require-decrypt --require-plaintext-hash \
  --require-signed-receipt --reject-replay --output "<CALIBRATION_BACKUP_REMOTE_EVIDENCE>"
"${DC[@]}" --profile calibration down --remove-orphans
quantctl deployment assert-project-stopped \
  --project aiq-calibration --assert-no-containers --assert-no-published-ports \
  --assert-no-attached-networks --allow-sealed-volumes
quantctl evidence seal-project \
  --project aiq-calibration \
  --include database-catalog,volume-inventory,compose-context,data-roots,remote-receipt-root,quality-report,final-backup \
  --require-project-stopped --deny-reuse-by aiq-validation \
  --output "<SEALED_CALIBRATION_EVIDENCE>"
quantctl calibration manifest-create \
  --schema contracts/calibration-dataset-manifest.schema.json \
  --dataset-id "$CALIBRATION_DATASET_ID" \
  --dataset-plan "$CALIBRATION_DATASET_PLAN_FILE" \
  --dataset-plan-hash "$DATASET_PLAN_HASH" \
  --registration-payload-hash "$DATASET_REGISTRATION_PAYLOAD_HASH" \
  --of-search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --price-action-config-hash "$PA_CONFIG_HASH" \
  --price-action-schema-hash "$PA_SCHEMA_HASH" \
  --quality-profile "$CALIBRATION_DATA_QUALITY_PROFILE_FILE" \
  --require-all-preregistration-signatures-before-t0 \
  --project-seal "<SEALED_CALIBRATION_EVIDENCE>" \
  --quality-report "<CALIBRATION_QUALITY_REPORT>" \
  --unsigned-payload-output "<CALIBRATION_MANIFEST_SIGNED_PAYLOAD>"
quantctl artifact jcs-hash \
  --input "<CALIBRATION_MANIFEST_SIGNED_PAYLOAD>" \
  --output "<CALIBRATION_MANIFEST_HASH>"
quantctl calibration manifest-challenge \
  --signed-payload "<CALIBRATION_MANIFEST_SIGNED_PAYLOAD>" \
  --manifest-hash "<CALIBRATION_MANIFEST_HASH>" \
  --output "<CALIBRATION_MANIFEST_CHALLENGE>"
quantctl approval sign-inline \
  --challenge "<CALIBRATION_MANIFEST_CHALLENGE>" \
  --key "<NON_TRADING_ED25519_SIGNING_KEY_PATH>" \
  --output "<CALIBRATION_MANIFEST_SIGNATURE>"
quantctl calibration manifest-finalize \
  --signed-payload "<CALIBRATION_MANIFEST_SIGNED_PAYLOAD>" \
  --manifest-hash "<CALIBRATION_MANIFEST_HASH>" \
  --signature "<CALIBRATION_MANIFEST_SIGNATURE>" \
  --output "<CALIBRATION_DATASET_MANIFEST>"
quantctl contract validate \
  --schema contracts/calibration-dataset-manifest.schema.json \
  --instance "<CALIBRATION_DATASET_MANIFEST>" \
  --verify-jcs-hash --verify-signature \
  --require-json-pointer /signed_payload/data_quality/status=QUALIFIED
quantctl backup verify \
  --project aiq-calibration --backup "<CALIBRATION_FINAL_BACKUP>" \
  --restore-target "<ISOLATED_CALIBRATION_RESTORE_PATH>" \
  --output "<CALIBRATION_RESTORE_EVIDENCE>"
```

manifest 签名键是非交易审批键，不得存放在生产 VPS。`down` 不带 `-v`。随后按 [07 B2](07_DISK_ARCHIVE_INCIDENT.md#b2-阶段互斥项目的退役容量回收)执行受控退役，固定 `RETIRED_PROJECT=aiq-calibration`、`NEXT_PROJECT=aiq-validation`、`RETIRED_PROJECT_SEAL=<SEALED_CALIBRATION_EVIDENCE>`、`RESTORE_EVIDENCE=<CALIBRATION_RESTORE_EVIDENCE>`。任何唯一副本、失败恢复或容量门禁失败都阻断下一阶段并要求扩容。

## 4. 独立回测机优化并生成参数候选

以下步骤只在独立回测/研究机执行。研究机从远端已验证对象重建 `D_CAL_3D`，验证 manifest/hash/签名后，严格执行预登记搜索空间；不得改变风险硬上限、PA 方向、PA/OF 冲突不交易或数据质量规则：

```bash
quantctl research dataset-import \
  --manifest "<CALIBRATION_DATASET_MANIFEST>" \
  --require-purpose CALIBRATION_3D --require-source-project aiq-calibration \
  --require-remote-verified --read-only \
  --verify-preregistration-chain \
  --dataset-plan "$CALIBRATION_DATASET_PLAN_FILE" \
  --of-search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --price-action-config "$PREREGISTERED_PA_CONFIG_FILE"
quantctl research calibrate-order-flow \
  --dataset "<CALIBRATION_DATASET_MANIFEST>" \
  --search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --frozen-price-action "$PREREGISTERED_PA_CONFIG_FILE" \
  --require-price-action-config-hash "$PA_CONFIG_HASH" \
  --require-price-action-schema-hash "$PA_SCHEMA_HASH" \
  --deny-price-action-search \
  --output "<CALIBRATION_RUN_EVIDENCE>"
quantctl strategy parameter-candidate-create \
  --schema contracts/of-parameter-candidate.schema.json \
  --training-dataset "<CALIBRATION_DATASET_MANIFEST>" \
  --calibration-evidence "<CALIBRATION_RUN_EVIDENCE>" \
  --price-action-config "$PREREGISTERED_PA_CONFIG_FILE" \
  --price-action-config-hash "$PA_CONFIG_HASH" \
  --price-action-schema-hash "$PA_SCHEMA_HASH" \
  --of-search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --limitations "THREE_DAY_ALL_OF_ALPHA_OPTIMIZATION;NOT_STATISTICALLY_ROBUST" \
  --output "<C0_PARAMETER_CANDIDATE>"
quantctl contract validate \
  --schema contracts/of-parameter-candidate.schema.json \
  --instance "<C0_PARAMETER_CANDIDATE>" --verify-jcs-hash --verify-signature
quantctl strategy parameter-candidate-verify \
  --candidate "<C0_PARAMETER_CANDIDATE>" \
  --search-plan "$PREREGISTERED_OF_SEARCH_PLAN" \
  --price-action-config "$PREREGISTERED_PA_CONFIG_FILE" \
  --require-price-action-config-hash "$PA_CONFIG_HASH" \
  --require-price-action-schema-hash "$PA_SCHEMA_HASH" \
  --deny-price-action-difference \
  --require-exact-scope-map --require-all-values-on-preregistered-grid \
  --require-short-less-than-medium-less-than-long \
  --require-trial-ledger-complete --replay-candidate-selection \
  --require-optimizer-byte-identity \
  --calibration-manifest "<CALIBRATION_DATASET_MANIFEST>"
```

只有 `signed_payload.data_quality.status=QUALIFIED`、全部远端回执有效、参数候选及研究证据 hash 有效且高过拟合限制显著显示时，才能进入 [03 固定 C0 的 72 小时双验证](03_SHADOW_72H.md)。此时尚未创建最终 CHAMPION `StrategyPackage`、最终 release 或 `FREEZE_CHAMPION` 批准；它们必须在 `aiq-validation` 完成迁移和预热后，绑定未来 `effective_at` 原子生成和消费。`D_OOS_87D` 从该生效时刻开始；本三日窗口本身不计作样本外或正式工程门禁。

## 验收

- `D_CAL_3D` 是预登记规则选出的首个连续合格 72 小时窗口，`T1-T0` 精确 72 小时；失败窗口和原因仍可追溯。
- `aiq-calibration` 全程零 Binance key/secret、零 execution-service、零 `OrderIntent`、零订单/成交，只有生产公开行情读和归档写。
- 全市场轻量数据、当时 Universe 快照、Top 10/候补完整 L2、质量事实和 collector 摘要均进入数据根；所有对象达到 `REMOTE_VERIFIED`。
- `aiq-testnet`、`aiq-calibration`、后续 `aiq-validation` 的 database、卷、队列、Redis、事实和计时窗口交集为零。
- 参数候选只由该 manifest 和预登记 OF 搜索产生；候选逐字绑定预先冻结且未参与搜索的 PA config/schema hash，证据中显著标注高过拟合；尚未创建或签名冻结 C0，也未宣称盈利或统计稳健。

## 证据留存

保留预登记 plan、PA YAML/Schema 的 config/schema hash 与 detached signature、T0/T1 解析、collector/release/config/image/schema hash、全市场/Universe/L2/日清单根、质量报告、失败 dataset、逐对象远端回执根、最终备份、project seal、manifest JCS/hash/签名、隔离恢复、B2 退役与 capacity-gate、研究运行清单和参数候选。最终 C0/release/批准证据由 03 手册在预热后生成。所有证据生成 SHA-256 并同步独立审计端。
