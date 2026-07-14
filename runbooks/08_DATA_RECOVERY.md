# 08 数据恢复与 PITR 手册

## 目的

从加密备份、PostgreSQL 基础备份/WAL、订单事实导出和 L2 manifest 恢复系统，同时保证不覆盖唯一副本、不伪造交易事实，并在恢复后以 `RISK_LOCKED` 与 Binance 全量对账。订单账本目标 RPO 1 小时、RTO 4 小时。

说明：文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约；恢复、补录、切换和解锁能力未通过隔离演练前不得用于生产。

## 适用场景

- PostgreSQL 损坏、误操作或迁移失败；
- VPS 丢失后在新主机重建；
- 归档文件损坏或研究机需要恢复缺片；
- 定期隔离恢复演练。

生产主机仍可管理持仓时，优先保持 execution-service 和交易所原生保护；不要为了恢复报表中断保护路径。主机完全失联时由账户所有者从 Binance 官方控制面核对。

## 前置条件与人工门禁

- 事故已定级并暂停新仓；记录目标恢复时间 `RECOVERY_TARGET_UTC`、当前交易所事实和最后已知账本位置。
- 具备签名 release manifest、基础备份、连续 WAL、订单/审计导出、配置/策略摘要和独立恢复端的 age 解密授权；VPS 上不得出现解密 identity。
- 新建隔离恢复卷/数据库；禁止原地覆盖当前卷或唯一备份。
- 恢复操作者与证据复核者分离记录；解密 key 不写入命令历史、日志或证据包。
- 业务事实恢复与宿主限额恢复是两个独立恢复流。任何 Binance 查询/取消/退出前必须先执行 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)，恢复专用 WAL、fencing、counter 与 429/418；业务数据库 PITR 绝不能回滚该权威。

## 1. 冻结与盘点

```bash
quantctl pause-new-entries --environment production --reason "data recovery" --idempotency-key "<COMMAND_ID>"
quantctl risk lock --environment production --reason "data recovery"
quantctl state snapshot --environment production --read-only --output "<PRE_RECOVERY_SNAPSHOT>"
quantctl backup catalog --remote "<BACKUP_REMOTE_NAME>" --before "<RECOVERY_TARGET_UTC>"
quantctl recovery plan \
  --target-utc "<RECOVERY_TARGET_UTC>" \
  --release-manifest "<SIGNED_RELEASE_MANIFEST>" \
  --output "<RECOVERY_PLAN_FILE>"
```

计划必须列出备份 ID、WAL 起止、预计 RPO/RTO、目标隔离路径、校验值和回退方案。

## 2. 校验备份链

```bash
quantctl backup fetch --plan "<RECOVERY_PLAN_FILE>" --target "<ISOLATED_DOWNLOAD_PATH>" --encrypted
quantctl backup verify-signature --plan "<RECOVERY_PLAN_FILE>" --trusted-key "<BACKUP_SIGNING_PUBLIC_KEY>"
quantctl backup verify-checksums --plan "<RECOVERY_PLAN_FILE>" --include-ciphertext
quantctl backup verify-envelope --plan "<RECOVERY_PLAN_FILE>" --format age-v1 --recipient-sha256 "<EXPECTED_AGE_RECIPIENT_SHA256>"
quantctl backup verify-wal-continuity --plan "<RECOVERY_PLAN_FILE>"
quantctl release verify --manifest "<SIGNED_RELEASE_MANIFEST>"
```

摘要、签名或 WAL 连续性失败时停止；不得跳过坏分段。选择更早的完整恢复点并显式计算 RPO，或升级 P1/P0。

## 3. 隔离 PITR

本步骤在隔离恢复主机/网络执行。加密备份的 age identity 只通过受控 provider 引用提供给单次恢复进程，不复制进待切换的应用卷或容器镜像：

