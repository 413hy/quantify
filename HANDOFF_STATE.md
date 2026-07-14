# Handoff state

Updated: `2026-07-14T06:57:33Z`

Resume in `/root/quantify/ai-quant-system`. Read `IMPLEMENTATION_STATUS.md`, ADR 0001, ADR 0002 and
`evidence/stages/M0/2026-07-14/M0_STAGE_REPORT.md`. Never modify
`/root/quantify/reference-materials`.

Current immutable implementation baseline is commit
`3a5762e37a5311f0a7faeca2e93b6c77ab8500ff`. M0 is not complete or accepted. The next engineering
unit is durable atomic Reserve allocation with signed capability/nonce reservation and kernel peer
identity; the existing `consume_permit` database function and locked gateway must remain
fail-closed.

Exact verification command:

```bash
cd /root/quantify/ai-quant-system && make ci && make test-migrations && make test-locked-runtime
```

Expected: CI passes 14 unit, 3 property, 2 contract and 2 security tests; migrations pass both
independent round-trips and first-grant/replay-deny checks; the no-network runtime returns
`RISK_LOCKED`.

Do not substitute `gpt-5.6-sol`, change the Testnet host allowlist, deploy this Debian development
host, add a business Binance route, add a secret to the gateway, or request production credentials.
M1 must not start before M0 implementation and fresh-context independent review are accepted.

