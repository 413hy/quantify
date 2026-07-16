# ADR 0025: Testnet 持续服务与重启对账

- 状态：Accepted
- 日期：2026-07-15
- 范围：当前 Debian 12 VPS 上的 Binance USDⓈ-M Futures Testnet

## 背景

已部署 campaign 虽由 systemd 启动，但仍使用三天运行期限并在正常完成后退出；其
`Restart=on-failure` 不会处理正常到期。Binance Testnet 凭据只存在于 `/run`，VPS 重启会
清空该目录。进程硬中断时，交易所原生止盈/止损仍然存在，但旧进程的持仓 worker 不再存在，
原实现无法补记退出结果或清理未触发的另一个 Algo 单。

## 决策

- `duration_seconds=0` 明确定义为持续运行，部署 unit 使用该模式；60 秒至 7 天仍可用于显式
  的有界测试；
- campaign、用户数据流与 Telegram 仪表盘使用 `Restart=always`，并设置重试间隔和启动频率
  上限。systemd 的显式 stop 仍然终止服务；
- 新增 root-only oneshot 服务，在 boot 时从 `/root/aiq-user-inputs/testnet/secrets/` 将凭据以
  `0400` 权限复制到 `/run/ai-quant-secrets/`；需要 Binance 凭据的两个服务必须在其后启动；
- 原生保护确认事件持久记录仓位开始时间以及止损/止盈 Algo 的 exchange ID 和 client ID；
- campaign 启动时先对账固定五币池。完整且唯一的系统原生保护组合由恢复 worker 接管；恢复
  worker 可响应最新反向确认信号，但不对继承仓位加仓。仓位已在停机期间退出时，恢复 worker
  清理 sibling Algo 并从账户成交补记手续费后结果；
- 有持仓但保护不完整、存在普通未完成订单或保护身份有歧义时，执行 fail-closed 市价平仓，
  再清理系统自身遗留订单。空仓遗留的系统订单也会清理并留证。

## 后果

VPS 重启后不再需要人工重填 `/run`，服务可以自动恢复评估、用户数据留证和 Telegram 页面。
硬重启期间的已有仓位仍依赖 Binance 原生保护；恢复只接管能严格识别的系统订单，不会声称
进程离线期间具备本地信号控制。恢复失败会由 systemd 重试，且任何不明确仓位优先安全平仓。
该决策只改善 Testnet 运行连续性，不启用生产交易，也不改变 `RISK_LOCKED` 准入状态。
