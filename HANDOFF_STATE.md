# Handoff state

Updated: `2026-07-14T13:15:15Z`

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

Business migrations now end at `0004_operations` and contain append-only market-data, risk,
execution, command, incident, notification and backup evidence. The pre-existing host-control tree
still ends at `0010_local_measurements`.

The reference acceptance sequence currently passes:

```bash
make ci test-replay test-integration test-fault test-resource
make test-migrations test-locked-runtime paper-flow
make sbom scan
```

Expected counts are 178 unit, 8 property, 2 contract, 16 security, 3 replay, 18 integration,
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
- Provision remote archive/backup destinations and prove restore.
- Collect three continuous qualified data days, freeze the signed candidate/C0, then run the
  72-hour dual validation.
- Obtain owner approvals and an independent fresh-context review.

Until those inputs exist, all real trading and deployment acceptance claims remain blocked, but the
offline implementation and deterministic Paper workflow are available for continued review.
