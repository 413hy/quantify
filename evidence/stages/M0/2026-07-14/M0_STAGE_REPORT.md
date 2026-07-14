# M0 stage progress report

- Stage: M0 — repository, contracts, configuration, migrations, audit and egress skeleton
- Status: `IN_PROGRESS / NOT_ACCEPTED / FAIL_CLOSED`
- Report time: `2026-07-14T08:26:34Z`
- Implementation commits: `3a5762e37a5311f0a7faeca2e93b6c77ab8500ff`,
  `fca378cf7e4f18457f46a381e29fc8599bb5baa8`,
  `d5a394e21776957f627c9c3e7da78dfd1accf53c`,
  `0b8dc507522596cc9ba8659b56cbb96744f7c375`,
  `8516679`, `42624ef909aa25cc4aa7c46c392a7c856eaa82f3`, `46865c3`,
  `411f4da41d1067fe6985a2e8da25bc1bfb136e56`,
  `b8bc2816c0118784d60267a7ee2648f12d37c66b`
- Implementer: `/root` engineering session
- Independent reviewer: not assigned; a different actor with fresh context is still required
- `CodexReviewReport`: absent by design; the implementer cannot self-sign it
- Open P0/P1 from independent review: unknown until independent review; no acceptance claim made

## Delivered foundation

The foundation commit changes 184 files; later atomic reservation and signed
capability/peer/fencing increments are recorded by their Git commits. The exact names are in Git;
the foundation grouped
counts are: 70 contract files, 35 configuration files, 14 immutable runbooks, 12 source files, 10
tests, 8 migration files, 8 scripts, 4 Compose files, 4 diagrams, one Dockerfile, the locked project
metadata, and evidence.

| Requirement ID | State | Evidence |
|---|---|---|
| M0-R01 immutable-source provenance | PASS | `scripts/validate/provenance.py`; `ci.log` |
| M0-R02 exact dependency lock and pinned images | PASS for development build | `uv.lock`; ADR 0002; `python-sbom.cdx.json` |
| M0-R03 all frozen schemas/examples/JCS/OpenAPI | PASS | `recommended-acceptance.log` |
| M0-R04 embedded configuration secrets rejected | PASS | `ci.log`; `no-production-credentials.txt` |
| M0-R05 independent business/host migrations | PASS | `migrations.log`; heads below |
| M0-R06 append-only audit and default lock | PASS for initial schema | migration files and integration assertions |
| M0-R07 one gateway definition, zero business egress network membership | PASS statically | `scripts/validate/compose.py`; `ci.log` |
| M0-R08 atomic reserve/permit/nonce consume and replay denial | PASS for implemented database boundary | `migrations.log`; unit/property tests |
| M0-R09 locked non-root container startup | PASS | `locked-runtime.log` |
| M0-R10 full Reserve→gateway→PermitConsume→send service | PARTIAL | bounded rate/gateway IPC, v2 Reserve/Consume, exact-wire single-send core and outcome journal pass; production transport is intentionally absent |
| M0-R11 signed startup evidence and host destination firewall | PARTIAL | independent trust-domain issuer/verifier passes; measured-facts service, signed deployment evidence and host firewall proof remain absent |
| M0-R12 independent fresh-context review | BLOCKED | reviewer and valid `CodexReviewReport` absent |

## Artifact and configuration identity

- Configuration manifest hash: `7720834b5d493460b2ff4e5a45b3be18df0d434f1f8f2be206442135b85793ba`.
- Contract manifest hash: `a5f238d75cf100493071c81a23f2260724e4bf290ea44d7980706052bd5fe9f7`.
- The earlier foundation implementation manifest hash was
  `b13e7e76e1f6ad5e08b4d2b846f7ea15cdcefab163b25db5256541f7dd60b91a`; Git commit identity is
  authoritative for the later increments.
- Local application OCI image ID: `sha256:2a3cdbf51a341e05d2d842660d5040960ae8224f71bb22032b4931ad56e46fd4`.
- Image architecture/size: Linux arm64, 340,686,460 bytes.
- The earlier image was reproduced twice. This new dependency-bearing image was built repeatedly
  from cache with the same ID but has not had a fresh no-cache reproducibility run.
