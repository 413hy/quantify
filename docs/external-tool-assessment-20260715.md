# 外部 AI 量化工具采用审查（2026-07-15）

本审查针对当前 Debian/aarch64、Binance Futures Testnet、10 秒评估和 1/5 分钟 PA/订单流
超短线场景。结论中的“不部署”是兼容性和证据决策，不代表项目质量评价。外部工具均不得
获得当前 Binance 凭据或 Testnet/生产下单权。

| 工具 | 官方定位与当前适配性 | 本轮决策 |
| --- | --- | --- |
| [Kronos](https://github.com/shiyu-coder/Kronos) | OHLCV/K 线基础模型，最小模型 4.1M 参数；官方同时明确演示回测不是生产系统，生产需要成本、滑点和风险建模。没有当前五币 1 分钟、费用后微利场景的走步外样本。 | 不接入实时决策、不下载模型。待积累足够闭合 K 线后，在无凭据隔离进程做 shadow forecast；只有费用后 walk-forward 显著优于 V3，才能作为附加否决信号，不能直接下单。 |
| [AI-Trader](https://github.com/HKUDS/AI-Trader) | agent-native 交易平台和代理接入层，职责与本项目控制/执行面重叠。 | 不部署。外部 agent 不进入下单权威；采用“提案与执行分离”的原则，当前 V3 仍由确定性规则独立运行。 |
| [daily-stock-analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | A/HK/US 股票的新闻、基本面、日报和推送系统，时间尺度与币安微观结构不匹配。 | 不部署。借鉴其清晰推送思路，现有中文 Telegram 已显示仓位、杠杆、费用和净结果。 |
| [PTrade](https://ptradeapi.com/) | 券商客户端内的股票、ETF、可转债和期货接口；文档所列品种和运行环境不是 Binance Futures。 | 不部署，也不做适配层。当前没有 PTrade 券商环境，接入不会增加本策略的可验证信号。 |
| [QuantDinger](https://github.com/brokermr810/QuantDinger) | 完整 Flask/Vue/PostgreSQL/Redis/Docker 量化平台，包含自己的回测和执行链路。 | 不在同一服务器启动第二套交易执行栈，避免重复订单权威、端口和状态源。采用其“研究、回测、执行分层”理念：外部研究永不与当前执行器共用凭据。 |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | 基本面、情绪、技术、研究、交易和风险多 agent 工作流，适合较慢的研究决策。 | 不接入 10 秒实时路径。采用持久决策日志理念；本项目 JSONL 已逐轮记录输入、候选参数、提交和结果。 |
| [Lightweight Charts](https://github.com/tradingview/lightweight-charts) | TradingView 的客户端金融图表库，本身不含行情也不生成交易信号。 | 不把 UI 库描述成 AI 决策工具。当前使用只读 Telegram；需要浏览器复盘页时再固定版本并保留 TradingView attribution。 |

## 已实际结合到 V3 的可验证原则

- Kronos 的概率预测定位促使模型输出只能先做 shadow/advisory，不能在没有本场景走步验证时
  进入下单权威；
- QuantDinger 和 AI-Trader 的分层思想落实为研究/提案与 Binance 执行凭据隔离；
- TradingAgents 的多角色记录思想落实为可审计的质量分组成和跨轮确认状态，而不是让 LLM
  在 10 秒循环中自由生成订单；
- daily-stock-analysis 与 Lightweight Charts 只影响可观测性规划，不伪装成 alpha 来源；
- PTrade 不支持当前交易场所，因此没有为了“工具数量”增加无效依赖。

本轮没有执行第三方安装脚本、没有启动第三方容器、没有向第三方提供密钥。等出现与当前
目标匹配的数据和通过条件后，研究工具应使用独立 Unix 用户、独立虚拟环境、只读市场数据和
固定源码/模型摘要部署；未通过前，状态必须保持 `ADVISORY_ONLY` 或 `NOT_DEPLOYED`。

