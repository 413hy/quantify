# Implementation status

Updated: `2026-07-14T08:51:35Z`

Overall state: `M0_IN_PROGRESS / NOT_ACCEPTED / FAIL_CLOSED`

Highest completed milestone: none

## Completed and evidenced

- All three outer hashes and all internal manifests/inventories match; sources remain read-only.
- Full required-document reading and machine audit are complete. Only two immutable broken anchors
  remain, with no additional source defect.
- Implementation commit `3a5762e37a5311f0a7faeca2e93b6c77ab8500ff` establishes the M0
  repository, exact dependency lock, immutable contract/config copies, validators, independent
  migrations, initial audit tables, Compose lifecycle boundaries, a one-shot permit consume model,
  locked Unix-socket runtime, tests, SBOM and security evidence.
- All recommended M0 validation commands pass. Both database trees reproduce
  `upgrade → downgrade base → upgrade`; atomic permit consumption grants once and denies replay.
- The non-root container starts with no network and returns only `RISK_LOCKED` when startup evidence
  is absent.
- Commit `fca378cf7e4f18457f46a381e29fc8599bb5baa8` adds PostgreSQL-authoritative,
  multi-window atomic Reserve. Signed-runtime endpoint policy, caller allowlist, catalog hash,
  cost/ceiling, fencing, request idempotency and one-time capability nonce are checked under row
  locks. Tests prove one charge on retry and fail-closed denial for replay, caller, fencing, catalog
  and blocked-window violations.
- Commit `d5a394e21776957f627c9c3e7da78dfd1accf53c` adds a closed signed-runtime
  capability trust-bundle loader, config-root/capability Ed25519 verification over RFC 8785 JCS
  hashes, full causal binding checks, Linux `SO_PEERCRED` caller/protocol ACLs, and durable
  PostgreSQL epoch leases. Competing owners and expired/stale leases deny Reserve and Consume.
- Commit `0b8dc507522596cc9ba8659b56cbb96744f7c375` adds signed endpoint-catalog
  source/hash/identity/tier verification, bounded one-request length-prefixed Unix framing, duplicate
  JSON-key rejection, runtime-directory/socket-mode enforcement, and a rate admission application
  that closes endpoint, facts, capability, protocol direction and kernel peer before authority calls.
- Commit `8516679` connects that boundary to PostgreSQL v2 atomic Reserve/Consume, stores exact
  signed endpoint payloads and deterministic multi-class cost vectors, journals gateway outcomes and
  observations idempotently, reconciles header maxima, and persists 429/418 blocks across restart.
  The executable rate service verifies exact active policy/window coverage before taking its lease.
- Commit `42624ef909aa25cc4aa7c46c392a7c856eaa82f3` adds gateway closed-schema IPC,
  `SO_PEERCRED` caller binding, authority-to-host and catalog wire identity checks, exact prepared
  wire/canonical/parameter/permit binding, Consume-before-one-send orchestration, SendOutcome, and
  short-lived signed startup-evidence verification. Its transport remains injection-only and is not
  enabled by Compose.
- Commit `46865c3` adds append-only Reserve/Consume decision journals written in the same database
  transaction as the decision; audit failure rolls back a grant. Disposable migration tests prove
  both journals reject mutation.
- Commit `411f4da41d1067fe6985a2e8da25bc1bfb136e56` isolates startup evidence from
  the host configuration trust root. Only the single dedicated signer in the signed capability
  trust bundle may sign it; the issuer enforces the frozen UID/GID, owner-only out-of-repository
  Ed25519 key, schema, 300-second ceiling and immediate independent re-verification.
- Commit `b8bc2816c0118784d60267a7ee2648f12d37c66b` binds the Compose services to
  their frozen trust-boundary identities (`11001`, `11002`, `11005`, `11006`, `11007`), aligns the
  hot-path name with `realtime-engine`, and restricts the attestation key grant to UID/GID 11007 at
  mode `0400`. Static policy and independent security tests reject drift.
- Commit `cc87fda6df0373dec2300a8bbf5616cd74838628` closes the final pre-send
  allocator-response boundary. Gateway validates the complete rate UDS schema and exact allocator
  instance, correlation, permit, connection, fencing, request/facts/capability hashes and bounded
  deadline; every valid-but-mismatched grant is journaled `NOT_SENT` and transport remains at zero.
- Commit `35cfb59287ee2051a6c3fa095673eff7c178974a` binds both sides of local IPC to
  attested identities. Clients verify socket inode/owner/group/mode before and after connect plus
  server `SO_PEERCRED`; servers require root-owned SGID runtime directories and exact socket owner.
  Compose grants only the frozen `11990/11991` shared socket groups.
- Commit `bd79957e59aac0828c32ba76ca342d71808842ad` binds every measured startup
  fact (including WAL/migration, socket identities, network policy, observations and integrity) in
  one local measurement hash, verifies before atomic fsync/rename publication, and re-reads and
  re-verifies the immutable `0444` evidence before controlled operations. Compose exposes exactly
  one evidence writer and one read-only gateway consumer.
