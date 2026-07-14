# 02 Binance Testnet 验证运行手册

## 目的

在与生产完全隔离的 Binance Testnet 环境，用 **Testnet 自身**的轻量 book、mark price、`exchangeInfo` 和用户数据流验证账户模式、交易规则、订单状态机、保护单、对账和故障处理。禁止把 Binance 生产公开行情的价格、过滤器或据此计算的下单数量送往 Testnet。Testnet 成交不证明真实流动性或盈利能力。

说明：文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约；实现前仅作为开发和验收接口定义，不能用临时签名脚本替代。

## 前置条件与人工门禁

- [01 初始化](01_INITIALIZE.md)全部通过，当前 release 和配置摘要已签名。
- 仅挂载 Testnet 专用 key；配置明确 `environment=testnet`、生产交易端点拒绝访问、订单前缀为测试命名空间。
- Testnet 账户预期为单向持仓、全仓保证金；任何配置动作只作用于 Testnet。
- PostgreSQL、卷、审计和通知使用 Testnet 隔离实例；风险状态从 `RISK_LOCKED` 开始。
- 测试人员已准备最小测试资金和可回收的合约列表，不在测试命令中填写 secret。
- 本手册运行的是独立预检项目 `aiq-testnet`，其小时数和交易事实不计入后续 72 小时门禁；进入下一阶段 `aiq-calibration` 前必须按本手册末尾封存并禁止复用其卷。
- 已先完整执行 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)，并导出未过期的 `HOST_RATE_STARTUP_EVIDENCE`；此前不得调用 account、`exchangeInfo`、listenKey 或任何 Testnet endpoint。

## 安全命令示例

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export TESTNET_ENV_PATH="<TESTNET_ENV_PATH>"
export TESTNET_PROTOCOL_PROBE_PLAN_FILE="<TESTNET_PROTOCOL_PROBE_PLAN_FILE>"
export HOST_RATE_STARTUP_EVIDENCE="<HOST_RATE_STARTUP_EVIDENCE>"
cd "$PROJECT_DIR"
DC=(docker compose -p aiq-testnet -f deploy/compose.yaml --env-file "$TESTNET_ENV_PATH")
"${DC[@]}" --profile testnet config --quiet
quantctl release verify --manifest "<SIGNED_RELEASE_MANIFEST>"
quantctl host-rate require-ready --evidence "$HOST_RATE_STARTUP_EVIDENCE" --max-age-seconds 300
quantctl environment assert --expected testnet --deny-production-endpoints --market-source BINANCE_TESTNET --project aiq-testnet
quantctl secrets inspect-metadata --service testnet-probe-runner --expected-scope testnet
quantctl access-matrix verify --compose deploy/compose.yaml --project aiq-testnet --env-file "$TESTNET_ENV_PATH" --allow-binance-secret-only testnet-probe-runner --deny-service-secret execution-service --deny-production-secrets --deny-whole-secret-directory-mount --redact
quantctl contract validate \
  --schema contracts/testnet-protocol-probe-plan.schema.json \
  --instance "$TESTNET_PROTOCOL_PROBE_PLAN_FILE" \
  --verify-jcs-hash --verify-signature
export TESTNET_PROTOCOL_PROBE_PLAN_HASH="$(quantctl contract field --instance "$TESTNET_PROTOCOL_PROBE_PLAN_FILE" --pointer /plan_hash --raw)"
quantctl protocol-probe verify-plan \
  --environment testnet --plan "$TESTNET_PROTOCOL_PROBE_PLAN_FILE" \
  --require-plan-hash "$TESTNET_PROTOCOL_PROBE_PLAN_HASH" --require-testnet-market
