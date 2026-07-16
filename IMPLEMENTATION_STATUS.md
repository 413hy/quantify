# Implementation status

Updated: `2026-07-16T02:20:22Z`

Overall state: `TESTNET_CONTINUOUS_EXPERIMENT_RUNNING / STRATEGY_UNVALIDATED / PRODUCTION_RISK_LOCKED`

This file describes the current implementation and deployed Testnet experiment. It is not a claim
of profitability, production readiness, completed calibration, 72-hour validation or live-trading
authorization.

## Supported platform

- Debian 12 Bookworm on aarch64 is the sole supported application-host platform.
- The verified Oracle Cloud host has 2 vCPU, about 12 GiB RAM and a 200 GB root filesystem.
- Debian 12 instructions are the only supported application-host instructions. Historical platform
  discussions remain only in immutable source material and ADR history.
- Deployment validation remains fail-closed and production transport remains `RISK_LOCKED`.

## Implemented system

- Offline Paper flow, raw market-data validation, order-book reconstruction, PA/Order Flow
  features, Decimal sizing, fee/slippage-aware edge checks, risk controls, execution state machine,
  native protection, notifications, monitoring, backup, research and orchestration are implemented.
- Binance USDⓈ-M Futures Testnet uses the owner-approved exact endpoints in ADR 0005. Production
  hosts are not used by the Testnet campaign.
- Testnet execution supports market entry, exchange-maximum initial leverage, approximately 1 USDT
  margin per position, a maximum 1 USDT estimated net-loss budget, and exchange-native
  `STOP_MARKET` plus `TAKE_PROFIT_MARKET` protection.
- Elapsed holding time is not an exit condition. Positions close through native target/stop,
  deterministic signal invalidation, confirmed reversal, execution fail-closed handling or an
  explicit operator stop.
- The fixed universe is BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT and XRPUSDT. Zero to five independent
  symbols may be held; five is capacity, not a target. Same-symbol scaling remains disabled.
- Telegram notifications are outbound-only, Chinese, structured and secret-free. The read-only
  dashboard exposes useful status/PnL controls without granting order authority.
- A separate Testnet user-data service maintains the private listen key, reconnects, deduplicates
  supported events and writes a hash-chained journal without exposing an order-submission method.
- Root-only persistent inputs rematerialize volatile Testnet credentials after boot. Campaign,
  user stream, Telegram dashboard and secret materializer are enabled under systemd and restart
  independently of Codex.

## Current strategy: V5.6

The running strategy identifier is `TESTNET_EXPERIMENT_OF_PA_V5_6`.

- The deterministic campaign evaluates once per minute to reduce CPU and Binance request pressure.
- Fast breadth uses a four-sample window (about three minutes); sustained breadth uses five samples
  (about four minutes). Full warm-up is five rounds.
- A 3/5 fast context is admitted only with strong predictive and order-flow authority. It requires
  at least 3 bps aligned forecast, 0.75 directional aggressive-trade strength, and aligned book or
  microprice evidence. Four/five-symbol contexts retain PA-or-strong-flow authority.
- Controlled entries are pullback/resumption or continuation plans. Current spread, hourly veto,
  target feasibility, fee-adjusted target and risk sizing are checked before submission.
- Gross targets are 22 bps for BTC/ETH and 25 bps for BNB/SOL/XRP. Execution still requires at
  least 0.10 USDT estimated net target after actual quantity, both taker fees and slippage buffer.
- Pullback state lasts at most ten rounds (about ten minutes); evidence may latch for two rounds.
  Continuation and local position-opposition checks use one minute-round so the slower polling does
  not add another mechanical multi-minute delay.
- Native stop/target orders remain live at Binance continuously and do not depend on the one-minute
  strategy loop.
- The complete current rule and deployment interface are documented in
  `docs/testnet-campaign.md` and ADRs 0032–0038.

## Current validation

Release checks run on the Debian application host on 2026-07-16:

- `make ci`: PASS.
- Unit tests: 302 passed.
- Property tests: 19 passed.
- Contract tests: 2 passed.
- Security tests: 19 passed.
- Full repository pytest run: 371 passed.
- Ruff: PASS; strict mypy: 98 source files, PASS; Bandit and repository secret scan: PASS.
- Contracts: 42 schemas, 39 instances, 26 JCS cases and 1 OpenAPI document, PASS.
- Configuration: 14 examples, no embedded secrets, PASS.
- Provenance: 123 copied files and 9 owner amendments, PASS.
- Compose/static deployment policy: PASS for four systemd services, Debian 12 locked bootstrap and
  no PostgreSQL TCP exposure.
- Debian host validation: Debian 12 Bookworm/aarch64, OCI, cgroup v2, 2 CPU, about 12 GiB memory and
  200 GB root storage, PASS.

## Runtime evidence and honest result boundary

- Campaign state: `/var/lib/ai-quant/evidence/testnet/campaign/current/state.json`.
- Append-only observations/results:
  `/var/lib/ai-quant/evidence/testnet/campaign/current/observations.jsonl`.
- Previous strategy versions are retained under
  `/var/lib/ai-quant/evidence/testnet/campaign/archive/`.
- User-stream evidence is under `/var/lib/ai-quant/evidence/testnet/user-stream/current/`.
- Current and historical samples are insufficient and have not established a profitable strategy.
  Zero/low sample counts, losing samples and rejected candidates must not be presented as a verified
  win rate.
- No production endpoint request or production order is authorized by this experiment. Production
  remains `RISK_LOCKED`.

## External gates not yet claimable

1. A qualified continuous L2 calibration dataset, signed candidate and C0 freeze.
2. Sufficient out-of-sample Testnet/Shadow evidence, including the required duration gates.
3. Complete user-stream keepalive/rotation and remaining pre-registered fault/race matrix.
4. Independent fresh-context acceptance and owner production approval.
5. Production credentials, activation and first-live evidence. These have not been requested or
   stored in the repository.

## Reproduce

```bash
make bootstrap
make validate-debian-platform
make ci
uv run pytest -q
make test-migrations
make test-locked-runtime
make paper-flow
```

Runtime credentials, Telegram tokens, passwords, `/run/ai-quant-secrets`, `/root/aiq-user-inputs`
and raw Codex state must never be committed.
