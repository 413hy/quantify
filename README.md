# AI Quant execution framework

This repository is now a strategy-free foundation for building a new trading system. It contains
no built-in signal generator and no active strategy campaign. It does retain a strategy-agnostic
automatic trading engine that can consume complete intents from a future project-owned decision
provider, enforce fail-closed gates and submit them through a protected execution adapter.

Current state: `FRAMEWORK_READY / AUTOMATION_ENGINE_READY / NO_BUILTIN_STRATEGY /
UNATTENDED_DISABLED / PRODUCTION_RISK_LOCKED`.

## Retained framework

- Strict market-data models, local order-book reconstruction, warm-up and archive/replay support.
- Reusable price-action and order-flow feature primitives. They calculate observations but do not
  decide or submit trades.
- Universe/ranking, Decimal cost/edge utilities, risk sizing and hard-limit validation.
- Generic order models, response classification, reconciliation, simulation and native protection
  planning.
- Strategy-agnostic automatic intent validation, idempotency, risk/cost gate and protected
  submission orchestration.
- Exact-destination Binance Testnet capability, order-lifecycle and native-protection probes.
- Read-only Testnet user-data stream observer with hash-chained evidence.
- Rate budgeting, control API, outbound notifications, monitoring, backup, validation and database
  migrations.
- Debian 12 Bookworm/aarch64 deployment and fail-closed production boundaries.

## Intentionally absent

- V4/V5 market-breadth, pullback, continuation or predictive entry rules.
- Strategy-owned position management and elapsed strategy state.
- Automatic Testnet campaign and its systemd service.
- A configured decision provider or enabled unattended automatic-trading service.
- Strategy-specific Telegram dashboard, replay sweeps, result reports and audit evidence.
- Any production activation or production credential.

The `ai_quant.strategy` package remains as an intentionally empty extension point. A new project
must define its decision provider, position policy, gate/executor adapters, tests and deployment
service before enabling unattended execution. The generic automation engine is not a strategy and
never invents an order when its input queue is empty.

## Retained services

- `aiq-testnet-secrets.service`: root-only volatile Testnet credential materialization.
- `aiq-testnet-user-stream.service`: read-only Testnet account-event evidence.

Neither currently enabled service generates orders. The previous strategy-specific campaign and
dashboard are stopped, disabled and removed. Automatic trading is a reusable library capability;
its runtime service stays absent until a new project supplies and validates the decision adapter.

## Validate

```bash
make bootstrap
make validate-debian-platform
make ci
uv run pytest -q
```

See [framework scope](docs/FRAMEWORK_SCOPE.md), [implementation status](IMPLEMENTATION_STATUS.md)
and [handoff state](HANDOFF_STATE.md). The editable three-page system view is in
[docs/architecture/trading-framework.drawio](docs/architecture/trading-framework.drawio), with
[Next AI Draw.io usage notes](docs/architecture/README.md).
