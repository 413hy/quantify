# Handoff state

Updated: `2026-07-14T09:14:24Z`

Resume in `/root/quantify/ai-quant-system`. Read `IMPLEMENTATION_STATUS.md`, ADR 0001–0003 and
`evidence/stages/M0/2026-07-14/M0_STAGE_REPORT.md`. Never modify
`/root/quantify/reference-materials`.

Current implementation head is commit `59108c9`. M0 is not complete
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
Commit `cc87fda` requires a schema-valid, exact full-bind allocator grant immediately before the
single transport call; mismatch is durably reported as `NOT_SENT` and never reaches transport.
Commit `35cfb59` enforces attested UDS inode/ownership/mode and bidirectional peer credentials, plus
the frozen shared socket groups in Compose. Runtime services are still intentionally locked.
Commit `bd79957` adds full measured-fact hashing, atomic evidence publication and per-operation
reverification, with a single-writer/read-only-consumer Compose mount. The measured-facts service
itself remains absent and the Compose command stays locked.
Commit `b9f0d32` requires an out-of-band root-owned fingerprint file beside the root-owned keyring
in `/etc/ai-quant/trust`; the release cannot replace it through an environment value or writable
mount. Provisioning this file is a deployment prerequisite, not performed on this dev host.
Commit `c586fef` rejects duplicate JSON/YAML keys and supplies a race-aware exact artifact hash
verifier. Release-manifest and real signed deployment inputs remain absent.
Commits `ead4d40` and `59108c9` record the implementation-context code review and fixes. Private
service files now share a race-aware `0400`/current-UID/out-of-repository loader; endpoint source
artifacts use the exact verifier; the allocator builds a fixed least-privilege database target from
a password file. Host migration `0009_runtime_role` creates a `NOLOGIN`, non-superuser runtime role,
revokes public function execution and hardens function search paths. The locked Compose service no
longer receives the database bootstrap password. UDS one-way messages require a post-handler commit
ACK, and gateway outcome failure latches closed. This implementer review is not the required
fresh-context independent acceptance review.

Exact verification command:

```bash
cd /root/quantify/ai-quant-system && make ci && make test-migrations && make test-locked-runtime
```

Expected: CI passes 96 unit, 3 property, 2 contract and 8 security tests; migrations pass both
independent round-trips through host head `0009_runtime_role`, least-privilege role checks,
multi-class Reserve,
full-bind Consume, journaling, 429 reconciliation and lease gates; the no-network runtime returns
`RISK_LOCKED`.

Do not substitute `gpt-5.6-sol`, change the Testnet host allowlist, deploy this Debian development
host, add a business Binance route, add a secret to the gateway, or request production credentials.
M1 must not start before M0 implementation and fresh-context independent review are accepted.
