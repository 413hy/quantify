# 07 磁盘与远端归档事故手册

## 目的

在原始 L2 增长、远端同步失败、校验不一致或磁盘接近耗尽时保护交易事实和持仓安全。核心规则：对象只有在 age v1/X25519 加密上传、远端实际解密、明文 hash/Parquet 校验和 Ed25519 签名回执全部通过后才可标记 `REMOTE_VERIFIED` 并授权删除；磁盘危险时停止新仓，不能靠删除未验证文件掩盖问题。

说明：文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约；未实现 manifest 约束和删除预览前，禁止用手工批量删除代替。

## 阈值与自动动作

| 任一触发条件（`combine_rule=ANY`） | 状态 | 自动动作 |
|---|---|---|
| 原始区 <60 GB、文件系统 <85% 且可用空间 ≥30 GB | 正常 | 按 72 小时或 80 GB（先到者）滚动保留 |
| 原始区 ≥60 GB、文件系统 ≥85%，或可用空间 <30 GB | 告警 | 检查增长率和远端 backlog，加快已验证文件清理 |
| 原始区 ≥72 GB、文件系统 ≥90%，或可用空间 <20 GB | 危险 | 停止新仓、保持保护/退出、升级 P1，每 5 分钟检查 |
| 原始区 ≥80 GB | 高优先级归档事故 | 持续禁止新仓；只允许按签名 plan 删除 `REMOTE_VERIFIED` 对象 |
| 文件系统 ≥95%，或可用空间 <10 GB | 紧急 | P0、`RISK_LOCKED`；事实不可写时按风险执行退出 |
| 业务数据库不可写 | 紧急 | 立即停新；仅在 [00 第 5 节](00_HOST_RATE_CONTROL.md#5-故障语义)全部前提满足时按专用 permit 取消/退出，否则零新 Binance egress并依赖原生保护/P0 官方控制面 |

原始 L2 本地上限为 72 小时或 80 GB，先到者为准。三条容量轴任一命中即执行对应动作，同时命中时采用最严重动作；磁盘 GB 使用 SI。远端同步失败时删除器必须自动冻结；即使超过保留上限，也不能删除尚未确认的数据。

## 前置条件

- 知道原始 L2、manifest、归档 staging、PostgreSQL/WAL、指标和日志的独立挂载点。
- 远端归档具有主机指纹/证书固定、age v1/X25519 客户端加密、日 manifest、Ed25519 签名解密回执和独立容量监控。VPS 只持 recipient 与回执验签公钥，不持 age 解密 identity。
- 归档服务没有删除 PostgreSQL、WAL、secret 或非归档目录的权限。
- 所有删除必须通过受控 archive 命令按 manifest 选择，禁止手工通配符删除。
- 若事故需要取消或退出，先验证 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)仍健康；不健康时不得从受损业务服务旁路发送请求，保留既有原生保护并升级 P0 人工官方控制面处置。

## 只读诊断

```bash
df -hT
df -i
du -x -h --max-depth=2 /srv/ai-quant | sort -h
quantctl archive status --show-oldest-unsynced --show-backlog-bytes
quantctl archive verify --scope local-manifests --read-only
quantctl archive verify --scope remote-receipts --require-age-v1-x25519 --require-remote-decrypt --verify-signatures --reject-replay --read-only
quantctl database status --include-wal --read-only
quantctl metrics check --profile disk-and-archive
```

不要在诊断输出中显示归档凭据。VPS 本来就不应存在 age 解密密钥；若发现解密 identity，立即冻结删除并升级安全事故。

## 处置流程

### A. 远端不可达或摘要不一致

```bash
quantctl archive freeze-deletion --reason "remote unavailable or checksum mismatch"
quantctl incident open --severity P2 --type archive_sync_failure --output "<INCIDENT_FILE>"
quantctl archive probe --remote "<ARCHIVE_REMOTE_NAME>"
quantctl archive retry --manifest "<FAILED_MANIFEST_ID>" --bounded --max-attempts 3
quantctl archive verify \
  --manifest "<FAILED_MANIFEST_ID>" \
  --both-sides \
  --require-age-v1-x25519 \
  --expected-recipient-sha256 "$ARCHIVE_AGE_RECIPIENT_SHA256" \
  --require-remote-decrypt \
  --require-signed-receipt \
  --reject-replayed-receipt
```

