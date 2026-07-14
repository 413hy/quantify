# ADR 0011: 只读 Telegram Testnet 仪表盘

- 状态：Accepted
- 日期：2026-07-15
- 决策者：Owner（当前会话明确要求）
- 范围：当前 Binance Testnet campaign 的只读查询界面

## 背景

现有 `TelegramSender` 是严格 outbound-only 的告警通道，生产策略编排配置也固定禁止入站命令。
Owner 要求在已配置的 Telegram bot 中增加虚拟键盘，用按钮查看当前盈亏及其他常用运行信息。

## 决策

新增独立的 `aiq-telegram-dashboard.service`，使用 Telegram Bot API 的 `getUpdates` 长轮询、
`setMyCommands` 和 `ReplyKeyboardMarkup`。它与原通知发送器分离，并遵守以下边界：

实现依据为 Telegram 官方 [Bot API](https://core.telegram.org/bots/api) 和
[Bot Features](https://core.telegram.org/bots/features)：长轮询使用递增 `offset` 确认 update，
自定义回复键盘使用持久、自动缩放布局，服务端仍独立校验命令和 chat ID 授权。

- 只处理 `telegram_chat_ids` 文件中明确允许的 chat ID；未授权更新只推进 offset，不回复；
- 只读取 Testnet campaign 状态、追加式交易证据和只读用户数据流状态；
- 不挂载 Binance API key/secret、执行 socket、数据库写权限或交易配置写权限；
- 只提供当前盈亏、当前持仓、运行状态、策略统计、刷新和帮助；禁止开仓、平仓、撤单、调整
  杠杆、修改参数或批准生产；
- 使用长轮询而非 webhook；若 bot 已配置 webhook，则拒绝启动，不自动删除外部配置；
- offset 和最小服务健康状态只写入 `/var/lib/ai-quant/telegram/dashboard-state.json`，不记录
  token、消息正文或用户资料；
- 当前生产 `strategy-orchestration` 的 outbound-only 契约保持不变。本 ADR 是隔离的 Testnet
  只读界面，不赋予 Telegram 生产控制权。

## 后果

Telegram bot 会接收已授权聊天发送的文字按钮或命令，因此不再能把整个 bot 描述为完全无
入站处理；准确表述是“生产通知器 outbound-only，Testnet 仪表盘 authorized read-only”。
服务故障不影响交易活动，交易活动故障也不会由仪表盘触发任何恢复或交易动作。
