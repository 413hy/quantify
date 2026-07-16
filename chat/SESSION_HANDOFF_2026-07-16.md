# Sanitized session handoff — 2026-07-16

## Owner requirements consolidated

- Debian 12 Bookworm/aarch64 is the only supported application-host platform; earlier alternative
  distribution guidance was superseded.
- Run the full workflow on the current Oracle Cloud Testnet host and keep it available after reboot.
- Use Binance USDⓈ-M Futures Testnet until the system has sufficient evidence. Production remains
  locked and no production credential is stored.
- Fixed pool: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT and XRPUSDT.
- Allow up to five independent symbol positions, but never fill slots merely to reach five.
- Confirmed entries use market orders. Same-symbol scaling remains disabled; a fully confirmed
  opposite signal may close and replace the old direction.
- Use approximately 1 USDT margin and the symbol/account exchange-maximum initial leverage. High
  leverage does not constitute a profitability guarantee and remains subordinate to quantity,
  fee, stop and maximum-loss checks.
- Use native take-profit and stop-loss protection. Holding time is not an exit condition.
- Telegram output is Chinese, structured and concise, with leverage and PnL visibility.
- The strategy, user stream and Telegram services must operate without Codex and restart at boot.
- The owner later requested reducing the strategy evaluation rate from ten seconds to once per
  minute to reduce server pressure.
- The owner authorized synchronizing the reviewed repository and sanitized continuity package to
  `git@github.com:413hy/quantify.git`.

## Current implementation

The current deployed strategy is `TESTNET_EXPERIMENT_OF_PA_V5_6`:

- One-minute main evaluation cadence.
- Four-sample fast momentum window (about three minutes) and five-sample sustained window (about
  four minutes).
- Three-of-five fast breadth enters the state machine only with strong prediction and order flow:
  at least 3 bps aligned forecast, 0.75 aggressive-trade direction and aligned secondary flow.
- Pullback/resumption and controlled continuation are the only new-position setup families.
- Gross target is 22 bps for BTC/ETH and 25 bps for BNB/SOL/XRP, subject to at least 0.10 USDT
  estimated net target after real quantity, fees and buffer.
- Position-local failed-followthrough/giveback invalidation is checked once per minute; native
  Binance stop/target orders remain continuously active.
- No time-expiry close, no time cooldown, no market-episode total quota and no forced slot filling.
- Production request count is required to remain zero.

The V5.5 three-coin hard-coded breadth mismatch and V5.6 cadence change are documented in ADRs 0037
and 0038. Earlier V4/V5 ADRs are historical audit records and must not be rewritten as current rules.

## Services and external inputs

Enabled services:

- `aiq-testnet-secrets.service`
- `aiq-testnet-campaign.service`
- `aiq-testnet-user-stream.service`
- `aiq-telegram-dashboard.service`

Secrets remain outside Git. Testnet credentials are materialized under `/run/ai-quant-secrets/`;
Telegram owner inputs are under `/root/aiq-user-inputs/notifications/`. Do not copy those paths into
the repository.

## Last release verification

On 2026-07-16:

- `make ci`: PASS.
- 302 unit, 19 property, 2 contract and 19 security tests passed.
- Full pytest: 371 passed.
- Ruff, strict mypy over 98 source files, Bandit and secret scan passed.
- Contract, config, provenance, Compose, deployment and Debian platform validators passed.
- Debian verifier observed Bookworm/aarch64, OCI, 2 CPU, about 12 GiB RAM and 200 GB root storage.

The strategy remains explicitly unvalidated. Existing samples include losses and execution
rejections; they do not establish a win rate. Never promise that a trade will earn a fixed amount or
that the strategy can achieve 100% wins.

## Continuation order

1. Inspect repository and runtime state; do not assume this handoff is current.
2. Read `IMPLEMENTATION_STATUS.md`, `HANDOFF_STATE.md`, `docs/testnet-campaign.md` and the latest ADRs.
3. Review relevant code before modifying it.
4. Preserve Testnet-only and production-lock boundaries.
5. Run the relevant complete validation before deployment or GitHub publication.
