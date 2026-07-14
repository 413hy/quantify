# Handoff state

Updated: `2026-07-14T10:04:35Z`

Resume in `/root/quantify/ai-quant-system`. Read `IMPLEMENTATION_STATUS.md`, ADR 0001–0004,
`docs/deployment/debian-12-platform.md` and
`evidence/stages/M0/2026-07-14/M0_STAGE_REPORT.md`. Never modify
`/root/quantify/reference-materials`.

Current implementation head is commit `be6c46a`. M0 is not complete
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
Commit `53784a5` adds the root-authenticated local-facts assembly boundary. It accepts no evidence
draft, requires a fresh direct root-owned `0444` snapshot with its own JCS hash, independently
remeasures boot ID, complete artifact/release bindings and both Unix sockets, and constructs the
signable content plus exact verifier expectation. The root facts collector is still absent.
Commit `d3711e0` adds the executable issuer service. Every cycle reloads the root-protected plan,
signed trust bundle and owner-only key, proves that the keyring/trust/schema files actually used are
the files bound into the evidence, signs and atomically publishes, and refreshes no slower than 60
seconds. Handled stop/refresh failure removes the last evidence. The root collector is still absent,
and locked Compose intentionally does not activate this service.
Commit `fcbcba2` turns that deployment lock into a CI-enforced Compose rule and security test.
ADR 0004 is an owner-approved baseline amendment: Debian 12 Bookworm/aarch64 on Oracle Cloud is the
only supported host platform. It supersedes conflicting OS selections in the immutable historical
inputs without changing their bytes. `BLK-003` is resolved; the live Debian host is a deployment
candidate but still lacks the remaining deployment and independent-review evidence.

Exact verification command:

```bash
cd /root/quantify/ai-quant-system && make validate-debian-platform && make ci && make test-migrations && make test-locked-runtime
```

Expected: CI passes 97 unit, 3 property, 2 contract and 9 security tests; migrations pass both
independent round-trips through host head `0009_runtime_role`, least-privilege role checks,
multi-class Reserve,
full-bind Consume, journaling, 429 reconciliation and lease gates; the no-network runtime returns
`RISK_LOCKED`.

Do not substitute `gpt-5.6-sol`, change the Testnet host allowlist, treat OS compatibility as full
deployment approval, add a business Binance route, add a secret to the gateway, or request
production credentials.
M1 must not start before M0 implementation and fresh-context independent review are accepted.
