# Strategy/runtime review — 2026-07-14

## Review result

The repository was reviewed against the active owner requests, immutable strategy/risk documents,
contracts and the executable code before further implementation. The prior status was accurate for
the offline Paper and bounded Testnet protocol probes, but it was not a continuously running or
live-authorized trading system. `realtime-engine`, `execution-service` and the gateway deployment
commands still intentionally launch `locked_process`; calibration, Shadow and live time gates have
not elapsed.

Material strategy gaps found during the review were:

- no deterministic existing-position decision function implementing the documented exit priority;
- no native take-profit plan paired with the already implemented native stop monitor;
- no runtime lookup for `SHRUNK_MARKOUT_CELL_MEAN_V1` and therefore no mechanical rejection path for
  sparse gross-edge cells;
- Schema capped leverage at 10x, but a direct Python caller could construct a higher
  `ConfiguredRiskLimits` value;
- no explicit floor-quantized per-order margin ceiling for the owner's approximately 1 USDT order
  budget;
- the Testnet proof covered a native stop, but not a simultaneous native stop/take-profit pair.

## Implemented resolution

- `ai_quant.strategy.position` now implements the fixed six-level exit precedence. Existing native
  protection is retained during a transient data fault; unhealthy protection forces a full
  reduce-only taker exit.
- `build_native_protection_plan` creates opposite-side close-all `STOP_MARKET` and
  `TAKE_PROFIT_MARKET` Algo intents and rejects invalid long/short price structure.
- `ai_quant.cost.gross_edge` implements the frozen parent order and observation thresholds with
  exact Decimal shrinkage. No exact/parent evidence means no entry.
- Python-level risk construction rejects every configured hard-cap breach. The margin-budget helper
  floors quantity to exchange step size and rejects leverage above 10x.
- The Testnet risk-profile probe reads current bracket/commission facts and applies only the bounded
  project leverage. On the current BTCUSDT Testnet account it observed exchange maximum 125x,
  applied 10x, maker 0.0200% and taker 0.0400%, with no matching order.
- A real Testnet minimum-fill cycle confirmed the stop at 387ms and take-profit at 626ms, flattened
  reduce-only and ended with zero ordinary orders, zero Algo orders and zero position.

## Activation conclusion

The reviewed code is suitable for continued locked Paper/Testnet validation, not production
activation. There is no signed three-day calibration table yet, so the correct runtime result for a
new entry is `NET_EDGE_EVIDENCE_INCOMPLETE`. Starting a process that bypasses that result or setting
125x merely because the exchange accepts it would contradict the immutable 10x risk contract and
would turn missing evidence into an unreviewed trading strategy.

Small repeated profits remain a valid research objective only after all-in cost. At 125 USDT
notional, two taker fills at the observed 0.0400% rate cost about 0.10 USDT before slippage. At the
project's 10x limit, a 1 USDT margin budget represents at most about 10 USDT notional; a 0.30 USDT
net target would require roughly a 3.08% favorable move before additional slippage, so it is not a
credible fixed ultra-short target. The implementation therefore treats 1 USDT as a maximum margin
allocation and derives target/entry eligibility from net edge rather than forcing a fixed PnL.
