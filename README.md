# AI Quant System implementation

This is the isolated implementation repository for the AI quantitative trading system. The immutable requirements and strategy references remain outside this repository under `/root/quantify/reference-materials/`.

Current state: the offline Paper development path and core Binance Testnet protocol flow are
implemented and verified. ADR 0005 endpoints, authenticated capability checks, actual
place/query/cancel, bounded Testnet leverage configuration, native STOP_MARKET and
TAKE_PROFIT_MARKET protection, reduce-only flatten and final zero-state reconciliation pass.
Deterministic position-exit precedence and the frozen hierarchical gross-edge lookup are now
implemented; missing calibration observations still reject entry. The external encrypted archive
roundtrip, signed decryption receipt and isolated restore probe also pass, and its host now exposes
200 GB with about 178 GB free. Time-based calibration, Shadow and live gates remain pending. No production
credential has been requested, copied, or injected; runtime remains `RISK_LOCKED` and is not
authorized for live trading.

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md), [HANDOFF_STATE.md](HANDOFF_STATE.md), and [ADR 0001](docs/adr/0001-implementation-baseline.md).