重试必须有退避，不得形成网络或 CPU 风暴。若 recipient、密文/明文摘要、Parquet 结构或回执签名不一致，对象保持 `REMOTE_PENDING`，删除器继续冻结；保留本地和远端两个对象，隔离远端坏副本并重新上传为新对象，不得覆盖唯一副本或人工伪造 `REMOTE_VERIFIED`。

### B. 任一容量轴进入告警

只清理已经双重确认的对象，先预览：

```bash
quantctl archive prune-plan \
  --local-retention-hours 72 \
  --local-size-cap-gb 80 \
  --require-remote-verified \
  --require-remote-decrypt-receipt \
  --verify-receipt-key "$ARCHIVE_RECEIPT_VERIFY_KEY_FILE" \
  --output "<PRUNE_PLAN_FILE>"
quantctl archive prune-verify --plan "<PRUNE_PLAN_FILE>"
quantctl archive prune-challenge --plan "<PRUNE_PLAN_FILE>" --expires-in 300 --output "<CHALLENGE_FILE>"
quantctl approval sign --challenge "<CHALLENGE_FILE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<APPROVAL_FILE>"
```

人工复核计划只含 L2/归档缓存，且每项都关联未重放的远端解密签名回执后执行：

```bash
read -r -p "输入 PRUNE-VERIFIED-<PLAN_ID> 继续: " CONFIRM
test "$CONFIRM" = "PRUNE-VERIFIED-<PLAN_ID>" || exit 1
quantctl archive prune-execute --plan "<PRUNE_PLAN_FILE>" --verified-only --approval "<APPROVAL_FILE>"
quantctl archive verify --scope local-manifests --read-only
```

日志和 Prometheus 只能按已配置保留策略轮转；不得临时清空审计、订单事件、PostgreSQL 或 WAL。

### B2. 阶段互斥项目的退役容量回收

本模式只适用于已经停止产生事实、完成适用的零持仓/零挂单检查并具有签名 seal 的 `aiq-testnet`、`aiq-calibration` 或 `aiq-validation`；不得用于正在运行、可能恢复运行或处于事故保全的 `aiq-live`。`aiq-calibration` 还必须证明全程零执行能力、零订单事实。回收分两步：先删除逐对象达到 `REMOTE_VERIFIED` 的旧项目 L2/可重建缓存；如仍不足，再受控退役该**已停止非生产项目**的 PostgreSQL/WAL 本地卷。第二步不是销毁订单、成交或审计事实：它要求完整加密备份已在远端验证、已从该备份完成隔离恢复，并继续按不少于 365 天或更长适用政策保留远端事实副本。

前置证据必须同时包括：旧项目已停止；`aiq-testnet`/`aiq-validation` 已完成适用环境的最终交易所对账、零仓位和零挂单断言；`aiq-calibration` 因无账户和密钥，以运行时访问矩阵、零 execution-service、零 `OrderIntent`/`OrderEvent` 和零订单/成交事实证明替代交易所对账。三类项目都必须提供远端加密归档全部对象的解密/明文 hash/Parquet/签名回执、PostgreSQL/WAL/订单/成交/审计/配置完整备份（不存在的订单类表也要以 schema/计数证据证明为空）的远端密文与解密校验、该备份的隔离恢复、不可变项目 seal、精确 volume inventory，以及下一项目容量预测。任何唯一副本、未封存事实或法律/事故保全对象都不得进入 plan。

先执行 L2/可重建缓存回收：

