# Strategy-free framework scope

## Purpose

Provide reusable, tested trading infrastructure without embedding a trading strategy or running an
automatic trader.

## Authority boundary

The retained framework may ingest data, calculate features, validate risk, model/order intents,
simulate execution, reconcile exchange responses, plan native protection and observe Testnet account
events. It must not decide a symbol, direction or entry time without a new project-owned decision
module.

## Retained extension points

- Market input: `ai_quant.market_data`, `ai_quant.orderbook`, `ai_quant.archive`.
- Optional observations: `ai_quant.features`. These have no order authority.
- Cost/risk: `ai_quant.cost`, `ai_quant.risk`, `ai_quant.rate_budget`.
- Execution primitives: `ai_quant.execution`, `ai_quant.binance_egress.testnet_probe`.
- Operations: `ai_quant.control`, `ai_quant.notifications`, `ai_quant.monitoring`,
  `ai_quant.backup`, `ai_quant.validation`.
- New decision code: the intentionally empty `ai_quant.strategy` package or, preferably, a separate
  new project package with an explicit dependency on this framework.

## Removed authority

The repository has no campaign loop, strategy-owned Testnet executor, strategy dashboard, strategy
result reviewer, strategy replay sweep or executable strategy core. Deployment contains no automatic
trading unit.

## Adding a new project

1. Define immutable input/output contracts for decisions.
2. Keep proposal/decision authority separate from exchange submission.
3. Add deterministic risk and net-cost rejection before any execution adapter.
4. Prove Paper behavior, then bounded Testnet behavior, before adding a disabled service unit.
5. Require explicit owner review before enabling unattended Testnet operation.
6. Keep production locked until independent production gates are complete.
