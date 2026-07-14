# ADR 0003: Signed capability, kernel peer and fencing boundary

- Status: accepted for M0 development; not a runtime or deployment approval
- Date: 2026-07-14
- Implementation commit: `d5a394e21776957f627c9c3e7da78dfd1accf53c`

## Decision

Add `cryptography==49.0.0` as an exact runtime dependency and verify both host-control configuration
signatures and causal capabilities as Ed25519 signatures over the raw 32-byte
`SHA-256(RFC8785-JCS(content))` digest.

The capability trust-bundle loader validates the frozen closed schemas before use, requires
`SIGNED_RUNTIME`, recomputes the content hash, verifies its config-root signature, validity window,
revocation state, unique issuer/key/service closure and caller/protocol ACL closure. The config
verification keyring must be a regular, non-symlink, root-owned `0444` file and its recomputed hash
must equal an independently pinned expected hash. Capability issuer keys cannot act as config roots.
The checked-in engineering example remains unsigned and is therefore rejected for runtime use.

For every causal capability, verify the issuer key, signature, time window, caller/issuer scope,
operation class, environment, endpoint authority and ID, connection handle, canonical request hash,
operation-facts hash, causal reference, capability ID and nonce. The peer UID and GID come from Linux
`SO_PEERCRED`; caller claims cannot replace them. Protocol messages from the gateway and allocator
are subject to a separate message-type ACL.

Migration `0003_fencing_lease` adds a PostgreSQL-authoritative compare-and-swap epoch lease and an
append-only lease-event ledger. First acquisition or takeover after expiry increments the epoch;
same-owner renewal preserves it; a competing owner is denied. Lease TTL is bounded to 1–300 seconds
so a stale authority cannot outlive one maximum startup-evidence window. Reserve and Consume wrappers
hold the fencing row lock while invoking the existing atomic functions, so takeover cannot race a
grant or consume. Missing, expired or stale fencing denies before budget or nonce mutation.

## Rejected alternatives

- Trusting a caller-provided UID/GID or service claim: it does not establish kernel identity.
- Accepting the checked-in `UNVALIDATED_ENGINEERING_BASELINE`: it has placeholder keys and no
  signature.
- Verifying Ed25519 directly over JSON text: surface formatting would make signatures ambiguous.
- An in-process or Redis lease: it would not fence independent allocator processes durably.
- Releasing budget or nonce when the lease expires: it would violate conservative accounting.

## Consequences

The verifier and durable lease boundary are implemented and tested, but the bounded rate-budget UDS
service does not yet connect peer admission, capability verification and the database call. Signed
endpoint-catalog ingestion, gateway UDS recomputation, send outcomes and startup attestation also
remain. Therefore all runtime artifacts are still absent, the locked gateway has no transport, and
M0 remains `IN_PROGRESS / NOT_ACCEPTED / FAIL_CLOSED`.
