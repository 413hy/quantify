# AI Quant System implementation

This is the isolated implementation repository for the AI quantitative trading system. The immutable requirements and strategy references remain outside this repository under `/root/quantify/reference-materials/`.

Current state: the offline Paper development path is implemented and verified end to end. The
current official Binance Testnet destinations are configured under ADR 0005, while the supplied
Demo credential is rejected by Binance with `-2015`; Shadow/calibration/live gates therefore remain
pending. No production credential has been requested, copied, or injected; runtime remains
`RISK_LOCKED` and is not authorized for live trading.

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md), [HANDOFF_STATE.md](HANDOFF_STATE.md), and [ADR 0001](docs/adr/0001-implementation-baseline.md).