```bash
quantctl recovery restore-postgres \
  --plan "<RECOVERY_PLAN_FILE>" \
  --target-utc "<RECOVERY_TARGET_UTC>" \
  --target-volume "<NEW_POSTGRES_VOLUME>" \
  --age-identity-provider "<AGE_IDENTITY_PROVIDER_REFERENCE>" \
  --network isolated
quantctl recovery verify-database \
  --target "<ISOLATED_DATABASE_DSN_REFERENCE>" \
  --checks integrity,migrations,order-ledger,audit,projections
quantctl recovery replay-projections --target "<ISOLATED_DATABASE_DSN_REFERENCE>" --deterministic
quantctl recovery compare-projections --target "<ISOLATED_DATABASE_DSN_REFERENCE>"
```

金额、订单事件计数、幂等键、策略版本、审批和审计摘要必须一致。恢复工具只引用受控 DSN，不在命令行展开密码。

## 4. L2 与归档恢复

L2 解密只能在独立回测/接收机或隔离恢复机执行，不在生产 VPS 执行。解密 identity 通过受控 identity provider、硬件令牌或仅该恢复机可读的文件描述符提供；禁止把私钥值或路径参数展开到聊天、命令历史、日志或证据包。先校验密文和历史签名回执，再解密到新隔离目录：

```bash
quantctl archive fetch \
  --manifest "<MANIFEST_ID_OR_RANGE>" \
  --target "<ISOLATED_ENCRYPTED_PATH>" \
  --encrypted \
  --preserve-existing
quantctl archive verify-receipt \
  --manifest "<MANIFEST_ID_OR_RANGE>" \
  --verify-key "<ARCHIVE_RECEIPT_VERIFY_KEY_FILE>" \
  --require-remote-decrypt \
  --reject-replay
quantctl archive decrypt-restore \
  --source "<ISOLATED_ENCRYPTED_PATH>" \
  --target "<ISOLATED_L2_PATH>" \
  --format age-v1 \
  --identity-provider "<AGE_IDENTITY_PROVIDER_REFERENCE>" \
  --preserve-existing
quantctl archive verify \
  --path "<ISOLATED_L2_PATH>" \
  --against-manifest "<MANIFEST_ID_OR_RANGE>" \
  --check-ciphertext-hash \
  --check-plaintext-hash \
  --check-parquet
quantctl replay validate --data "<ISOLATED_L2_PATH>" --check-sequences --check-causality
```

manifest 中的 age 版本、recipient 指纹、密文 hash、解密后明文 hash、row/schema 和 Parquet 结构必须全部一致。错误 recipient、损坏密文、回执签名无效或回执重放时立即停止，不能通过跳过验证或换用 VPS 本地私钥继续。缺片必须标记为数据质量事件，不生成虚构 L2；研究回测必须排除或按预注册规则处理缺片。

## 5. 切换与交易所对账

只有隔离恢复全部通过且人工签名批准后，才在维护窗口切换到新卷：

```bash
quantctl recovery challenge --plan "<RECOVERY_PLAN_FILE>" --expires-in 300 --output "<CHALLENGE_FILE>"
quantctl approval sign --challenge "<CHALLENGE_FILE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<APPROVAL_FILE>"
read -r -p "输入 CUTOVER-RECOVERY-<PLAN_ID> 继续: " CONFIRM
test "$CONFIRM" = "CUTOVER-RECOVERY-<PLAN_ID>" || exit 1
quantctl recovery cutover --plan "<RECOVERY_PLAN_FILE>" --challenge "<CHALLENGE_FILE>" --approval "<APPROVAL_FILE>"
quantctl recovery assert-no-decryption-identity --targets production-host,application-volumes,containers
quantctl database verify --read-write --migration-head "<EXPECTED_ALEMBIC_HEAD>"
quantctl host-rate require-ready --evidence "<HOST_RATE_STARTUP_EVIDENCE>" --max-age-seconds 300
quantctl reconcile \
  --environment production \
  --full \
  --recover-missing-events \
  --no-order-retry \
  --output "<POST_RECOVERY_RECON_REPORT>"
quantctl protection verify --environment production --all-positions
```

