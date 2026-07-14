# Implementation status

Updated: `2026-07-14T15:44:06Z`

Overall state: `TESTNET_CORE_PROTOCOL_PASS / EXTERNAL_DURATION_GATES_PENDING / RISK_LOCKED`

The trading system's offline Paper path is implemented and verified from raw market data through
native protection. This is a development completion statement, not completed Testnet, Shadow,
calibration, 72-hour, or live authorization evidence. No production exchange order was sent.

## Implemented

- M0 foundation: immutable contracts/configuration, Debian 12 Bookworm/aarch64 platform amendment,
  independent databases, host rate authority, bounded gateway contracts, startup evidence and
  fail-closed no-network runtime remain intact.
- M1 market data: strict raw depth/trade records, snapshot plus `U/u/pu` reconstruction, duplicate
  handling, whole-book invalidation on gaps/crossed/empty books, warm-up gate, hourly UTC
  Parquet/Zstd, append-only daily manifests, Ed25519 remote receipts, verified-only retention and
  deterministic archive replay.
- M2 strategy: exact Decimal Type-7 Top-10 ranking, two-confirmation/60-minute/5.00 hysteresis,
  managed positions, closed-bar PA primitives, normal-quantity-only Order Flow, PA/OF conflict
  rejection, 1000ms signals, all-in net-edge evaluation and a shared live/replay strategy core.
- M3 risk/execution: multiplier-aware hard limits, floor-to-step sizing, STANDARD/ALGO namespaces,
  append-only order projection, exact Algo status mapping, response classification, 5-second UNKNOWN
  reconciliation, conservative fills, 1000ms protection monitoring and restart reconciliation.
- Existing-position management applies the owner-amended exit order: kill/protection/account, hard
  stop/risk, PA invalidation, OF reversal/exhaustion and structural target. Elapsed time is not an
  exit condition. Every active exit is full reduce-only taker and cannot increase exposure.
- Native protection planning now produces the opposite-side close-all Algo pair: `STOP_MARKET` plus
  `TAKE_PROFIT_MARKET`, with strict long/short stop-entry-target structure validation.
- The runtime `SHRUNK_MARKOUT_CELL_MEAN_V1` lookup now uses the frozen parent order, signed Decimal
  means, minimum observation thresholds and deterministic shrinkage. Missing exact and parent
  support returns `NET_EDGE_EVIDENCE_INCOMPLETE`; it never invents zero edge.
- Risk hard caps are validated inside Python as well as Schema. A direct 100x/125x caller is rejected,
  and a per-order margin budget is converted to a floor-quantized quantity ceiling. The current
  requested operating ceiling is 1 USDT of margin and remains subordinate to all risk/edge gates.
- The earlier bounded Testnet micro-position and parallel pressure runners have been removed under
  ADR 0006 because they used elapsed time as an exit. Their historical evidence remains auditable,
  but no current command can open a position through that non-strategy path.
- A three-day Testnet campaign service applies the checked-in unvalidated PA baseline across five
  1-USDT-feasible symbols using closed 1m/5m bars, the latest 20-level book and the 500ms
  aggregate-trade window. It records every decision while observing up to three symbols
  concurrently. The service is now mechanically `OBSERVATION_ONLY`: PA/OF confirmation alone does
  not produce a full setup, structural stop/target or signed gross-edge horizon, so entry remains
  `REJECT` with explicit incomplete-plan reason codes. This is a wider forward-sample pool, not
  fabricated Top10 or strategy execution evidence.
- A reproducible no-time-exit T1 structural research backtest now consumes current Testnet klines,
  actual per-symbol taker fees and conservative slippage. The first five-symbol review failed the
  research gate: 2 closed samples, 0 wins and -0.0389499593 USDT at 10 USDT notional. The exact
  forward baseline also had 0 eligible observations out of 679. Machine evidence is under
  `/var/lib/ai-quant/evidence/testnet/backtest/current/`; the campaign remains observation-only.
- Testnet Order Flow collection now uses one persistent exact-host combined `aggTrade` WebSocket
  for all five symbols. It validates and retains only events with finite non-negative Binance `nq`
  normal quantity and evaluates a rolling five-second window. A live post-deployment check produced
  non-zero aggressive flow for all 10 observations across two complete rounds, replacing the
  mostly-empty ten-second REST polling snapshots.
