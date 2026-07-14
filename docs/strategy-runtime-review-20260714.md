# Strategy/runtime review — 2026-07-14

> Historical review. Its 10x conclusion was explicitly corrected by the owner on 2026-07-15;
> ADR 0009 now requires dynamic `EXCHANGE_MAXIMUM` in Testnet and future Production.

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
- The historical schema capped leverage at 10x, but a direct Python caller could construct a
  higher `ConfiguredRiskLimits` value. ADR 0009 removed that project cap;
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
- At the time of this review, Python-level risk construction rejected every configured hard-cap
  breach. The then-current margin-budget helper floored quantity to exchange step size and rejected
  leverage above 10x. ADR 0009 later replaced this with current exchange-maximum selection.
- The historical Testnet risk-profile probe read bracket/commission facts and applied the former
  bounded project leverage. On BTCUSDT Testnet it observed exchange maximum 125x, applied 10x,
  maker 0.0200% and taker 0.0400%, with no matching order.
- A real Testnet minimum-fill cycle confirmed the stop at 387ms and take-profit at 626ms, flattened
  reduce-only and ended with zero ordinary orders, zero Algo orders and zero position.

## Activation conclusion

The reviewed code is suitable for continued locked Paper/Testnet validation, not production
activation. There is no signed three-day calibration table yet, so the correct runtime result for a
new entry is `NET_EDGE_EVIDENCE_INCOMPLETE`. Starting a process that bypasses that result or setting
At the time, setting 125x merely because the exchange accepted it would have contradicted the then
active 10x risk contract. ADR 0009 subsequently changed the leverage policy, but did not waive the
missing calibration and net-edge evidence gates.

Small repeated profits remain a valid research objective only after all-in cost. At 125 USDT
notional, two taker fills at the observed 0.0400% rate cost about 0.10 USDT before slippage. Under
the former 10x policy, a 1 USDT margin budget represented at most about 10 USDT notional; a 0.30 USDT
net target required roughly a 3.08% favorable move before additional slippage. The current
implementation still treats 1 USDT as a maximum margin allocation and derives target/entry
eligibility from net edge rather than forcing a fixed PnL.

## Bounded Testnet micro-position result

The historical attended runner selected SOLUSDT because its Testnet filters admitted a market quantity
inside the 1 USDT margin ceiling at the then-active 10x policy. One real cycle used 0.12 SOL at a 77.050000 USDT fill,
0.92460000 USDT initial margin, a 76.3000 stop trigger and a 77.9700 target trigger. Native stop and
take-profit confirmations arrived in 371ms and 609ms respectively.

Price did not reach either trigger during the historical sample window, so the old runner flattened
reduce-only. ADR 0006 subsequently removed that elapsed-time exit and the runner itself. Realized
PnL was -0.00359999 USDT, commission was 0.00739536 USDT and final
net PnL was -0.01099535 USDT. The target was not achieved. Reconciliation proved zero regular
orders, zero Algo orders, zero position and zero production endpoint requests. This verifies the
bounded execution lifecycle; it does not verify a profitable entry signal or justify unattended
repetition.
