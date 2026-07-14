# 05 暂停新仓、取消挂单与紧急平仓手册

## 目的

提供三个逐级风险动作：暂停新仓、取消非保护挂单、紧急 reduce-only 平仓。动作必须幂等、可审计；暂停和取消不能误删保护单，紧急平仓必须二次确认。全局风险和数据健康可覆盖策略的自然退出规则。

说明：文中的 `quantctl` 是实现阶段必须提供并经过故障测试的受控 CLI 契约；不得用无幂等、无签名或无审计的临时脚本代替高危动作。

## 何时使用

- **暂停新仓**：行情/时钟/资源降级、人工检查、连亏、日损/回撤、维护窗口。
- **取消非保护挂单**：执行状态不确定、维护、风险锁、入场不再有效。
- **紧急平仓**：保护缺失、账户事实严重不一致、数据库无法持久化、密钥事件或继续持仓风险不可接受。

若 VPS 完全失联，使用 Binance 官方控制面核对；恢复后仍按 [06 重启对账](06_RESTART_RECONCILIATION.md)处理。

## 前置条件

- 确认环境和账户脱敏标识，记录当前 release、风险状态、持仓、挂单和保护快照。
- 所有生产操作只允许使用 VPS 本机受控 CLI 或 loopback/私网 FastAPI。Telegram 只接收通知，不查询、不暂停、不撤单、不平仓、不确认。
- 飞书仅通知，无操作权限。
- 紧急情况下可以先 fail-closed，但所有自动动作必须产生事故和审计事件。
- 所有自动或 CLI 取消与退出仍须满足 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)；若 allocator、专用 PostgreSQL、fencing 或 UDS 不健康，则零新 Binance egress，依赖既有原生保护并由账户所有者使用 Binance 官方控制面处置。

## A. 暂停新仓

本机 CLI：

```bash
quantctl status --environment "<ENVIRONMENT>"
quantctl pause-new-entries \
  --environment "<ENVIRONMENT>" \
  --reason "<INCIDENT_OR_MAINTENANCE_REASON>" \
  --idempotency-key "<UNIQUE_COMMAND_ID>"
quantctl status --environment "<ENVIRONMENT>" --assert-no-new-entries
```

Telegram 只能收到操作结果的脱敏通知；服务端不得注册任何入站命令。暂停不撤销 reduce-only/保护单，不阻止持仓风险管理。

## B. 取消非保护挂单

先只读预览：

```bash
quantctl orders list --environment "<ENVIRONMENT>" --status open --include-standard --include-algo --resolve-actual-orders --classify-protection
quantctl cancel-pending \
  --environment "<ENVIRONMENT>" \
  --exclude-protective \
  --dry-run \
  --output "<CANCEL_PREVIEW_FILE>"
```

人工核对 preview 已按 `transport/order_role/clientAlgoId/algoId/actualOrderId` 分类，且不含 Algo 止损、reduce-only 退出或其他原生保护后执行：

```bash
quantctl cancel-pending \
  --environment "<ENVIRONMENT>" \
  --exclude-protective \
  --preview "<CANCEL_PREVIEW_FILE>" \
  --idempotency-key "<UNIQUE_COMMAND_ID>"
quantctl reconcile --environment "<ENVIRONMENT>" --full --include-standard-orders --include-open-algo-orders --resolve-actual-orders --fail-on-difference
```

取消竞争中出现部分成交时，先更新持仓和保护数量，再继续；禁止假定撤单请求成功。

## C. 紧急平仓（高危、二次确认）

第一步生成只读计划，计划包含每个 symbol 的交易所持仓、reduce-only side/quantity、保护单和预期取消项：

```bash
quantctl emergency-flatten plan \
  --environment production \
  --scope all \
  --cancel-entry-orders \
  --keep-protective-until-fill \
  --output "<FLATTEN_PLAN_FILE>"
sha256sum "<FLATTEN_PLAN_FILE>"
```

第二步由授权人员核对计划，生成不超过 60 秒且绑定计划摘要/账户/nonce 的挑战并签名：

```bash
quantctl emergency-flatten challenge \
  --plan "<FLATTEN_PLAN_FILE>" \
  --expires-in 60 \
  --output "<CHALLENGE_FILE>"
quantctl approval sign --challenge "<CHALLENGE_FILE>" --key "<OWNER_SIGNING_KEY_PATH>" --output "<APPROVAL_FILE>"
read -r -p "输入 FLATTEN-PRODUCTION-<CHALLENGE_ID> 继续: " CONFIRM
test "$CONFIRM" = "FLATTEN-PRODUCTION-<CHALLENGE_ID>" || exit 1
quantctl rate-budget verify-emergency-capacity \
  --plan "<FLATTEN_PLAN_FILE>" --require-durable-reservations --current-verified-window
quantctl emergency-flatten execute \
  --plan "<FLATTEN_PLAN_FILE>" \
  --challenge "<CHALLENGE_FILE>" \
  --approval "<APPROVAL_FILE>"
```

执行只使用 STANDARD reduce-only 市价/交易所允许的紧急退出方式，并利用 Binance 对 `-1008` 下 reduce-only/close-position 的优先语义；每个实际请求仍须由健康 allocator 原子取得并消费持久化 `EMERGENCY_REDUCE_ONLY` reservation，低优先级不得越过自身 ceiling。首版没有预发 emergency lease；allocator、host-control 数据库、gateway 或任一 UDS 故障时 CLI 必须拒绝全部新的 Binance REST、WS API、market-stream 建连及 control send，保留现有原生保护并立即升级 P0，由账户所有者使用 Binance 官方控制面处置。Algo 保护在对应持仓确认归零前保留，随后用 Algo cancel endpoint 取消并查询确认残留，避免先撤保护再暴露。本机/私网紧急平仓必须走短时效挑战；重复确认按 idempotency key 只执行一次。Telegram 只接收结果通知。

## 事后确认

```bash
quantctl reconcile --environment production --full --include-standard-orders --include-open-algo-orders --resolve-actual-orders --fail-on-difference
quantctl positions list --environment production --assert-flat
quantctl orders list --environment production --status open --include-standard --include-algo --resolve-actual-orders --classify-protection
quantctl risk lock --environment production --reason "post-emergency-flatten"
quantctl evidence export --incident "<INCIDENT_ID>" --output "<EVIDENCE_DIR>"
```

“平仓请求已发送”不是完成。必须以交易所持仓为 0、相关成交到账、残留订单已分类处理和本地投影一致作为完成依据。结束状态固定 `RISK_LOCKED`。

## 验收

- 暂停后没有新开仓意图送出，退出和保护仍可工作。
- 取消列表不含必要保护；部分成交导致的持仓/保护数量已同步。
- 紧急平仓有计划摘要、短时效挑战、签名、二次确认、每个已发送请求的 emergency reservation 和幂等结果。
- 交易所与本地订单/成交/持仓差异为 0；平仓后保持 `RISK_LOCKED`。
- 未授权、过期、重放或摘要不符命令均被拒绝并审计。

## 停止与升级条件

任何订单结果未知时禁止重复执行；先查询和对账。若保护失效、退出被拒、账户模式异常、执行服务不可用或主机失联，立即升级 P0/P1，由账户所有者使用 Binance 官方控制面处置并保全证据。解除风险锁只能按本机人工审批流程，不能通过 Telegram。

## 证据留存

保存触发原因、环境/账户指纹、前后持仓与订单快照、命令 ID、preview/plan/challenge 摘要、签名指纹、rate reservation ID/窗口/成本、交易所响应、用户流事件、对账结果、告警送达和事故时间线。secret、签名私钥和完整认证头不得进入证据。
