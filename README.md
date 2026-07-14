# AI Quant System implementation

This is the isolated implementation repository for the AI quantitative trading system. The immutable requirements and strategy references remain outside this repository under `/root/quantify/reference-materials/`.

Current state: the offline Paper development path and core Binance Testnet protocol flow are
implemented and verified. ADR 0005 endpoints, authenticated capability checks, actual
place/query/cancel, bounded Testnet leverage configuration, native STOP_MARKET and
TAKE_PROFIT_MARKET protection, reduce-only flatten and final zero-state reconciliation pass.
Deterministic position-exit precedence and the frozen hierarchical gross-edge lookup are now
implemented; missing calibration observations still reject entry. The external encrypted archive
roundtrip, signed decryption receipt and isolated restore probe also pass, and its host now exposes
200 GB with about 178 GB free. Historical bounded Testnet samples verified the 1 USDT margin
ceiling, native protection and zero-state cleanup, but did not verify a profitable strategy. Their
fixed-duration runner has been removed under owner-approved ADR 0006; elapsed time can no longer
close a position. Calibration, Shadow and live gates remain pending.
A three-day Testnet-only experimental campaign runs as `aiq-testnet-campaign.service`; it observes
five 1-USDT-feasible symbols and can hold up to three Testnet positions in parallel with native
structural stop/target protection and no elapsed-time exit. It is explicitly unvalidated and does
not change the production `RISK_LOCKED` gate.
A separate three-symbol parallel Testnet execution stress sample
also passed final zero-state reconciliation; it is not counted as strategy evidence. No production
credential has been requested, copied, or injected; runtime remains `RISK_LOCKED` and is not
authorized for live trading.

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md), [HANDOFF_STATE.md](HANDOFF_STATE.md), and [ADR 0001](docs/adr/0001-implementation-baseline.md).
