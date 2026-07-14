# Handoff state

Updated: `2026-07-14T15:14:06Z`

Resume in `/root/quantify/ai-quant-system`. Debian 12 Bookworm/aarch64 is the sole supported host.
Do not modify `/root/quantify/reference-materials`; the copied contract/config provenance validator
must continue to pass.

## Current code state

The complete offline Paper trading path is implemented across these packages:

- `market_data`, `orderbook`, `archive`
- `universe`, `features`, `strategy`, `cost`
- `risk`, `execution`
- `control`, `notifications`, `monitoring`, `backup`
- `orchestration`, `validation`, `research`, `iteration`
- `demo.paper_flow`

The latest review added deterministic existing-position exits, native stop/take-profit pair
planning, the frozen hierarchical gross-edge runtime lookup, a 1 USDT margin quantity ceiling and
Python-level enforcement of the immutable 10x leverage cap. The Testnet BTCUSDT risk profile is set
to 10x; this is the maximum allowed by this project, not the exchange-reported 125x maximum.

The historical SOLUSDT protocol sample used 0.92460000 USDT margin at 10x and ended at
-0.01099535 USDT net after commission, with zero remaining orders or position. Owner-approved ADR
0006 removed both elapsed-time position exits and the standalone runner that used them. This sample
is retained only as historical lifecycle evidence.

The Testnet-only `aiq-testnet-campaign.service` is enabled as an owner-authorized experimental
execution process. It evaluates SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT and ADAUSDT every ten seconds,
can hold three different symbols in parallel, uses risk-sized margin up to 10 USDT per position at
10x with a 0.35 USDT estimated loss budget, and
installs native structural stop/target protection without an elapsed-time exit. Strict baseline
production rejection remains recorded separately; experiment results are unvalidated. State is
`/var/lib/ai-quant/evidence/testnet/campaign/current/state.json`; see `docs/testnet-campaign.md`.
Telegram messages are structured Chinese text.

The reproducible structural research review in `scripts/backtest-testnet-structural.py` failed the
entry gate: the exact forward baseline had 0 eligible observations out of 679, while the no-time-
exit T1 proxy produced 2 closed samples, 0 wins and -0.0389499593 USDT at 10 USDT notional. Do not
enable Testnet order submission from this result. Evidence is
`/var/lib/ai-quant/evidence/testnet/backtest/current/structural-review.json`; new campaign records
include `mid_price` and `microprice` for future forward evaluation.

The campaign now obtains recent trades from a persistent five-symbol Testnet `aggTrade` WebSocket,
not REST polling. Only messages with valid `nq` normal quantity enter OF. The first two deployed
five-second rounds had non-zero aggressive flow for every symbol; entry still remained rejected by
PA/setup/edge gates, as intended.

The historical parallel Testnet sample ran SOLUSDT, BNBUSDT and XRPUSDT concurrently. It ended at
-0.00742656/-0.00525511/-0.00930724 USDT net and reconciled fully flat. Its elapsed-duration runner
has been deleted; the retained records are execution stress evidence, not strategy trades.

Business migrations now end at `0004_operations` and contain append-only market-data, risk,
execution, command, incident, notification and backup evidence. The pre-existing host-control tree
still ends at `0010_local_measurements`.

The reference acceptance sequence currently passes:

```bash
make ci test-replay test-integration test-fault test-resource
make test-migrations test-locked-runtime paper-flow
make sbom scan
```

Expected counts are 206 unit, 17 property, 2 contract, 17 security, 3 replay, 19 integration,
6 fault and 1 resource test. The Paper result has `external_requests=0`, `order_state=FILLED`,
`protection_healthy=true`, and `runtime_state=RISK_LOCKED`.

## Safety and activation boundary

The user asked to finish development and run the workflow before security/deployment activation.
Accordingly, the flow was completed offline, while the existing `RISK_LOCKED` and no-network
defaults were preserved. Do not reinterpret this as permission to enable production transport,
inject secrets, arm live trading, alter SSH/firewall state or manufacture time-window evidence.

No GitHub push is requested yet. The user explicitly wants repository upload deferred until the
system is complete. Local changes must be reviewed and committed before any future push.

## External work still required

- The owner approved ADR 0005 and the current official Testnet destinations are configured. The
  replacement credential passed the safe capability probe, actual GTX place/query/cancel lifecycle,
  and a minimum fill/native Algo protection/reduce-only flatten cycle. Final Testnet state is zero
  regular orders, zero Algo orders and zero position; production request count is zero. Runtime
  copies are root-owned `0400` files under `/run/ai-quant-secrets/` and must never enter Git/chat.
- Complete live User Data event consumption/reconnect evidence, the remaining pre-registered
  protocol fault/race cases, and independent project persistence/backup/seal before calibration.
- Complete actual Binance destination measurements and the still-running 24-hour generic host
  baseline.
- The external archive receiver is provisioned and its encrypted upload, remote decrypt, Parquet
  inspection, signed receipt, replay/tamper rejection and isolated restore all pass. Its Debian 11
  appliance is not an application host. Its disk and root XFS filesystem now expose 200 GB with
  about 178 GB free. Regenerate and bind formal capacity evidence before claiming the 90-day gate. Receiver deployment
  artifacts are in `deploy/archive-receiver/`; sender evidence is under
  `/var/lib/ai-quant/evidence/archive/current/`.
- Telegram input files are
  `/root/aiq-user-inputs/notifications/secrets/telegram_bot_token` and
  `/root/aiq-user-inputs/notifications/telegram_chat_ids`. They are configured outside the
  repository, remain root-only, and the live outbound probe passes.
- Collect three continuous qualified data days, freeze the signed candidate/C0, then run the
  72-hour dual validation.
- Obtain owner approvals and an independent fresh-context review.

Until those inputs exist, all real trading and deployment acceptance claims remain blocked, but the
offline implementation and deterministic Paper workflow are available for continued review.