```bash
quantctl archive prune-plan \
  --project "<RETIRED_PROJECT>" \
  --retirement-mode \
  --include-recent \
  --require-project-stopped \
  --project-seal "<RETIRED_PROJECT_SEAL>" \
  --include-only l2,recomputable-cache \
  --exclude postgres,wal,orders,fills,audit,config,manifest,catalog,seal \
  --require-remote-verified \
  --require-remote-decrypt-receipt \
  --verify-receipt-key "$ARCHIVE_RECEIPT_VERIFY_KEY_FILE" \
  --output "<RETIREMENT_PRUNE_PLAN>"
quantctl archive prune-verify --plan "<RETIREMENT_PRUNE_PLAN>" --require-isolated-restore-evidence "<RESTORE_EVIDENCE>"
quantctl archive prune-challenge --plan "<RETIREMENT_PRUNE_PLAN>" --expires-in 300 --output "<RETIREMENT_CHALLENGE>"
quantctl approval sign --challenge "<RETIREMENT_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<RETIREMENT_APPROVAL>"
read -r -p "输入 RETIRE-VERIFIED-<PROJECT>-<PLAN_ID> 继续: " CONFIRM
test "$CONFIRM" = "RETIRE-VERIFIED-<PROJECT>-<PLAN_ID>" || exit 1
quantctl archive prune-execute --plan "<RETIREMENT_PRUNE_PLAN>" --verified-only --approval "<RETIREMENT_APPROVAL>"
```

若容量门禁仍不满足，才可为同一退役项目生成精确数据库卷退役计划。`<RETIRED_VOLUME_INVENTORY>` 必须来自 project seal，逐项列出 Docker volume ID、挂载目的、字节数和内容摘要；计划不得使用名称通配符：

```bash
quantctl backup create \
  --project "<RETIRED_PROJECT>" \
  --scope postgres,wal,orders,fills,audit,config \
  --encrypt age-v1-x25519 \
  --output "<RETIRED_FACT_BACKUP>"
quantctl backup remote-verify \
  --backup "<RETIRED_FACT_BACKUP>" \
  --require-ciphertext-hash \
  --require-remote-decrypt \
  --require-plaintext-hash \
  --require-signed-receipt \
  --reject-replay \
  --output "<RETIRED_FACT_REMOTE_RECEIPT>"
quantctl backup verify \
  --backup "<RETIRED_FACT_BACKUP>" \
  --restore-target "<ISOLATED_RESTORE_PATH>" \
  --verify-ledger-projection \
  --verify-audit-chain \
  --output "<RESTORE_EVIDENCE>"
quantctl deployment volume-retire-plan \
  --project "<RETIRED_PROJECT>" \
  --require-project-stopped \
  --project-seal "<RETIRED_PROJECT_SEAL>" \
  --exact-volume-inventory "<RETIRED_VOLUME_INVENTORY>" \
  --include-only postgres,wal \
  --exclude-project aiq-live \
  --exclude-active --exclude-incident-hold \
  --require-remote-receipt "<RETIRED_FACT_REMOTE_RECEIPT>" \
  --require-isolated-restore-evidence "<RESTORE_EVIDENCE>" \
  --remote-retention-days 365 \
  --output "<VOLUME_RETIRE_PLAN>"
quantctl deployment volume-retire-verify --plan "<VOLUME_RETIRE_PLAN>" --deny-wildcards --deny-live
quantctl deployment volume-retire-challenge --plan "<VOLUME_RETIRE_PLAN>" --expires-in 300 --output "<VOLUME_RETIRE_CHALLENGE>"
quantctl approval sign --challenge "<VOLUME_RETIRE_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<VOLUME_RETIRE_APPROVAL>"
read -r -p "输入 RETIRE-VOLUMES-<PROJECT>-<PLAN_ID> 继续: " CONFIRM
test "$CONFIRM" = "RETIRE-VOLUMES-<PROJECT>-<PLAN_ID>" || exit 1
quantctl deployment volume-retire-execute --plan "<VOLUME_RETIRE_PLAN>" --approval "<VOLUME_RETIRE_APPROVAL>"
quantctl deployment verify-retired --project "<RETIRED_PROJECT>" --retain catalog,seal,remote-receipts
quantctl deployment capacity-gate \
  --next-project "<NEXT_PROJECT>" \
  --disk-capacity-bytes 200000000000 \
  --max-active-allocation-bytes 170000000000 \
  --min-free-bytes 30000000000 \
  --include-retired-seals
```