- Business migration head: `0001_business_core`.
- Host-control migration head: `0008_decision_audit`.

The local image ID is not represented as a signed registry release digest. No release, deployment,
startup-evidence, or live authorization has been issued.

## Verification results

| Flow | Command/evidence | Result |
|---|---|---|
| Normal | first atomic permit consume | `CONSUME_GRANTED:RATE_PERMIT_CONSUMED` |
| Normal | atomic reserve and same-key retry | same permit returned; window charged exactly once |
| Normal | fencing acquire and same-owner renewal | epoch increments once; renewal preserves epoch |
| Error | same permit consumed again | `CONSUME_DENIED:PERMIT_NOT_RESERVED` |
| Error | unauthorized caller, stale fencing, unknown catalog, blocked window | all denied before permit creation |
| Error | competing fencing owner and expired lease | owner denied; expired lease denies Reserve and Consume |
| Boundary | signed bundle/capability/peer checks | valid path passes; tampering, wrong peer, expiry and non-Unix peer deny |
| Boundary | gateway exact-wire path | peer/caller, catalog, host, request, permit and facts bind before Consume; fake transport called once only after grant |
| Accounting | gateway journal and header reconciliation | outcome/observation idempotency, replay denial, observed max and durable 429 block pass |
| Audit | Reserve/Consume decisions | same-transaction append-only journals pass; mutation is rejected |
| Boundary | startup attestation trust | config-root reuse and wrong signatures deny; only the trust-bundle signer and frozen holder UID/GID may issue |
| Boundary | Compose service identity | realtime/execution/gateway/rate/signer use frozen UID/GID values; attestation key is granted only to 11007 at `0400` |
| Boundary | any changed binding hash, expiry, or replay | property tests deny without reopening permit |
| Startup failure | non-root container, no network, no startup evidence | `RISK_LOCKED`, new egress false |
| Database | business + host `upgrade → downgrade base → upgrade` | PASS on fresh disposable volumes |
| Configuration/contracts | all recommended M0 validation targets | PASS |

Primary logs and SHA-256 values are stored below this report in `tests/`, `security/`, and
`artifacts/`. The final CI run passed 60 unit, 3 property, 2 contract, and 4 security tests. The
migration shape test and containerized migration round-trip also passed.

## Resource and security observations

- Latest full functional CI exited 0. The most recent dedicated resource measurement before this
  increment was 14.50 seconds wall time and 79,008 KiB maximum resident set; it is not relabeled as
  a measurement of this increment.
- Host at report time: 2 vCPU, 12,536,565,760 bytes RAM, 199,142,084,608-byte root filesystem,
  7% used, no swap.
- Chrony: synchronized, leap status normal, 0.026 ms observed system offset; this is not 24-hour
  deployment evidence.
- Bandit: PASS. Repository/evidence secret scan: PASS.
- Python environment audit: 104 records, one editable-root skip, zero known vulnerabilities; method
  limitation is documented in `security/pip-audit-method.md`.
- CycloneDX 1.6 SBOM: 103 components, reproducible-output mode.
- No OS-package/image CVE attestation or signed supply-chain provenance exists yet.

## Fault and rollback posture

Fault checks completed: missing startup evidence, `--network none`, changed permit binding, expired
permit, replayed permit, absent permit, and migration downgrade/upgrade. No actual Binance transport
exists in the active service, so these tests cannot be interpreted as exchange integration evidence.

Rollback before any durable environment exists: stop/remove the three Compose projects and revert
commit `3a5762e`. Destructive host-control database downgrade is forbidden after real authority state
exists; then rollback must follow runbook 09A with counters, fencing, nonce and permit state moving
only forward. The integration test downgrades disposable empty test volumes only.

## Human gates and remaining work

All runtime and live gates remain `NOT_AUTHORIZED`; `RISK_LOCKED` is mandatory. No credentials are
needed for the next work.

M0 cannot be accepted until real signed runtime inputs populate the deployment database, the
attestation signer issues deployment-bound evidence, an independently reviewed production transport
is activated behind destination-specific host network enforcement, and a fresh-context independent
review is accepted. Offline services and verifiers do not constitute deployment evidence. M1 has not
started.
