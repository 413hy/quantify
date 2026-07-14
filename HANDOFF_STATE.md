# Handoff state

Updated: `2026-07-14T12:57:45Z`

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

Expected counts are 176 unit, 8 property, 2 contract, 16 security, 3 replay, 18 integration,
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

- The owner approved ADR 0005 and the current official Testnet destinations are configured. Public
  REST and public/market/WS-API handshakes pass. The bounded authenticated probe exists but the
  currently supplied Demo credential receives Binance `-2015` on the first signed account call;
  replace it with a Demo/Testnet key that has Futures read/trade permission and, if enabled, the
  `140.245.75.36` IP allowlist. Never place the credential in Git or chat.
- After credential replacement, rerun `scripts/run-testnet-probe.py`; it must pass before any real
  matching-engine lifecycle test. Production credentials remain unnecessary until signed live
  arming.
- Complete actual Binance destination measurements and the still-running 24-hour generic host
  baseline.
- Provision remote archive/backup destinations and prove restore.
- Collect three continuous qualified data days, freeze the signed candidate/C0, then run the
  72-hour dual validation.
- Obtain owner approvals and an independent fresh-context review.

Until those inputs exist, all real trading and deployment acceptance claims remain blocked, but the
offline implementation and deterministic Paper workflow are available for continued review.
