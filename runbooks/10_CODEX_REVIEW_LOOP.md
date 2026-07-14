# 10 VPS Codex 独立审查与迭代运行手册

## 目的

把每个工程实现补丁转换成可复现、可复审、可回滚且需要人工批准的候选发布。本文中的 `quantctl review ...` 是 VPS Codex 必须实现的受控 CLI 契约；Codex 本身不持生产秘密或批准私钥。本文不处理每 30 分钟生产策略分析，也不处理月度字段白名单自动发布；后者使用 [11 月度自动迭代](11_MONTHLY_AUTO_ITERATION.md)。

## 前置条件

- 当前需求、ADR、契约和 `MANIFEST.sha256` 已验证。
- 实现工作树、基础 release digest 和 patch ID 已冻结。
- 实现者会话已结束；复审使用全新上下文和不同 actor ID。
- 生产秘密未注入审查环境；Champion OOS 仍处于盲化 ACL。
- 已准备 normal/error/boundary 三类场景和适用 replay/Testnet/fault/resource 计划。

## 1. 冻结补丁与需求映射

```bash
set -euo pipefail
export REVIEW_ID="<REVIEW_ID>"
export PATCH_ID="<PATCH_ID>"
export RELEASE_ID="<RELEASE_ID>"
export EVIDENCE_DIR="evidence/reviews/$REVIEW_ID"

quantctl review freeze-patch \
  --patch "$PATCH_ID" --base-release "$RELEASE_ID" \
  --output "$EVIDENCE_DIR/changed-files.manifest.json" \
  --require-clean-generated-files --sha256
quantctl review requirements-map \
  --patch-manifest "$EVIDENCE_DIR/changed-files.manifest.json" \
  --output "$EVIDENCE_DIR/requirements-map.json" \
  --fail-unmapped-behavior
```

发现未映射行为、生成文件漂移、secret、未解释占位符或不在 patch manifest 中的文件时立即失败。

## 2. 计算变更等级

```bash
quantctl review classify \
  --patch-manifest "$EVIDENCE_DIR/changed-files.manifest.json" \
  --requirements "$EVIDENCE_DIR/requirements-map.json" \
  --rules docs/13_VPS_CODEX_AUDIT_AND_ITERATION.md \
  --output "$EVIDENCE_DIR/change-class.json" \
  --fail-on-ambiguous
```

任何策略、特征、成本、风险、订单、数据选择或事件时序变化最低为 C3。不确定时输出人工决定，不得下调等级。

## 3. 独立复审

复审者从冻结需求和 diff 开始，逐项填写 [PR 检查表](../templates/PR_REVIEW_CHECKLIST.md)，不得读取实现者未验证的推理记录。执行：

```bash
make lint
make typecheck
make contracts
make test
make replay-test
make integration-test
make fault-test
make security-scan
make resource-test

quantctl review scenario --kind normal --plan "<NORMAL_PLAN>" --evidence "$EVIDENCE_DIR/tests/normal"
quantctl review scenario --kind error --plan "<ERROR_PLAN>" --evidence "$EVIDENCE_DIR/tests/error"
quantctl review scenario --kind boundary --plan "<BOUNDARY_PLAN>" --evidence "$EVIDENCE_DIR/tests/boundary"
quantctl review oos-access verify --expect-zero --output "$EVIDENCE_DIR/oos-access-log.json"
quantctl review rollback-drill --release "$RELEASE_ID" --patch "$PATCH_ID" --output "$EVIDENCE_DIR/rollback"
```

只运行适用子集必须在报告中给出 `NOT_APPLICABLE` 的具体理由；C3 不允许跳过 replay、fault、Testnet/仿真、resource 和 rollback。

## 4. 生成并验证报告

```bash
quantctl review build-report \
  --review-id "$REVIEW_ID" --patch "$PATCH_ID" --release "$RELEASE_ID" \
  --implementer-actor "<IMPLEMENTER_ACTOR_ID>" \
  --reviewer-actor "<REVIEWER_ACTOR_ID>" \
  --evidence "$EVIDENCE_DIR" \
  --output "$EVIDENCE_DIR/codex-review-report.json"

quantctl contract validate \
  --schema contracts/codex-review-report.schema.json \
  --instance "$EVIDENCE_DIR/codex-review-report.json" \
  --verify-jcs-hash
quantctl review verify \
  --report "$EVIDENCE_DIR/codex-review-report.json" \
  --require-different-actors --require-zero-p0-p1 \
  --require-oos-clean --verify-all-evidence-hashes
```

## 5. 执行重置决定

```bash
RESET_DECISION="$(quantctl contract field --instance "$EVIDENCE_DIR/codex-review-report.json" --pointer /content/reset_decision --raw)"
quantctl gate apply-reset-decision \
  --decision "$RESET_DECISION" --patch "$PATCH_ID" --release "$RELEASE_ID" \
  --require-human-ack --append-only
```

- `LOCAL_TESTS_ONLY`：不修改 72h/OOS 状态。
- `RESTART_72H`：作废当前门禁窗口，从新 release 健康起点重新计时。
- `RESTART_OOS_87D` 或 `NEW_CHAMPION_REQUIRED`：暂停新仓，封存旧窗口，重新生成 Champion 和未来 `effective_at`，不得拼接样本。
- `HUMAN_DECISION_REQUIRED`：保持发布阻断和原状态，不做默认选择。

## 6. 人工批准和发布

```bash
quantctl approval prepare \
  --action RELEASE_PATCH --review "$EVIDENCE_DIR/codex-review-report.json" \
  --expires-in-seconds 300 --output "<APPROVAL_CHALLENGE_FILE>"
quantctl approval verify --challenge "<APPROVAL_CHALLENGE_FILE>" --approval "<HUMAN_APPROVAL_FILE>" --consume-once
quantctl release stage --patch "$PATCH_ID" --review "$EVIDENCE_DIR/codex-review-report.json" --approval "<HUMAN_APPROVAL_FILE>"
```

Telegram、工程审查 Codex、CI 和远程 API 均不能完成上述人工签名。发布后按 C1/C2/C3 的观察与门禁执行；任何差异立即回滚或进入前向修复，不自动解除 `RISK_LOCKED`。初始 90 天通过后的月度白名单流水线是独立例外，使用机器签名的 `AutoIterationReport` 和固定 0.10/0.50 阶段，不伪造本节人工批准。

## 7. 封存

```bash
quantctl evidence manifest --root "$EVIDENCE_DIR" --sha256 --output "$EVIDENCE_DIR/MANIFEST.sha256"
quantctl evidence verify --manifest "$EVIDENCE_DIR/MANIFEST.sha256" --strict
quantctl evidence seal --root "$EVIDENCE_DIR" --read-only --sync-audit-remote
```

## 验收

- 实现者与 reviewer ID 不同，reviewer 使用新上下文。
- 报告通过 closed Schema、JCS hash 和所有 evidence hash 验证。
- 正常、错误、边界场景均有输入、期望、实际、证据和结论。
- P0/P1 为零，OOS 访问为零或符合第 90 天一次性授权。
- reset decision 已执行，回滚演练通过，人工批准只消费一次。
- 失败报告和旧补丁证据均保留，没有覆盖或删除。