quantctl exchange verify-account --expected-position-mode one-way --expected-margin-mode cross --read-only
quantctl exchange refresh-rules --source exchangeInfo --environment testnet
"${DC[@]}" --profile testnet up -d
quantctl access-matrix verify-runtime --project aiq-testnet --allow-binance-secret-only testnet-probe-runner --deny-service-secret execution-service --deny-production-secrets --redact
```

本 Bash 会话后续如需执行 Compose `pull/up/exec/ps/restart/down`，必须复用同一个 `DC` 数组；不得省略 project、Compose 文件或 `--env-file` 后重新拼命令。

`testnet-protocol-probe-plan.schema.json` 是 closed Schema；上述 `contract validate` 必须拒绝未知字段，按 RFC 8785 重算并逐字比较 `plan_hash = SHA-256(JCS(content))`，再用受信 Ed25519 公钥验证 `signature`。Schema、重算 hash、签名或随后 Testnet-only 语义检查任一失败都必须以非零码终止；不得启动 Testnet、不得沿用旧探针报告，也不得把该失败降级为告警。

只读核验全部通过后，由人工输入绑定 release 的确认短语启用 Testnet：

```bash
read -r -p "输入 ARM-TESTNET-<RELEASE_ID> 继续: " CONFIRM
test "$CONFIRM" = "ARM-TESTNET-<RELEASE_ID>" || exit 1
quantctl testnet arm --release "<RELEASE_ID>" --approval "<APPROVAL_FILE>"
quantctl status --environment testnet
```

执行预注册测试计划。订单的 symbol、价格、过滤器和数量只能由同一 Testnet 行情/规则快照推导，数量由风险计算器生成并受 Testnet 上限约束；禁止在 shell 直接拼接签名请求：

```bash
quantctl test run --plan tests/plans/testnet-order-lifecycle.yaml --environment testnet
quantctl test run --plan tests/plans/testnet-partial-fill-cancel-race.yaml --environment testnet
quantctl test run --plan tests/plans/testnet-unknown-result-reconcile.yaml --environment testnet
quantctl test run --plan tests/plans/testnet-user-stream-rotation.yaml --environment testnet
quantctl test run --plan tests/plans/testnet-native-protection.yaml --environment testnet
quantctl reconcile --environment testnet --full --fail-on-difference
```

结束时停止新增测试订单、取消非保护挂单并确认余额/持仓：

```bash
quantctl pause-new-entries --environment testnet --reason "testnet plan complete"
quantctl cancel-pending --environment testnet --exclude-protective
quantctl reconcile --environment testnet --full --fail-on-difference
quantctl positions list --environment testnet --assert-flat
quantctl protection cleanup-stale --environment testnet --require-flat
quantctl orders list --environment testnet --status open --assert-empty
```

若仍有测试持仓，按 [05 暂停/撤单/平仓](05_PAUSE_CANCEL_FLATTEN.md)的二次确认流程处理，禁止直接绕过本地账本。

确认交易所与本地均为零持仓、零挂单后，封存独立项目并停止它；`down` 故意不带 `-v`，防止未经核验整卷删除。这些卷不得被 `aiq-calibration` 或任何后续项目挂载，且在创建下一项目之前必须完成远端恢复与本地容量门禁：

```bash
quantctl testnet assert-flat --project aiq-testnet --fail-on-open-order
quantctl persistence freeze-writers --project aiq-testnet --reason testnet-plan-complete
quantctl persistence drain --project aiq-testnet --require-zero-inflight --require-zero-unflushed
quantctl evidence export --project aiq-testnet --environment testnet --output "<EVIDENCE_DIR>"
quantctl backup create \
  --project aiq-testnet --scope database,wal,orders,fills,audit,config \
  --encrypt age-x25519 --remote-required --output "<TESTNET_FINAL_BACKUP>"
quantctl backup remote-verify \
  --backup "<TESTNET_FINAL_BACKUP>" --require-ciphertext-hash \
  --require-remote-decrypt --require-plaintext-hash --require-signed-receipt \
  --reject-replay --output "<TESTNET_FINAL_BACKUP_REMOTE_EVIDENCE>"
quantctl backup verify \
  --backup "<TESTNET_FINAL_BACKUP>" --restore-target "<ISOLATED_TESTNET_RESTORE_PATH>" \
  --verify-ledger-projection --verify-audit-chain --output "<TESTNET_RESTORE_EVIDENCE>"
"${DC[@]}" --profile testnet down --remove-orphans
quantctl deployment assert-project-stopped \
  --project aiq-testnet --assert-no-containers --assert-no-published-ports \
  --assert-no-attached-networks --allow-sealed-volumes
quantctl evidence seal-project \
  --project aiq-testnet --require-project-stopped \
  --include database-catalog,volume-inventory,compose-context,final-backup,remote-backup-receipt \
  --final-backup "<TESTNET_FINAL_BACKUP>" \
  --deny-reuse-by aiq-calibration \
  --output "<SEALED_TESTNET_EVIDENCE>"