`down -v`、手工删除卷、名称通配符和泛化 Docker prune 仍然禁止。退役后本机只保留小型只读 volume metadata、catalog、seal 与远端回执；它们必须计入部署文档的 20 GB staging/evidence 预算，远端加密事实备份按保留策略继续存在。若存在任何未验证对象、恢复失败、plan 越界、签名不符或容量门禁失败，停止切换并先扩容。

### C. 任一容量轴进入危险或紧急

```bash
quantctl pause-new-entries --environment production --reason "disk danger" --idempotency-key "<COMMAND_ID>"
quantctl risk lock --environment production --reason "disk danger"
quantctl protection verify --environment production --all-positions
quantctl persistence status --assert-writable
```

立即扩容卷、恢复远端通道或把已验证归档移出。若数据库/耐久队列不可写或保护状态不能持久化，按 [05 暂停/撤单/平仓](05_PAUSE_CANCEL_FLATTEN.md)执行风险退出。不得为了维持交易删除未验证的市场数据或交易事实。

## 恢复步骤

只有远端连接、age recipient、密/明文摘要、Parquet 可读性、签名回执和容量恢复，backlog 清零，原始区 <60 GB、文件系统 <85%、可用磁盘 ≥30 GB，数据库持续可写 15 分钟后才可解冻删除：

```bash
quantctl archive verify --scope all-pending --both-sides --require-age-v1-x25519 --require-remote-decrypt --require-signed-receipt --reject-replay
quantctl archive status --assert-backlog-zero
quantctl archive unfreeze-challenge --reason "remote verified and free space recovered" --output "<UNFREEZE_CHALLENGE>"
quantctl approval sign --challenge "<UNFREEZE_CHALLENGE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<UNFREEZE_APPROVAL>"
quantctl archive unfreeze-deletion --approval "<UNFREEZE_APPROVAL>"
quantctl persistence soak --duration 15m --fail-on-write-error
quantctl reconcile --environment production --full --fail-on-difference
```

新仓恢复需要本机签名解除 `RISK_LOCKED`，Telegram 不具备该权限。

## 验收

- 未取得有效远端解密签名回执的切片零删除；所有删除均可追溯到 prune plan、manifest 和唯一回执。
- 小时片、日 manifest、age recipient 指纹、本地明文/远端密文 SHA-256、远端解密明文 SHA-256 和 Parquet 结构一致，缺片显式标记。
- 原始区 <60 GB、文件系统 <85%、可用磁盘 ≥30 GB，inode、PostgreSQL、WAL 和耐久队列健康；backlog 为 0。
- 危险期间没有新仓，既有持仓原生保护健康；交易所对账差异为 0。
- 根因、增长率和容量改进已记录，告警已验证恢复。

## 停止与升级条件

远端身份/recipient/回执验签公钥不可信、摘要持续不一致、回执重放、VPS 出现解密 identity、容量无法在预计耗尽前恢复、数据库写失败或可用磁盘 <10 GB 时升级 P0/P1。业务库不可写时立即进入 `RISK_LOCKED`，出口网关阻断全部新的 Binance REST、WS API 与 market-stream control 请求；V1 没有本地应急日志或延后回填例外，只依赖已确认的交易所原生保护并由账户所有者使用 Binance 官方控制面。保持删除冻结；严禁手工删除、覆盖唯一副本或解除阈值告警继续运行。

## 证据留存

保存 `df/du`、增长曲线、backlog、manifest、age 工具版本/recipient 指纹、密文与明文摘要、远端解密/Parquet 结果、回执 payload/签名/去重判定、probe/重试、prune plan、人工确认、删除结果、数据库写测试、持仓保护、对账、告警和事故时间线。命令输出脱敏后生成 SHA-256 并同步独立审计端。