恢复点后交易所已经发生而数据库缺失的订单、成交和持仓，只能以交易所证实事件追加补录；禁止改写历史事件或盲重发订单。旧持仓保持首次非零仓位时固化的 `owner_strategy_version`，直到该 position episode 归零；旧运行包不可验证时保持原生保护并人工决定自然退出或二次确认平仓。

## 6. 恢复新仓

至少满足：差异为 0、保护健康、数据库连续写入 15 分钟、订单簿重建/预热、时钟 ≤50 ms、无开放 P0/P1、RPO/RTO 已计算。随后通过本机短时效挑战和人工签名解除 `RISK_LOCKED`；Telegram 不可执行。

```bash
quantctl recovery report --plan "<RECOVERY_PLAN_FILE>" --output "<RECOVERY_REPORT>"
export RISK_UNLOCK_PRESTATE="$(quantctl state hash --environment production --require-risk-locked --raw)"
export RISK_UNLOCK_EFFECTIVE_AT="$(quantctl time future --lead-seconds 300 --format rfc3339 --raw)"
export RISK_UNLOCK_EXPIRES_AT="$(quantctl time add --at "$RISK_UNLOCK_EFFECTIVE_AT" --seconds 120 --format rfc3339 --raw)"
quantctl risk unlock-challenge --operator-action RISK_UNLOCK --reason "recovery verified" \
  --report "<RECOVERY_REPORT>" --precondition-state-hash "$RISK_UNLOCK_PRESTATE" \
  --effective-at "$RISK_UNLOCK_EFFECTIVE_AT" --expires-at "$RISK_UNLOCK_EXPIRES_AT" \
  --bind-current-runtime --output "<CHALLENGE_FILE>"
quantctl approval sign --schema contracts/operator-approval.schema.json --expected-action RISK_UNLOCK \
  --challenge "<CHALLENGE_FILE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<APPROVAL_FILE>"
quantctl time await --at "$RISK_UNLOCK_EFFECTIVE_AT" --not-after "$RISK_UNLOCK_EXPIRES_AT" --fail-if-late
quantctl risk unlock --challenge "<CHALLENGE_FILE>" --approval "<APPROVAL_FILE>" \
  --effective-at "$RISK_UNLOCK_EFFECTIVE_AT" --precondition-state-hash "$RISK_UNLOCK_PRESTATE" \
  --consume-once --atomic --fail-if-state-changed
```

## 验收

- 基础备份/WAL/manifest 签名和摘要通过，恢复从未覆盖唯一副本。
- 订单账本、审计和投影可确定性重建；交易所与本地差异为 0。
- 订单账本数据损失 ≤1 小时，`RISK_LOCKED` 可读恢复 ≤4 小时；超出则门禁失败。
- 所有持仓原生保护健康，未知订单为 0，旧策略版本归属不变。
- L2 密文与 age v1/X25519 recipient 指纹一致，在隔离恢复机成功解密；密/明文 hash、Parquet/row/schema、签名回执均与日 manifest 一致，缺片显式标记。
- 切换、解锁均有人工签名、二次确认和远端证据。

## 停止与升级条件

备份身份/摘要/age recipient/回执不可信、密文无法解密、WAL 不连续、投影不一致、RPO >1 小时、预计 RTO >4 小时、交易所差异无法收敛或保护缺失时保持 `RISK_LOCKED` 并升级 P1/P0。若继续持仓不可接受，按 [05](05_PAUSE_CANCEL_FLATTEN.md)紧急平仓。不得为达到 RTO 而跳过完整性验证，也不得把解密 identity 临时复制到 VPS。

## 证据留存

保存事故 ID、恢复计划、备份目录、签名/摘要、age 工具版本/recipient 指纹、密文与明文 hash、回执验签/去重判定、隔离端解密与 Parquet 校验（不含 identity）、WAL 连续性、目标 UTC、隔离恢复日志、投影对比、L2 校验、切换确认、交易所补录事件、保护检查、RPO/RTO 时间线、人工签名和最终报告。证据加密后同步独立审计端。
