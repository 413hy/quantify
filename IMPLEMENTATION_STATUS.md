# Implementation status

Updated: `2026-07-14T13:15:15Z`

Overall state: `TESTNET_CORE_PROTOCOL_PASS / EXTERNAL_DURATION_GATES_PENDING / RISK_LOCKED`

The trading system's offline Paper path is implemented and verified from raw market data through
native protection. This is a development completion statement, not Testnet, Shadow, calibration,
72-hour, or live authorization evidence. No real exchange order was sent.

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
- M4 operations: bounded FastAPI control surface, session-context binding, idempotent commands,
  one-use emergency-flatten challenge, outbound-only redacted notifications, Prometheus exposition,
  alert/runbook mapping, checksummed backup manifests and append-only operational migrations.
- Later-stage offline orchestration: fresh-context AI/rule authority and three-dry-run recovery,
  immutable continuous validation gates, 90-day research thresholds, FIFO/single-concurrency monthly
  iteration and quota deferral.
- Business database head is `0004_operations`; host-control head remains
  `0010_local_measurements`. Both trees pass `upgrade -> downgrade base -> upgrade` in disposable
  PostgreSQL/TimescaleDB containers.

## Verified results

- Full CI: 178 unit, 8 property, 2 contract and 16 security tests pass.
- Additional suites: 3 replay, 18 integration, 6 fault-injection and 1 resource-profile test pass.
- Ruff, strict mypy (84 source files), Bandit, secret scan, all 42 contract schemas/39 examples,
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

## Not yet claimable

The following require external facts, elapsed observation windows, credentials or human signatures
and were deliberately not fabricated:

1. Continuous User Data Stream event consumption/reconnect evidence and the complete pre-registered
   external fault/race matrix. Listen-key lifecycle and the private WebSocket upgrade pass, but a
   received `ORDER_TRADE_UPDATE`/`ALGO_UPDATE` event transcript has not yet been claimed.
2. The independent Testnet project database/Compose seal, remote encrypted backup/decrypt receipt
   and isolated restore required before discarding its facts or starting calibration.
3. A continuous qualified three-day L2 calibration dataset, signed parameter candidate and C0
   freeze.
4. Continuous 72-hour Shadow/Testnet validation, first-live 24-hour evidence and 87-day forward OOS
   results.
5. Owner signatures, production/Testnet/archive/notification credentials, remote object storage,
   off-host restore evidence and an independent fresh-context acceptance review.
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
