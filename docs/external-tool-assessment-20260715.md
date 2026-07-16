# 外部 AI 量化工具采用审查（2026-07-15）

本审查最初针对 Debian/aarch64、Binance Futures Testnet、10 秒评估和 1/5 分钟 PA/订单流；
当前 V5.6 已改为一分钟评估，工具权限结论不变。该场景仍属于
超短线场景。结论中的“不部署”是兼容性和证据决策，不代表项目质量评价。外部工具均不得
获得当前 Binance 凭据或 Testnet/生产下单权。

| 工具 | 官方定位与当前适配性 | 本轮决策 |
| --- | --- | --- |
| [Kronos](https://github.com/shiyu-coder/Kronos) | OHLCV/K 线基础模型，最小模型 4.1M 参数；官方同时明确演示回测不是生产系统，生产需要成本、滑点和风险建模。2026-07-15 已在独立虚拟环境固定源码与模型 revision，对 V4.11 六个信号做无凭据 CPU shadow；止损三单均未得到做空多数确认，但反向目标票数同样为 0/5。 | 保留为离线 shadow/advisory，不授予下单权。实际发现用于增加确定性的预测方向冲突否决、目标触达率和费用后收益风险门槛；只有足够 walk-forward 样本显著优于规则基线后，才评估接入实时附加否决。 |
| [AI-Trader](https://github.com/HKUDS/AI-Trader) | agent-native 交易平台和代理接入层，职责与本项目控制/执行面重叠。 | 不部署。外部 agent 不进入下单权威；采用“提案与执行分离”的原则，当前 V5.6 仍由确定性规则独立运行。 |
| [daily-stock-analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | A/HK/US 股票的新闻、基本面、日报和推送系统，时间尺度与币安微观结构不匹配。 | 不部署。借鉴其清晰推送思路，现有中文 Telegram 已显示仓位、杠杆、费用和净结果。 |
| [PTrade](https://ptradeapi.com/) | 券商客户端内的股票、ETF、可转债和期货接口；文档所列品种和运行环境不是 Binance Futures。 | 不部署，也不做适配层。当前没有 PTrade 券商环境，接入不会增加本策略的可验证信号。 |
| [QuantDinger](https://github.com/brokermr810/QuantDinger) | 完整 Flask/Vue/PostgreSQL/Redis/Docker 量化平台，包含自己的回测和执行链路。 | 不在同一服务器启动第二套交易执行栈，避免重复订单权威、端口和状态源。采用其“研究、回测、执行分层”理念：外部研究永不与当前执行器共用凭据。 |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | 基本面、情绪、技术、研究、交易和风险多 agent 工作流，适合较慢的研究决策。 | 不接入一分钟实时下单路径。采用持久决策日志理念；本项目 JSONL 已逐轮记录输入、候选参数、提交和结果。 |
| [Lightweight Charts](https://github.com/tradingview/lightweight-charts) | TradingView 的客户端金融图表库，本身不含行情也不生成交易信号。 | 不把 UI 库描述成 AI 决策工具。当前使用只读 Telegram；需要浏览器复盘页时再固定版本并保留 TradingView attribution。 |

## 已实际结合到当前系统的可验证原则

- Kronos 的概率预测定位促使模型输出只能先做 shadow/advisory，不能在没有本场景走步验证时
  进入下单权威；
- QuantDinger 和 AI-Trader 的分层思想落实为研究/提案与 Binance 执行凭据隔离；
- TradingAgents 的多角色记录思想落实为可审计的质量分组成和跨轮确认状态，而不是让 LLM
  在一分钟循环中自由生成订单；
- daily-stock-analysis 与 Lightweight Charts 只影响可观测性规划，不伪装成 alpha 来源；
- PTrade 不支持当前交易场所，因此没有为了“工具数量”增加无效依赖。

Kronos 已固定在独立虚拟环境，只读取历史公开 OHLCV，未启动第三方容器，也未获得 Binance
密钥。其余工具没有部署：它们不是当前一分钟超短线入口的可验证信号源，强行并入只会引入第二套
订单权威或不匹配的数据尺度。未通过足够 walk-forward 样本前，状态保持 `ADVISORY_ONLY` 或
`NOT_DEPLOYED`。
