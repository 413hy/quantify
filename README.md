# AI Quant System implementation

This is the isolated implementation repository for the AI quantitative trading system. The immutable requirements and strategy references remain outside this repository under `/root/quantify/reference-materials/`.

Current state: the offline Paper development path and core Binance Testnet protocol flow are
implemented and verified. ADR 0005 endpoints, authenticated capability checks, actual
place/query/cancel, exchange-maximum Testnet leverage configuration, native STOP_MARKET and
TAKE_PROFIT_MARKET protection, reduce-only flatten and final zero-state reconciliation pass.
Deterministic position-exit precedence and the frozen hierarchical gross-edge lookup are now
implemented; missing calibration observations still reject entry. The external encrypted archive
roundtrip, signed decryption receipt and isolated restore probe also pass, and its host now exposes
200 GB with about 178 GB free. Historical bounded Testnet samples verified the 1 USDT margin
ceiling, native protection and zero-state cleanup, but did not verify a profitable strategy. Their
fixed-duration runner has been removed under owner-approved ADR 0006; elapsed time can no longer
close a position. Calibration, Shadow and live gates remain pending.
A continuous Testnet-only experimental campaign runs as `aiq-testnet-campaign.service`; it observes
five symbols and can hold zero to five Testnet positions in parallel with about 1 USDT margin and an
exchange-maximum-leverage policy (also specified for future production), native
structural stop/target protection and no elapsed-time exit. Position slots are capacity, not a
target. V5.6 runs the pullback and controlled-continuation state machines once per minute, plus the directional
forecast and price-structure authority after V5.1 produced 11 entries, zero targets and fee-dominated
losses. V5.5 fixed a configuration/code mismatch that left the configured 3/5 fast breadth unable
to enter the state machine. A three-coin fast context now participates, but it must have at least
3 bps forecast edge, 0.75 directional aggressive-trade strength and aligned secondary flow even
when PA appears aligned; four- and five-coin contexts retain PA-or-strong-flow authority. Weak
three-coin and weak forecast chase entries remain rejected. Gross targets are fee-sized to 22–25
bps and execution still requires at least 0.10 USDT
estimated net. Local failed-followthrough and profit-giveback exits no longer wait for a four-symbol
market reversal. All five fixed symbols may hold independently confirmed
positions at once; there is no per-round two-candidate quota, market-episode quota or time cooldown.
Instead, a persisted signal-episode identity makes each continuous one-minute signal submit only
once. Same-symbol scaling remains disabled, while a distinct fully confirmed opposite signal may
close and replace the old direction. The service, Testnet user
stream and Telegram dashboard are enabled at boot and restart
independently of Codex. Volatile runtime credentials are rematerialized from root-only persistent
inputs after boot; an interrupted native-protected Testnet position is reconciled and adopted before
new entries are considered. The strategy is explicitly unvalidated and does not change the
production `RISK_LOCKED` gate.
An independent `aiq-testnet-user-stream.service` now maintains the Testnet listen key and records
deduplicated, secret-free, hash-chained user events without exposing an order-submission method.
A separate three-symbol parallel Testnet execution stress sample
also passed final zero-state reconciliation; it is not counted as strategy evidence. No production
credential has been requested, copied, or injected; runtime remains `RISK_LOCKED` and is not
authorized for live trading.

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md), [HANDOFF_STATE.md](HANDOFF_STATE.md), and [ADR 0001](docs/adr/0001-implementation-baseline.md).