- M4 operations: bounded FastAPI control surface, session-context binding, idempotent commands,
  one-use emergency-flatten challenge, outbound-only redacted notifications, Prometheus exposition,
  alert/runbook mapping, checksummed backup manifests and append-only operational migrations.
- External archive receiver: chrooted key-only SFTP, separate no-login processor, age/X25519
  decryption, ciphertext/plaintext hash binding, remote Parquet inspection, Ed25519 schema `1.1.0`
  receipts, replay rejection and sender-side pinned host/receipt keys. The legacy schema `1.0.0`
  receipt remains compatible but cannot satisfy the stronger remote-decryption gate by itself.
- Telegram delivery now has a concrete outbound-only HTTPS sender loaded from root-only token and
  chat-ID files. Messages use a structured Chinese format and Beijing time; trade results include
  entry/protection prices, realized PnL, fees, net result, protection latency and final exposure.
  No update polling or inbound command surface is implemented.
- Later-stage offline orchestration: fresh-context AI/rule authority and three-dry-run recovery,
  immutable continuous validation gates, 90-day research thresholds, FIFO/single-concurrency monthly
  iteration and quota deferral.
- Business database head is `0004_operations`; host-control head remains
  `0010_local_measurements`. Both trees pass `upgrade -> downgrade base -> upgrade` in disposable
  PostgreSQL/TimescaleDB containers.

## Verified results

- Full CI: 206 unit, 17 property, 2 contract and 17 security tests pass.
- Additional suites: 3 replay, 19 integration, 6 fault-injection and 1 resource-profile test pass.
- Ruff, strict mypy (92 source files), Bandit, secret scan, all 42 contract schemas/39 examples,
  14 config examples, provenance, Compose and Debian deployment validators pass.
- Runtime dependency audit covers 45 packages and reports zero known vulnerabilities. A reproducible
  CycloneDX SBOM and audit JSON are under `evidence/build/current/`.
- Debian verifier passes on Debian 12 Bookworm, aarch64, 2 vCPU, approximately 12 GiB RAM and OCI.
- Container runtime test returns `RISK_LOCKED`, `new_egress_allowed=false`, `network=none`.
- `make paper-flow` deterministically produces a BTCUSDT Paper signal, approves quantity `1.9`,
  fills it conservatively, confirms full native protection, performs zero external requests and
  leaves runtime state `RISK_LOCKED`.
- ADR 0005 resolves the frozen Testnet hostname conflict using the current Binance official
  destinations. Public Testnet `/time` and `/exchangeInfo` requests pass from the Debian host, and
  routed `/public` and `/market` streams complete HTTP 101 upgrades. The gateway now allows only
  the exact current Testnet authority/host pairings and continues to reject production hosts for
  Testnet callers.
- A bounded Testnet capability probe now validates secret-file metadata, server time,
  `exchangeInfo`, account mode, symbol margin configuration, clean account state, a non-matching
  engine `/order/test`, listen-key lifecycle and all four WebSocket routes without logging secrets.
  After the credential was replaced, the real probe passed for 724 Testnet symbols, one-way/Cross
  account configuration, a clean account, `/order/test`, listen-key create/private-upgrade/close
  and all routed WebSocket endpoints. It sent zero production requests and created zero
  matching-engine orders. Redacted evidence is at
  `/var/lib/ai-quant/evidence/testnet/current/safe-capability-probe.json`.
- The real matching-engine lifecycle passed: one far-from-market BTCUSDT GTX order reached `NEW`,
  query agreed, cancel reached `CANCELED`, and final reconciliation reported zero orders and zero
  positions. A separate minimum-size fill/protection cycle filled the entry, confirmed a native
  STOP_MARKET Algo order in 365ms against the 1,000ms limit, flattened with a reduce-only market
  order and finished with zero regular orders, zero Algo orders and zero position. Neither flow
  contacted a production endpoint. Evidence is in
  `/var/lib/ai-quant/evidence/testnet/current/{order-lifecycle,native-protection}.json`.
