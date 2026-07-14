# Handoff state

Updated: `2026-07-14T08:26:34Z`

Resume in `/root/quantify/ai-quant-system`. Read `IMPLEMENTATION_STATUS.md`, ADR 0001–0003 and
`evidence/stages/M0/2026-07-14/M0_STAGE_REPORT.md`. Never modify
`/root/quantify/reference-materials`.

Current implementation head is commit `b8bc281`. M0 is not complete
or accepted. Commit `8516679` adds the executable bounded rate service, PostgreSQL v2 Reserve and
full-bind Consume, deterministic multi-class policy ingestion, idempotent outcome/observation
journals and durable 429/418 reconciliation. Commit `42624ef` adds closed gateway IPC validation,
kernel peer/caller and exact-wire binding, consume-before-single-send orchestration, SendOutcome,
and signed startup-evidence verification. Compose intentionally still runs `locked_process`: no
signed runtime catalog/evidence, production transport, or host network proof exists.
Commit `46865c3` adds same-transaction append-only Reserve and Consume decision journals; an audit
write failure rolls back the associated authority transaction.
Commit `411f4da` separates startup attestation from the host configuration trust domain and adds a
fixed-identity, owner-only Ed25519 issuance primitive that schema-validates and independently
re-verifies every signed draft. The executable measured-facts collector/service is still absent.
Commit `b8bc281` makes Compose use the frozen peer/holder identities and owner-only attestation-key
grant; services remain locked and no network or credential was activated.

Exact verification command:

```bash
cd /root/quantify/ai-quant-system && make ci && make test-migrations && make test-locked-runtime
```

Expected: CI passes 60 unit, 3 property, 2 contract and 4 security tests; migrations pass both
independent round-trips through host head `0008_decision_audit`, multi-class Reserve,
full-bind Consume, journaling, 429 reconciliation and lease gates; the no-network runtime returns
`RISK_LOCKED`.

Do not substitute `gpt-5.6-sol`, change the Testnet host allowlist, deploy this Debian development
host, add a business Binance route, add a secret to the gateway, or request production credentials.
M1 must not start before M0 implementation and fresh-context independent review are accepted.
