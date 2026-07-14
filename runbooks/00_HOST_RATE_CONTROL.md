# 00 宿主级限额权威与唯一 Binance 出口启动手册

## 目的与不可突破的边界

本手册是 Testnet、三日校准、72 小时双验证、实盘、恢复、升级与回滚的共同前置门禁。它启动两个与业务 release 独立、跨业务项目常驻的宿主项目：

- `aiq-host-control`：`host-rate-postgres`、`rate-budget-service`、`host-attestation-signer`；唯一事实库为 `aiq_host_rate_control`。
- `aiq-binance-egress`：恰好一个 `binance-egress-gateway`；它是主机上唯一允许解析 Binance 域名、建立 Binance TCP/TLS/WebSocket、发送 REST/WS API/control frame 的进程。

业务容器必须没有到 Binance 的网络路由。它们只能通过 `/run/ai-quant-rate/rate.sock` 预约预算，再通过 `/run/ai-quant-egress/gateway.sock` 提交不可变请求。gateway 根据实际 authority、method、path、parameter names、exact wire hash、connection generation 重新生成事实，向 allocator 发起 `PermitConsumeRequest`；只有 `CONSUME_GRANTED` 才发送一次。allocator 只裁决预算且不建立 Binance 连接，gateway 不持 capability signing key，也不持 Binance API secret。生产 `BINANCE_API_KEY/SECRET` 仅由 `execution-service` 在内存中生成短寿命预签名请求；gateway 不重签。

**startup evidence 验签通过前，禁止任何新的 Binance REST、WS API、market-stream connect 或 control send，包括 `/time`、`exchangeInfo`、listenKey、subscribe/unsubscribe、ping/pong、查询、撤单与平仓。** 已建立连接的纯入站 relay 可在 allocator 故障后持续到自然断开，但不得重连、续订或发送任何 control frame。

文中的 `quantctl`、Compose 文件、命令输出和 Schema 是 VPS Codex 必须实现的受控接口契约；不得用 `curl`、临时脚本、SDK 或额外转发层旁路。

## 1. 离线验签，网络保持全闭

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export HOST_CONTROL_ENV_PATH="<HOST_CONTROL_ENV_PATH>"
export GATEWAY_ENV_PATH="<GATEWAY_ENV_PATH>"
export STAGE="<testnet|calibration|validation|live|recovery|upgrade>"
export HOST_RATE_STARTUP_EVIDENCE="<HOST_RATE_STARTUP_EVIDENCE>"
cd "$PROJECT_DIR"

HC=(docker compose -p aiq-host-control -f deploy/host-control.compose.yaml --env-file "$HOST_CONTROL_ENV_PATH")
GW=(docker compose -p aiq-binance-egress -f deploy/binance-egress.compose.yaml --env-file "$GATEWAY_ENV_PATH")

quantctl egress close --scope binance-all --reason "host startup gate"
quantctl release verify --manifest "<SIGNED_HOST_CONTROL_RELEASE_MANIFEST>" \
  --trust-root "$RELEASE_SIGNING_TRUST_ROOT_FILE" \
  --require-rate-image --require-gateway-image --require-compose-hashes \
  --require-host-control-migration-head
quantctl config verify-host-boundary \
  --trust-root "$HOST_CONTROL_CONFIG_TRUST_ROOT_FILE" \
  --rate-budget "<RATE_BUDGET_CONFIG_FILE>" \
  --endpoint-catalog "<ENDPOINT_COST_CATALOG_FILE>" \
  --mandatory-inventory "<MANDATORY_ENDPOINT_INVENTORY_FILE>" \
  --connection-contract "<BINANCE_CONNECTION_CONTRACT_FILE>" \
  --trust-bundle "<CAPABILITY_TRUST_BUNDLE_FILE>" \
  --network-egress "<NETWORK_EGRESS_POLICY_FILE>" \
  --rate-uds-schema contracts/rate-budget-uds.schema.json \
  --gateway-request-schema contracts/binance-gateway-request.schema.json \
  --gateway-ipc-schema contracts/binance-gateway-ipc.schema.json \
  --startup-evidence-schema contracts/host-rate-startup-evidence.schema.json \
  --require-schema-hash-match --require-content-hash-match \
  --require-signature --require-not-expired --expected-stage "$STAGE"