- Commit `b9f0d3243089a8b3ec54e2fcbc3371cacd7a51a1` moves the config-root fingerprint
  out of ordinary environment control. Both keyring and SHA-256 pin must be direct root-owned
  `0444` files in a non-symlink, non-writable `/etc/ai-quant/trust` boundary; only rate, signer and
  gateway receive that directory, read-only.
- Commit `c586fef1f9896c476811e46d893ca283d746433c` makes runtime JSON/YAML parsing
  reject duplicate keys and adds exact artifact binding verification for raw schema bytes, JCS of a
  complete document, and JCS of a signed document's `content`. Coverage gaps, symlinks, path escape,
  read-time replacement and hash mismatch all fail closed.
- Docker CE/Compose, Python 3.12.13 via `uv`, chrony, ripgrep and GNU time are installed for
  development. Initial chrony observations are healthy, but not a 24-hour deployment proof.

Detailed evidence: `evidence/stages/M0/2026-07-14/M0_STAGE_REPORT.md`.

## M0 work still required

1. Provision real signed runtime catalog/trust/policy/window inputs and prove the executable rate
   service startup on the authorized Ubuntu 24 deployment target; until then Compose stays locked.
2. Implement and independently review the production exact-wire transport and gateway service only
   after startup evidence and destination policy exist. Multi-role endpoints remain denied because
   the frozen request contract does not provide a unique gateway-side causal derivation rule.
3. Build the executable attestation service and root-authenticated local-facts collector around the
   completed signer/issuer/atomic-publisher/monitor primitives; no caller-supplied draft may be
   treated as measured host state.
4. Destination-specific host DNS/firewall enforcement proving exactly one Binance socket owner and
   zero business Binance routes; current Compose validation is static only.
5. A different actor in fresh context must independently review and issue a valid
   `CodexReviewReport` with zero open P0/P1 before M0 acceptance.

## Current blockers

| ID | Scope | Blocker | Required resolution |
|---|---|---|---|
| BLK-001 | M5 Testnet and later validation | Official Testnet WS base is `wss://demo-fstream.binance.com`; frozen schemas require routed `fstream.binancefuture.com` hosts | Owner-approved baseline amendment or current primary-source/account evidence |
| BLK-002 | M2 Codex execution, M9 | Exact `gpt-5.6` absent from current authenticated Codex catalog; substitution prohibited | Wait for catalog availability or explicit baseline change |
| BLK-003 | Deployment/M6+ | Host is Debian 12 rather than frozen Ubuntu 24 target | Reprovision or approve a reviewed platform amendment |
| BLK-004 | Deployment/M6+ | 24-hour network/clock/static-IP, independent backtest, remote storage, restore, heartbeat, credential-isolation and signature evidence absent | Complete deployment preflight; no secrets requested now |
| BLK-005 | M0 acceptance and every later milestone | Independent fresh-context reviewer absent | Perform independent review after the remaining M0 implementation |

## M0–M9 plan

| Milestone | Status |
|---|---|
| M0 repository/contracts/config/migrations/audit/host control/gateway | IN PROGRESS; offline rate/gateway boundaries implemented, signed deployment/network/attestation/review outstanding |
| M1 market data/order book/quality/archive/replay | NOT STARTED; M0 acceptance required |
| M2 PA/OF/Top10/cost/Codex orchestration/unified backtest | NOT STARTED; Codex portion blocked by model catalog |
| M3 risk/order state/user stream/native protection/reconciliation | NOT STARTED |
| M4 local control/notifications/monitoring/backup/archive/heartbeat | NOT STARTED |
| M5 complete validation | NOT STARTED; Testnet portion blocked by endpoint conflict |
| M6 three-day calibration/candidates only | BLOCKED by earlier milestones and deployment gates |
| M7 fresh validation/C0 freeze/72-hour dual validation | BLOCKED |
| M8 signed experimental live/forward/90-day decision | BLOCKED |
| M9 monthly selection/review/canary/promotion/rollback | BLOCKED |

## Credentials and deployment state

No Binance, OpenAI, Telegram, database, archive, heartbeat or signing production credential has
been requested or injected. Only ephemeral random database passwords were created for disposable
local migration tests and removed by cleanup traps. No exchange API connection, account probe,
order, Testnet runtime, shadow runtime, deployment or live action has occurred.

Deployment authorization: `NOT_AUTHORIZED`. Runtime default: `RISK_LOCKED`.

## Next exact command

```bash
cd /root/quantify/ai-quant-system && make ci && make test-migrations && make test-locked-runtime
```

After this baseline re-verifies, continue M0 with deployment-safe attestation issuance and host
network enforcement evidence. Do not start M1 or enable a transport.
