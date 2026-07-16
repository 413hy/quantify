# ADR 0040：保留无策略自动交易引擎

- 状态：已接受
- 日期：2026-07-16

## 背景

ADR 0039 删除旧 V4/V5 策略及其专用 campaign 是正确的，但把“自动交易能力”与“旧策略实现”
视为同一组件，导致框架无法直接承接新项目产生的交易决策。所有者明确要求新框架仍须具备自动
交易能力，只是不复用旧策略。

## 决策

1. 新增 `ai_quant.automation.AutomaticTradeEngine`，自动处理完整、不可变的交易意图。
2. 引擎负责时效、幂等、环境、仓位数、日亏损、紧急停止与项目门禁检查，并委托执行适配器完成
   入场及交易所原生保护。
3. 引擎不计算行情方向，不选择币种，不生成信号；决策提供器仍由新项目实现。
4. `AutomationEnvironment` 仅允许 Paper 和 Testnet，生产环境在类型边界中不存在。
5. 旧 V4/V5 campaign 不恢复。新项目经过 Paper/Testnet 验证后，应提供自己的决策、风险成本门禁、
   受保护执行适配器及默认禁用的 systemd 单元。

## 结果

框架状态改为 `AUTOMATION_ENGINE_READY / NO_BUILTIN_STRATEGY / UNATTENDED_DISABLED /
PRODUCTION_RISK_LOCKED`。自动交易能力已存在，但当前 Debian 运行环境没有启用无人值守下单服务，
不会在缺少交易意图时自行造单。