sha256sum "<SEALED_TESTNET_EVIDENCE>"/*
quantctl isolation verify-seal \
  --source-project aiq-testnet \
  --target-project aiq-calibration \
  --deny-shared-volumes \
  --deny-shared-databases \
  --evidence "<SEALED_TESTNET_EVIDENCE>"
```

随后必须按 [磁盘与归档事故手册 B2](07_DISK_ARCHIVE_INCIDENT.md#b2-阶段互斥项目的退役容量回收)执行退役，固定替换为 `RETIRED_PROJECT=aiq-testnet`、`NEXT_PROJECT=aiq-calibration`、`RETIRED_PROJECT_SEAL=<SEALED_TESTNET_EVIDENCE>`、`RESTORE_EVIDENCE=<TESTNET_RESTORE_EVIDENCE>`。B2 先清理已验证的 L2/可重建缓存；若仍不足，再要求完整事实备份远端解密验签、隔离恢复、精确 volume allowlist 和人工 challenge 后，才可退役这个已停止非生产项目的本地 PostgreSQL/WAL 卷。只有 `verify-seal`、远端验证、隔离恢复和 `capacity-gate` 全部通过，后续才可创建全新的 `aiq-calibration` database、角色、队列与事实窗口。不得把 `aiq-testnet` 的订单、成交、持仓、风险、审计或门禁计时事实复制、导入或只读挂载到新项目；可转交的只有签名测试报告、release/config 摘要和脱敏协议覆盖结果。事实的远端加密备份继续按保留策略保存；未验证的唯一副本会阻断切换并要求扩容，不能删除。

## 必测断言

- `exchangeInfo` 规则、响应头限额和用户数据流状态为运行时事实，不使用永久硬编码值。
- maker-first/post-only TTL、允许转 taker 的净优势/紧迫条件和费用记录符合策略。
- 429 遵守 `Retry-After`、418 持久化封禁；写超时/断连与 UNKNOWN 类 503 才进入未知对账，两种 definite-failure 503、`-1008` 和确定性 4xx 不得误分类。STANDARD 复用同一 `clientOrderId`，ALGO 复用同一 `clientAlgoId/algoId`，零盲重试。
- `ALGO_UPDATE` 的 `NEW/TRIGGERING/TRIGGERED/FINISHED/CANCELED/REJECTED/EXPIRED` 全覆盖；`TRIGGERED` 无 `actualOrderId` 与 `FINISHED` 尚无 child 终态时进入对账，不能伪造 ID 或把 FINISHED 当 FILLED。
- REST/WS API 每次发送前均取得耐久 rate reservation；并发逼近限额、缺失/乱序 header、UNKNOWN 和服务重启不回拨计数，普通请求不能借用保护/撤单与紧急退出保留预算。
- 部分成交、撤单竞争、重复事件、用户流重连和连接轮换均产生追加式事件，最终投影一致。
- 首次 fill 到交易所原生保护确认 ≤1,000 ms；保护失败会停止新仓并安全退出。
- 重启时有挂单/持仓，系统进入 `RISK_LOCKED`，不重复下单并恢复保护状态。
- 单向/全仓不一致时拒绝启用，不自动更改生产账户。
- 所有协议探针的行情源、规则版本和构造输入均标记为 `BINANCE_TESTNET`，不存在生产公开价格或生产过滤器。

## 验收

所有预注册计划通过；订单/成交/持仓/保护与交易所对账差异为 0；重复订单为 0；无保护持仓事件为 0；没有开放 P0/P1。证据能用 correlation ID 串联意图、风险、请求、交易所事件和账本投影。独立 `aiq-testnet` 已停止，卷目录和 database catalog 已签名封存并证明不会被 `aiq-calibration` 复用；远端恢复、签名退役 plan 与 200 GB/至少 30 GB 空闲容量门禁通过，其交易事实和运行小时均不计入后续门禁。满足后才可进入 [02A 三日 L2 校准](02A_CALIBRATION_3D.md)。

## 停止与升级条件

出现生产端点或生产 key、重复订单、订单 `UNKNOWN` 达到 5 秒、保护超过 1,000 ms、账户模式不符、账本不可写或时间偏移 >100 ms 时立即暂停新仓。业务库不可写即进入 `RISK_LOCKED`，出口网关阻断全部新的 Binance REST、WS API 与 market-stream control 请求；V1 没有本地应急日志或延后回填例外，只依赖已在交易所确认的原生保护单并升级 P0，由账户所有者通过 Binance 官方控制面处置。修复后相关计划全部重跑；安全或状态机改动必须重新执行完整 Testnet 套件。

## 证据留存

保存 Testnet endpoint/行情源标识、key fingerprint、账户模式只读结果、规则版本、探针计划摘要、全部订单事件、用户流重连记录、限流响应头、保护时延、对账报告、事故 ID 和 release/config/策略摘要；另保存 `aiq-testnet` Compose 上下文、database catalog、卷清单、停止时间、封存摘要和 `verify-seal` 报告。原始证据生成 SHA-256，禁止仅保存截图或“通过”结论。