"${HC[@]}" config --quiet
"${GW[@]}" config --quiet
quantctl compose assert-boundary \
  --host-control-project aiq-host-control \
  --gateway-project aiq-binance-egress \
  --host-control-database aiq_host_rate_control \
  --require-separate-volumes --forbid-public-ports \
  --require-single-gateway --forbid-business-binance-route \
  --forbid-gateway-api-secret --forbid-gateway-signing-key
```

任一 baseline 为 `UNVALIDATED_ENGINEERING_BASELINE`、hash/签名/有效期不符、mandatory group 在适用 authority 缺失、未知 endpoint/参数组合、生产文档被冒充为 Testnet 证据，或 Compose 越过秘密/网络边界时，保持零 egress 并停止。

两个 trust root 都必须是 root-owned `0444`、release 不可写，并在首次安装时与独立控制台显示的 SHA-256 fingerprint 人工比对。配置、release、owner approval、research approval 使用相互独立的 keyring；轮换要求旧/新 key 双签和重叠期，撤钥立即 fail-closed，不能把 capability issuer key 当配置根信任。

## 2. 恢复独立事实库与 allocator

```bash
"${HC[@]}" up -d host-rate-postgres
quantctl host-rate database verify \
  --database aiq_host_rate_control --read-write \
  --migration-head "<EXPECTED_HOST_RATE_MIGRATION_HEAD>" \
  --require-independent-volume --require-restored-wal
quantctl host-rate recover-authority \
  --preserve-reservations --preserve-permits --preserve-capability-nonces \
  --preserve-consume-decisions --preserve-outcomes --preserve-observations \
  --preserve-window-allocations --preserve-429-418 \
  --deny-counter-reset --deny-environment-as-scope-key
quantctl host-rate acquire-fencing-lease \
  --single-writer-per-scope --increment-epoch --output "<FENCING_EVIDENCE>"
"${HC[@]}" up -d rate-budget-service
quantctl uds verify \
  --socket /run/ai-quant-rate/rate.sock --mode 0660 \
  --require-root-owned-runtime-dir --require-fixed-uid-gid \
  --require-so-peercred-acl --forbid-tcp-fallback --max-roundtrip-ms 25
```

恢复点不能证明某窗口已使用量时，将该窗口视为完全消耗；418 可能丢失时，对该 authority 全 class 无限期阻断并开 P0，禁止用探测请求猜测解封。

## 3. 启动唯一 gateway，仍不开放外网

```bash
"${GW[@]}" up -d binance-egress-gateway
quantctl uds verify \
  --socket /run/ai-quant-egress/gateway.sock --mode 0660 \
  --require-root-owned-runtime-dir --require-fixed-uid-gid \
  --require-so-peercred-acl --forbid-tcp-fallback \
  --max-frame-bytes 16777216 --max-decoded-payload-bytes 12582912
quantctl gateway verify \
  --instances-exactly 1 --only-binance-socket-creator \
  --no-api-secret --no-capability-signing-key \
  --allocator-socket /run/ai-quant-rate/rate.sock \
  --deny-send-before-consume --deny-send-more-than-once \
  --output "<GATEWAY_BOUNDARY_EVIDENCE>"
quantctl egress prove-closed \
  --business-binance-route-count 0 --gateway-binance-route-count 0
```

此时 gateway IPC 可以接收本地请求，但网络策略仍必须拒绝所有 Binance 目标。

## 4. 首次空库的生产 bootstrap

仅真正首次安装且专用数据库无任何历史时，才允许使用已签名、最长有效 15 分钟的 bootstrap catalog。它只含 `REST_SERVER_TIME` 和 `REST_UM_EXCHANGE_INFO`，每项成本 1、各一次。此阶段不用 5% 池：bootstrap floor 精确允许这两项；拿到经验证总限额后进入运行算法 `R=max(2,floor(L×0.05))`，且 `L>=3`。host reserve 与 business pool 双向不可借；业务 class 内部优先级借用不包含 `HOST_RATE_CONTROL`。

`host-bootstrap-runner` 持 `HOST_BOOTSTRAP_AUTHORITY` key，但没有 Binance 路由；请求路径必须为：

```text
host-bootstrap-runner
  -> ReserveRequest
  <- ReserveDecision(GRANTED)
  -> GatewaySendRequest
