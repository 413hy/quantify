# Sanitized session handoff — 2026-07-14

## User intent

The user asked the agent to continue the existing project, complete everything in the frozen
M0→M9 order, and first perform a substantive review of the current code against the development
documents and explicit requirements. The user later asked how to preserve the host without losing
the code or conversation, then authorized uploading the repository and this sanitized continuity
package to `https://github.com/413hy/quantify`.

Human-authored requests from this session were:

1. `继续任务`
2. `直接一次性全部吧这个项目按照流程全部做完吧`
3. Continue from the actual repository and documentation state; review relevant existing code
   deeply before implementing anything, and do not duplicate existing functionality.
4. Ask whether the host must be reinstalled and how to preserve the code and conversation.
5. Upload the project to the GitHub repository, create `chat/`, preserve the session, and add a
   prompt that lets another AI continue.

Environment-generated context, system/developer instructions, private tool logs and authentication
state are deliberately excluded.

## Repository and source boundaries

- Project repository at handoff: `/root/quantify/ai-quant-system`.
- Original immutable material: `/root/quantify/reference-materials` (outside Git; restore
  separately and keep read-only).
- Frozen repository copies: `config/`, `contracts/`, and `runbooks/`; provenance checks currently
  pass. Do not edit them casually or regenerate them from memory.
- The repository was clean before creation of this continuity package.
- The implementation baseline immediately before the GitHub-history merge was commit `3c4469d`.
- GitHub originally contained only an initial `# quantify` README commit. It was joined to the full
  local history with a normal unrelated-history merge; no force push was used.

## Required project state

The honest state remains:

```text
M0_IN_PROGRESS / NOT_ACCEPTED / FAIL_CLOSED
```

No milestone has been independently accepted. M1 must not start until M0 passes a fresh-context
independent review. No production transport, Binance connection, Testnet runtime, production
credential, deployment or live action has been enabled.

## Review performed in this session

The implementation review found and fixed material boundary defects rather than reimplementing
existing features:

- One-way UDS EOF could be mistaken for a committed notification after handler failure. A
  post-handler ACK is now required; repeated gateway outcome-journal failure latches closed.
- The locked rate service unnecessarily received the host database bootstrap secret. That grant was
  removed. Private files now require absolute, non-symlink, current-UID `0400` files outside the
  release tree.
- Host migration `0009_runtime_role` creates the narrow `aiq_rate_authority` role as `NOLOGIN` by
  default, revokes public function execution and hardens security-definer search paths.
- Host migration `0010_local_measurements` adds the fixed read-only database/WAL/fencing/integrity
  snapshot callable by the narrow runtime role.
- Endpoint source artifacts, authority/transport/scheme/host tuples and denied Consume connection
  bindings are exact and fail closed.
- Startup evidence no longer accepts caller-authored draft content. A fresh root-owned `0444`
  local-facts snapshot is strictly parsed, hash-bound and remeasured against boot ID, artifacts,
  release files and both Unix sockets.
- An executable fail-closed attestation issuer now reloads the root plan, signed trust bundle and
  owner-only key every cycle; binds the actual keyring/trust/schema files used; publishes atomically;
  refreshes within 60 seconds; and removes the evidence on handled stop or refresh failure.
- A root-only local-facts collector now requires six fresh protected measurement sources, remeasures
  artifacts/releases/image digests/boot ID/sockets, validates the immutable evidence Schema and
  atomically publishes `0444 root:root`.
- All six measurement producer/verifier boundaries are implemented. They require one coherent
  capture timestamp, authenticated journals, complete dual bootstrap traces and enforceable live
  nftables/Docker facts. Real deployment sources and rules are not provisioned yet.
- Compose validation explicitly forbids activating that issuer without real deployment facts. It
  must remain `ai_quant.services.locked_process` with `RISK_LOCKED` in the current baseline.

Important commits include:

- `59108c9` — reviewed authority-boundary fixes.
- `53784a5` — local-facts evidence assembly.
- `d3711e0` — executable fail-closed attestation issuer.
- `fcbcba2` — deployment-lock policy and security test.
- `4b71424` — root-only local-facts collector and independent source revalidation.
- `632fd52` — coherent database/authority/network/bootstrap/readiness measurement producers.
- `123428d` — executable Debian measurement cycle, unit artifacts and checked nftables renderer.
- `543791d` — narrow runtime database table/function privileges and fixed observation reader.

## Last verified results

The final functional checks before this handoff passed:

```text
make ci
  unit:    135 passed
  property:  3 passed
  contract:  2 passed
  security:  9 passed
  ruff/mypy/bandit/secret-scan/provenance: PASS

make test-locked-runtime
  status=RISK_LOCKED
  new_egress_allowed=false
  network=none
```

The host-control migration head is `0010_local_measurements`; the independent disposable migration
round-trip previously passed and no later migration changed it.

Latest local arm64 cached-build image evidence:

```text
sha256:f15ab84db9c6f0da442cb358787f6c4e725fc55dc58b6693b868f368804e711a
size=340930816
```

Do not treat that local image ID as a signed registry release or deployment attestation.

## External blockers and remaining gates

- `BLK-001`: frozen Testnet WS hosts conflict with the currently documented official endpoint;
  owner-approved baseline change or authoritative account evidence is required.
- `BLK-002`: exact required `gpt-5.6` was absent from the authenticated model catalog;
  substitution is prohibited.
- `BLK-003`: resolved by owner-approved ADR 0004; Debian 12 is the sole platform.
- `BLK-004`: qualified deployment, 24-hour clock/network/static-IP proof, signed runtime inputs,
  destination-specific DNS/firewall evidence, remote storage/restore/heartbeat and related evidence
  are absent.
- `BLK-005`: a different fresh-context reviewer has not issued a valid `CodexReviewReport` with zero
  open P0/P1.

The owner subsequently clarified that Debian 12 is the only supported platform. Retain the current
host, create a rollback image if desired, and complete the remaining deployment qualification on
Debian. If the host must be replaced, first keep both the GitHub push and an independently verified
off-host archive.

## Authoritative continuation files

Read these before changing code:

1. `IMPLEMENTATION_STATUS.md`
2. `HANDOFF_STATE.md`
3. `docs/adr/0001-implementation-baseline.md`
4. `docs/adr/0002-m0-toolchain-and-runtime-topology.md`
5. `docs/adr/0003-signed-capability-peer-and-fencing.md`
6. `docs/adr/0004-debian-12-sole-platform.md`
7. `docs/deployment/debian-12-platform.md`
8. `evidence/stages/M0/2026-07-14/M0_STAGE_REPORT.md`
9. `contracts/codex-review-report.schema.json` and its example if performing independent review

After restoring the repository, run:

```bash
make bootstrap
make validate-debian-platform
make ci
make test-migrations
make test-locked-runtime
```

Do not request or inject production credentials merely to make local checks pass.
