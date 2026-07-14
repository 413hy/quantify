# 交易系统补丁独立复审检查表

> 每项填写 `PASS`、`FAIL` 或 `NOT_APPLICABLE`，并附证据 SHA-256。未填写、只写“看起来正常”或引用实现者结论均视为失败。

## 身份与范围

- [ ] patch/base release/changed-files manifest 已冻结。
- [ ] 实现者与 reviewer actor ID 不同，复审使用全新上下文。
- [ ] 所有行为变化映射到冻结需求编号，无范围外修改。
- [ ] 变更等级 C0–C4 和 reset decision 符合文档矩阵。

## 策略与成本

- [ ] PA 结构、OF 因果窗口、冲突否决、target/stop、TTL 和 setup 互斥未产生未声明变化。
- [ ] Top 10、候补、managed position、预热和历史 universe 防幸存者偏差规则成立。
- [ ] gross edge 只使用获准训练数据；Champion OOS 行和收益未读取。
- [ ] fee、双边 slippage、adverse selection、funding、failure/cancel 全部存在且未过期。
- [ ] edge 三条 Decimal 算术不变量精确成立；maker→taker 重新计算。
- [ ] 策略健康漏斗、reason-code 分布和 live/replay parity 有证据。

## 风险、执行与数据

- [ ] 风险硬上限、首日 0.10、reservation、相关簇、连亏、日损和回撤不变量成立。
- [ ] 订单 ID、append-only 状态机、UNKNOWN、部分成交、撤单竞争和重启对账通过。
- [ ] 首 fill 后 1,000ms 内原生保护；缺失时 fail-closed。
- [ ] Decimal/JCS、event-time、水位线、序列和幂等在实时/回放一致。
- [ ] 数据用途、归档、远端回执、恢复和 OOS access log 完整。

## 安全与部署

- [ ] 只有唯一 gateway 具有 Binance 路由，业务容器零 Binance 路由。
- [ ] 生产 secret 只进入 execution-service；Testnet secret 只进入 testnet-probe-runner。
- [ ] allocator、gateway、UDS、数据库任一失效时零新 Binance REST/WS API/connect/control send。
- [ ] 无真实 secret、未解释占位符、未锁依赖、浮动镜像 tag 或公网管理端口。
- [ ] 2 vCPU/12 GiB/约 200 GB 预算和队列/保留上限通过压力测试。
- [ ] 数据库迁移、备份隔离恢复、镜像/配置摘要和回滚演练通过。

## 场景证据

| 场景 | 输入 | 期望 | 实际 | 证据 SHA-256 | 结论 |
|---|---|---|---|---|---|
| 正常 |  |  |  |  |  |
| 错误 |  |  |  |  |  |
| 边界 |  |  |  |  |  |

## 缺陷与结论

- [ ] 未关闭 P0：0。
- [ ] 未关闭 P1：0。
- [ ] P2/P3 均有 requirement、owner、期限和人工决定。
- [ ] `CodexReviewReport` 通过 Schema/JCS/evidence hash 校验。
- [ ] 人工批准仍未由 Codex/CI 代替，批准只消费一次。

Reviewer verdict：`PASS | FAIL | HUMAN_DECISION_REQUIRED`

Reviewer actor ID：`<REVIEWER_ACTOR_ID>`

Report hash：`<SHA256>`