gateway
  -> PermitConsumeRequest(actual operation facts + wire hash)
  <- PermitConsumeDecision(CONSUME_GRANTED)
  -> send exactly once
  -> SendOutcome + ServerTimeObservation / ExchangeRateLimitObservation
```

```bash
quantctl host-rate bootstrap-plan \
  --empty-authority-only \
  --allow REST_SERVER_TIME,REST_UM_EXCHANGE_INFO \
  --exactly-once-each --bootstrap-floor-no-percentage \
  --caller host-bootstrap-runner --output "<BOOTSTRAP_PLAN>"
quantctl egress open-bootstrap \
  --gateway-only --authority BINANCE_PRODUCTION_FAPI \
  --endpoints REST_SERVER_TIME,REST_UM_EXCHANGE_INFO \
  --require-plan "<BOOTSTRAP_PLAN>"
quantctl gateway bootstrap-execute \
  --plan "<BOOTSTRAP_PLAN>" \
  --reserve-socket /run/ai-quant-rate/rate.sock \
  --gateway-socket /run/ai-quant-egress/gateway.sock \
  --require-consume-before-send --record-full-causal-chain \
  --output "<BOOTSTRAP_EVIDENCE>"
quantctl egress close --scope binance-all --reason "bootstrap complete"
quantctl host-rate converge \
  --from-gateway-server-time-observation \
  --from-gateway-exchange-rate-limit-observation \
  --from-verified-response-headers --monotonic-max-observation \
  --deny-limit-increase-without-authority --output "<CONVERGENCE_EVIDENCE>"
```

响应未知仍永久消耗本窗口预算；429/418 立即持久化并覆盖全部 class。

## 5. Testnet 契约的无循环 bootstrap

生产官方文档不得推定覆盖 Testnet。优先使用明确覆盖 Testnet authority 的官方资料。若没有，允许人工签名一次 `SIGNED_TESTNET_BOOTSTRAP_OPERATOR_CEILING` profile，且必须同时满足：

- 最长 TTL 900 秒；`source_ids=[]` 并绑定独立 operator challenge/hash/signature。
- 仅 Testnet authority、仅唯一 gateway、最多 1 个连接和 1 个 stream、禁止订单与用户数据流。
- 仅运行连接/心跳/控制限额探针；业务 executor 不持探针秘密，也没有直连路由。
- 探针完成立即关闭 egress，生成签名 probe evidence，并替换为 `SIGNED_TESTNET_PROTOCOL_PROBE` profile；bootstrap profile 立刻撤销。未完成替换不得进入 Testnet 交易测试。

```bash
quantctl testnet contract-bootstrap verify \
  --profile "<SIGNED_TESTNET_BOOTSTRAP_PROFILE>" \
  --max-ttl-seconds 900 --max-connections 1 --max-streams 1 \
  --orders-forbidden --gateway-only --require-operator-signature
quantctl egress open-testnet-probe \
  --gateway-only --testnet-only --require-bootstrap-profile
quantctl testnet probe execute \
  --runner testnet-probe-runner \
  --reserve-socket /run/ai-quant-rate/rate.sock \
  --gateway-socket /run/ai-quant-egress/gateway.sock \
  --plan "<SIGNED_TESTNET_PROBE_PLAN>" --orders-forbidden \
  --output "<SIGNED_TESTNET_PROBE_EVIDENCE>"
quantctl egress close --scope binance-all --reason "testnet probe complete"
quantctl testnet contract-bootstrap replace \
  --old "<SIGNED_TESTNET_BOOTSTRAP_PROFILE>" \
  --new "<PROBE_BACKED_SIGNED_RUNTIME_PROFILE>" \
  --revoke-old --require-probe-evidence
