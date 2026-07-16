# Implementation status

Updated: `2026-07-16T02:45:14Z`

Overall state: `FRAMEWORK_READY / NO_STRATEGY / NO_AUTO_TRADING / PRODUCTION_RISK_LOCKED`

## Reset outcome

The previous V4/V5 Testnet strategy product has been removed so this repository can be used as a
clean execution framework for a new project. The deletion includes the strategy state machine,
strategy-owned Testnet executor, automated campaign, strategy Telegram dashboard, replay/result
tools, strategy tests, strategy ADRs and repository-local strategy evidence.

The old automated campaign and dashboard were stopped and disabled while the account had no active
position or pending entry. Their installed systemd units were removed. Historical runtime evidence
under `/var/lib/ai-quant/evidence/testnet/campaign/` was not altered; it is outside the source
repository and can be archived separately if desired.

## Retained capabilities

- Debian 12 Bookworm/aarch64 platform validation and locked deployment controls.
- Market-data contracts, order-book reconstruction, archive/retention and deterministic replay.
- Reusable PA and Order Flow feature calculators without decision authority.
- Universe ranking/membership and fee/net-edge utilities.
- Generic risk sizing, exchange filter handling and maximum-loss validation.
- Order intent/state models, Binance response classification, UNKNOWN reconciliation, conservative
  simulator and native stop/take-profit planning.
- Binance Testnet safe capability, order lifecycle, risk profile and native-protection probes.
- Read-only Testnet user stream and root-only volatile credential materializer.
- Control, notification, monitoring, backup, orchestration, iteration and validation components.
- Business and host-control database migration trees.

## Deliberately missing

- No strategy implementation exists under `ai_quant.strategy` beyond an empty extension package.
- No executable module chooses symbols, directions, entries, targets or exits.
- No unattended service has order-submission authority.
- No strategy-specific dashboard or PnL/win-rate report remains.
- No production transport or credential is enabled; production remains `RISK_LOCKED`.

## Framework validation

Post-reset validation on Debian 12/aarch64:

- Full pytest: 253 passed.
- CI suites: 190 unit, 19 property, 2 contract and 17 security tests passed.
- Ruff passed; strict mypy passed over 86 source files; Bandit and secret scan passed.
- Contract/config/provenance/Compose checks passed.
- Deployment validation passed with two retained Testnet services and no strategy unit.
- Debian host validation passed on the 2-vCPU, approximately 12-GiB, 200-GB OCI host.

Reproduce:

```bash
make validate-debian-platform
make ci
uv run pytest -q
```

The authoritative retained scope and extension boundary are documented in
`docs/FRAMEWORK_SCOPE.md` and ADR 0039.

## New-project requirements

A future project must provide, as new code rather than hidden configuration:

1. A versioned signal/decision contract.
2. Explicit entry, exit and position-ownership rules.
3. Fee/slippage-aware expected-value and risk gates.
4. Deterministic unit, replay and fault tests.
5. A separate deployment service that remains disabled until Testnet review passes.
6. New evidence and documentation that do not reuse old strategy win/loss claims.

Runtime credentials, Telegram inputs, server passwords, raw Codex state and account-identifying
evidence must remain outside Git.