- The Testnet risk-profile probe queried the current account fee and leverage-bracket facts, then set
  BTCUSDT to the project hard cap of 10x (exchange-reported maximum 125x). It created no matching
  order and ended flat. A subsequent minimum-fill cycle confirmed both native Algo orders: stop in
  387ms and take-profit in 626ms, then reduce-only flattened and reconciled zero ordinary orders,
  zero Algo orders and zero position. Evidence is under
  `/var/lib/ai-quant/evidence/testnet/current/{risk-profile,native-protection-pair}.json`.
- One historical bounded SOLUSDT sample used 0.92460000 USDT initial margin at 10x, confirmed the
  native stop in 371ms and take-profit in 609ms, and was closed by the now-retired duration rule.
  The target was not reached: realized PnL was -0.00359999 USDT, commission was 0.00739536 USDT and
  net PnL was -0.01099535 USDT. Final ordinary orders, Algo orders and position were all zero, with
  zero production endpoint requests. Evidence is at
  `/var/lib/ai-quant/evidence/testnet/current/sol-micro-scalp.json`.
- A historical three-symbol parallel Testnet execution sample ran SOLUSDT, BNBUSDT and XRPUSDT with
  independent 1-USDT margin ceilings. All three completed their native protection lifecycle and
  reconciled zero orders/Algo orders/positions. None hit stop or target; the retired runner closed
  them by elapsed duration. Net results were -0.00742656, -0.00525511 and -0.00930724 USDT respectively, mostly
  commission. The sample is explicitly classified `EXECUTION_STRESS_NOT_STRATEGY_SIGNAL`; its
  Chinese per-trade and aggregate Telegram notifications passed. Evidence is under
  `/var/lib/ai-quant/evidence/testnet/parallel/20260714-sample-01/`.
- `aiq-testnet-campaign.service` is enabled for a three-day Testnet observation. It is
  observation-only and cannot submit orders until the document-required setup state, structural
  exit plan and signed gross-edge horizon exist; the account remains at zero regular orders, zero
  Algo orders and zero position.
  State and append-only observations are under
  `/var/lib/ai-quant/evidence/testnet/campaign/current/`.
- A real external archive roundtrip to the isolated receiver passed. The sender encrypted an exact
  L2 Parquet object with age/X25519; the remote endpoint recomputed its ciphertext hash, decrypted
  it, matched the plaintext hash, opened 21 Parquet columns, matched one row and schema `1.0.0`, and
  returned an Ed25519-signed receipt. Exact verification passed while replay and tamper probes were
  rejected. A second independent decrypt/read/hash probe passed and removed its temporary
  plaintext. Evidence is under `/var/lib/ai-quant/evidence/archive/current/`.

## Not yet claimable

The following require external facts, elapsed observation windows, credentials or human signatures
and were deliberately not fabricated:

1. Continuous User Data Stream event consumption/reconnect evidence and the complete pre-registered
   external fault/race matrix. Listen-key lifecycle and the private WebSocket upgrade pass, but a
   received `ORDER_TRADE_UPDATE`/`ALGO_UPDATE` event transcript has not yet been claimed.
2. The independent Testnet project database/Compose seal required before discarding its facts or
   starting calibration. The remote encrypted Parquet/decrypt-receipt/isolated-restore mechanism is
   proven. The receiver disk is now 200 GB with about 178 GB free; the capacity evidence still needs
   to be regenerated and bound to the next project seal before the formal gate can be claimed.
3. A continuous qualified three-day L2 calibration dataset, signed parameter candidate and C0
   freeze.
4. Continuous 72-hour Shadow/Testnet validation, first-live 24-hour evidence and 87-day forward OOS
   results.
5. Owner signatures, production credentials and an independent fresh-context acceptance review.
   an independent fresh-context acceptance review. Testnet and archive transport credentials are
   configured outside the repository.
6. Production activation. It remains unauthorized and locked.

These are validation/deployment inputs, not hidden unfinished offline implementation. The software
will continue to reject new real orders until the corresponding gates are supplied and passed.

## Reproduce

```bash
cd /root/quantify/ai-quant-system
make ci test-replay test-integration test-fault test-resource
make test-migrations test-locked-runtime paper-flow
make sbom scan
```