```

Testnet 缺强制 endpoint 时必须阻断阶段并记录签名 probe evidence；不能静默从 inventory 删除。

## 6. 生成短寿命 startup evidence 并开放唯一出口

`host-attestation-signer` 使用独立 Ed25519 key，固定 UID/GID，仅能读取宿主状态与写 evidence；allocator、gateway、业务服务均不得挂载其私钥。签名 payload 为 `Ed25519(SHA-256(UTF-8(RFC8785-JCS(content))))`。证据最长 300 秒，每 60 秒刷新；消费者在过期、撤钥、boot/release/hash/socket inode/fencing 变化时立即 fail-closed。

```bash
"${HC[@]}" up -d host-attestation-signer
quantctl host-rate attest \
  --schema contracts/host-rate-startup-evidence.schema.json \
  --bind-host-boot --bind-release --bind-all-artifacts \
  --bind-rate-database --bind-fencing --bind-both-sockets \
  --require-single-gateway --require-zero-business-binance-routes \
  --require-fresh-observation-per-enabled-authority \
  --require-no-nonce-permit-anomaly \
  --ttl-seconds 300 --refresh-before-expiry-seconds 60 \
  --output "$HOST_RATE_STARTUP_EVIDENCE"
quantctl host-rate evidence verify \
  --evidence "$HOST_RATE_STARTUP_EVIDENCE" \
  --require-signed-ready --max-age-seconds 300 \
  --require-enabled-stage-authority-closure
quantctl egress open-controlled \
  --policy "<NETWORK_EGRESS_POLICY_FILE>" \
  --gateway-project aiq-binance-egress \
  --gateway-only --require-zero-business-routes \
  --require-evidence "$HOST_RATE_STARTUP_EVIDENCE"
quantctl gateway require-ready \
  --evidence "$HOST_RATE_STARTUP_EVIDENCE" --continuous
```

`validation` 的一份 evidence 必须同时绑定 `shadow` 与 `testnet`，并对 production/Testnet 各 FAPI/FSTREAM authority 给出新鲜 observation/profile；不能只验证一个 lane。

## 7. 持续检查与失效演练

```bash
quantctl host-rate evidence watch \
  --file "$HOST_RATE_STARTUP_EVIDENCE" --fail-closed-on-expiry \
  --fail-closed-on-hash-change --fail-closed-on-socket-replacement
quantctl host-rate evidence drill-expiry \
  --expect-zero-new-rest --expect-zero-new-ws-api \
  --expect-zero-new-stream-connect --expect-zero-control-send
quantctl host-rate artifact refresh-plan \
  --minimum-lead-seconds 86400 --require-human-signature \
  --output "<ARTIFACT_REFRESH_PLAN>"
```

限额 snapshot、connection contract、trust bundle、inventory、catalog 至少在过期前 24 小时进入人工刷新流程；未按时完成则证据到期后自动关闭新 egress。`/time` 刷新同样消耗非借用的 `HOST_RATE_CONTROL` reserve，不因业务池饱和而饿死。

## 8. 故障语义

- allocator、专用 PostgreSQL、fencing、任一 UDS、gateway、attestation signer、签名 artifact、caller identity、evidence 或唯一网络边界失效：`RISK_LOCKED`，阻断全部新的 REST/WS API/market-stream connect/control send；首版无 emergency lease。
- 业务事实数据库不可写：立即 `RISK_LOCKED`；gateway 阻断全部新的 Binance 出站。首版没有 emergency journal/backfill 例外，只依赖已确认的交易所原生保护并升级 P0，由账户所有者使用 Binance 官方控制面处置。
- 已授 permit 在 `NOT_SENT_AFTER_CONSUME` 或结果未知时不得释放 budget/nonce；过 deadline 无 outcome 是 P0。
- 恢复不会自动解除 `RISK_LOCKED`。必须先验证完整账本、全量对账、重新生成 startup evidence，再走对应人工解锁手册。

## 9. 验收标准

startup evidence 必须通过 [Schema](../contracts/host-rate-startup-evidence.schema.json)，并绑定：host-control release manifest；allocator/gateway image、Compose、config；全部关键 Schema/content hash；独立 migration head/WAL/fencing；两个 UDS 的 inode/mode/owner/ACL；恰好一个 gateway；零业务 Binance route；每个启用 authority 的 server-time/rate-limit 或 connection profile；nonce/permit 完整性；bootstrap 的 Reserve→Gateway→Consume→Send→Observation 因果链。

故障注入必须证明：无 `CONSUME_GRANTED` 时 gateway 发送数为 0；business 容器直接连 Binance 永远失败；allocator/gateway/数据库/evidence 任一失效时所有新出站为 0；业务项目切换不会回拨 counter、fencing、nonce、封禁或替换两个宿主项目。
