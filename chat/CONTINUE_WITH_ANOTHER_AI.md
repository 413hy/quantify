# Prompt for another AI/Codex

Copy everything inside the following block into a fresh AI session after cloning this repository.

```text
You are continuing a security-critical quantitative trading system from an existing Git repository.
Treat the repository and restored immutable source materials as authoritative; do not rely only on
this prompt or prior-agent claims.

Repository:
- Detect the clone root with `git rev-parse --show-toplevel`; do not assume the old absolute path.
- The historical path was /root/quantify/ai-quant-system.
- The separate immutable source directory was /root/quantify/reference-materials. It is not stored
  in this Git repository. If it is absent, stop provenance-dependent work and ask the owner to
  restore the original archive; do not recreate or modify it.

Before implementation:
1. Inspect `git status`, current branch, recent history and remotes.
2. Read completely:
   - chat/SESSION_HANDOFF_2026-07-14.md
   - IMPLEMENTATION_STATUS.md
   - HANDOFF_STATE.md
   - docs/adr/0001-implementation-baseline.md
   - docs/adr/0002-m0-toolchain-and-runtime-topology.md
   - docs/adr/0003-signed-capability-peer-and-fencing.md
   - docs/adr/0004-debian-12-sole-platform.md
   - docs/adr/0005-current-binance-testnet-endpoints.md
   - docs/deployment/debian-12-platform.md
   - evidence/stages/M0/2026-07-14/M0_STAGE_REPORT.md
3. Review the relevant existing code deeply against the frozen documentation and explicit
   requirements before changing it. Do not superficially inspect or duplicate an existing feature.
4. Verify the baseline with `make ci`, `make test-migrations`, and `make test-locked-runtime` as
   appropriate for the restored host. Record actual results; do not copy expected results as proof.

Mandatory state and safety constraints:
- Current state is OFFLINE_DEVELOPMENT_FLOW_PASS / TESTNET_CREDENTIAL_REJECTED / RISK_LOCKED.
- Debian 12 Bookworm/aarch64 on Oracle Cloud is the sole owner-approved host platform. Do not select
  or recommend another distribution. Run `make validate-debian-platform` on deployment candidates.
- Keep runtime default RISK_LOCKED.
- Do not enable production transport, request or inject production credentials, deploy live
  trading, or claim deployment evidence from local tests. Testnet-only probes are owner-approved
  under ADR 0005 and must remain exact-host, bounded and fail-closed.
- Do not substitute the required model or silently change the ADR 0005 Testnet host policy.
- Do not edit original reference materials. Treat repository `config/`, `contracts/`, and
  `runbooks/` as provenance-protected frozen copies unless an explicit owner-approved baseline
  amendment authorizes a change.
- Preserve business/host-control/gateway secret, database, network and UDS authority separation.
- M1 must not begin before M0 is independently accepted.
- The implementation agent cannot self-sign the required independent acceptance report.

Current implementation facts to verify rather than assume:
- Reviewed UDS commit ACK and gateway failure latch exist.
- Database runtime role head is 0010_local_measurements and the locked rate service receives no bootstrap
  database secret.
- Root-authenticated local-facts assembly rejects caller-authored evidence drafts.
- The root-only local-facts collector closes six protected dynamic-source types, remeasures static
  bindings, validates the immutable evidence Schema and atomically publishes `0444 root:root`.
- All six producer/verifier boundaries exist offline; mixed capture timestamps, broken bootstrap
  causality, incomplete nftables policy, journal mismatch and failed readiness deny publication.
- A root-only executable cycle now connects those producers using a closed root-owned plan, fixed
  local PostgreSQL Unix socket, signed connection/catalog verification, live Docker/nftables state
  and UDS `SO_PEERCRED` probes. Hardened Debian units and an nftables renderer are staged only; they
  are not installed, enabled or applied.
- The executable attestation issuer binds the actual keyring/trust/schema artifacts, refreshes no
  slower than 60 seconds, atomically publishes and removes evidence on handled failure.
- Compose intentionally keeps the attestation signer on locked_process/RISK_LOCKED until real
  deployment facts exist.
- The restricted database role now has five explicit table reads and six explicit function entry
  points; observations and authority blocks are measurement-reader-only.
- Last recorded tests were 176 unit, 8 property, 2 contract, 16 security, 3 replay, 18 integration,
  6 fault and 1 resource tests, but rerun them.

Known external blockers:
- BLK-001 endpoint conflict resolved by ADR 0005; authenticated Testnet validation is currently
  blocked because Binance returns `-2015` for the supplied Demo credential.
- BLK-002 exact gpt-5.6 unavailable; substitution prohibited.
- BLK-003 resolved by owner-approved ADR 0004; Debian 12 is the sole platform.
- BLK-004 qualified deployment/network/clock/storage/restore/heartbeat/signed evidence absent.
- BLK-005 independent fresh-context reviewer absent.
- BLK-006 complete signed Debian host bootstrap/toolchain/hardening/quantctl bundle absent; never
  invent package hashes, signing fingerprints or SSH approval inputs.

If you are acting as the independent reviewer, do not modify implementation before completing the
review. Read `contracts/codex-review-report.schema.json`, verify every claimed M0 fact directly, and
issue a report only if reviewer/implementer separation is genuine and open P0/P1 count is zero. If
you instead continue implementation, keep M0 NOT_ACCEPTED and leave independent review to another
actor after your changes.

Continue all safe, in-scope M0 work that current evidence permits. Do not fake external facts or
weaken gates to make progress appear complete. Commit coherent changes, keep evidence synchronized,
and report exact remaining blockers. Only proceed to M1 after valid independent M0 acceptance.
```
