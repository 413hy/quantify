# AI Quant execution framework

This repository is now a strategy-free foundation for building a new trading system. It contains
no automated signal generator, no active strategy campaign and no service capable of autonomously
opening trades.

Current state: `FRAMEWORK_READY / NO_STRATEGY / NO_AUTO_TRADING / PRODUCTION_RISK_LOCKED`.

## Retained framework

- Strict market-data models, local order-book reconstruction, warm-up and archive/replay support.
- Reusable price-action and order-flow feature primitives. They calculate observations but do not
  decide or submit trades.
- Universe/ranking, Decimal cost/edge utilities, risk sizing and hard-limit validation.
- Generic order models, response classification, reconciliation, simulation and native protection
  planning.
- Exact-destination Binance Testnet capability, order-lifecycle and native-protection probes.
- Read-only Testnet user-data stream observer with hash-chained evidence.
- Rate budgeting, control API, outbound notifications, monitoring, backup, validation and database
  migrations.
- Debian 12 Bookworm/aarch64 deployment and fail-closed production boundaries.

## Intentionally absent

- V4/V5 market-breadth, pullback, continuation or predictive entry rules.
- Strategy-owned position management and elapsed strategy state.
- Automatic Testnet campaign and its systemd service.
- Strategy-specific Telegram dashboard, replay sweeps, result reports and audit evidence.
- Any production activation or production credential.

The `ai_quant.strategy` package remains as an intentionally empty extension point. A new project
must define its own signal contract, position policy, tests and deployment service before it can
request execution.

## Retained services

- `aiq-testnet-secrets.service`: root-only volatile Testnet credential materialization.
- `aiq-testnet-user-stream.service`: read-only Testnet account-event evidence.

Neither service generates orders. The previous campaign and strategy dashboard are stopped,
disabled and removed from the deployment package.

## Validate

```bash
make bootstrap
make validate-debian-platform
make ci
uv run pytest -q
```

See [framework scope](docs/FRAMEWORK_SCOPE.md), [implementation status](IMPLEMENTATION_STATUS.md)
and [handoff state](HANDOFF_STATE.md).
