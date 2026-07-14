# Root local-facts collector

The Debian host runs `ai_quant.services.local_facts_collector` as a root-only host process. It is
not activated by the locked Compose baseline. Activation requires real deployment measurement
sources, destination-specific network enforcement and the normal M0 review gates.

## Trust boundary

The collector loads one direct, root-owned `0444` plan from `/etc/ai-quant/trust`. Its output
directory must be an absolute, root-owned directory with no group or world write permission. Each
refresh writes a new `0444 root:root` facts document with file and directory `fsync`, then publishes
it with an atomic replacement. Stop or refresh failure removes the last facts document.

The plan closes all of these source sets:

- every startup-evidence artifact with an explicit raw/JCS hash mode;
- the four release-file hashes and two runtime image-digest files;
- the host boot-ID path and the two Unix-socket paths;
- both socket peer-ACL hashes;
- exactly six dynamic sources: database authority, network boundary, authority observations,
  nonce/permit integrity, bootstrap chain and readiness.

Each dynamic source is a direct `0444 root:root` JSON file in a protected root-owned directory:

```json
{
  "schema_version": "1.0.0",
  "captured_at": "2026-07-14T00:00:00Z",
  "measurement_hash": "<SHA-256(RFC8785-JCS(measurement))>",
  "measurement": {}
}
```

All six source snapshots must be no more than five seconds old and must carry exactly the same
capture timestamp; a partially replaced or mixed-generation set fails closed. The collector
validates the combined facts against the immutable startup-evidence Schema before publication. The
independent signer then reloads those same sources and remeasures the boot ID, artifacts, release
files, image digests and sockets; changing a source after collection therefore invalidates issuance.

The implemented producers use a fixed security-definer database snapshot, authenticated gateway
journals, fixed read-only Docker/nftables inspection commands, two non-replayed causal bootstrap
traces and independent readiness probes. These are executable boundaries, not proof that the real
deployment inputs or firewall rules currently exist.

## Measurement generation cycle

`ai_quant.services.measurement_cycle` is the only implemented host process that may generate the
six dynamic source files. It reloads the direct root-owned `0444` measurement plan on every cycle,
uses the fixed `/run/ai-quant-host-postgres` Unix socket (never a caller-supplied DSN), verifies the
signed runtime connection contract and endpoint catalog against the pinned config keyring, verifies
the source artifact bytes, reads the database snapshot and authenticated journals, inspects Docker
and nftables with fixed absolute read-only commands, verifies two fresh causal bootstrap traces,
and probes both Unix sockets with filesystem identity plus `SO_PEERCRED`.

The plan shape is shown by `deploy/measurement-cycle-plan.example.json`. The example is not a
runtime authorization and must not be copied unchanged. Runtime policy, catalog, connection
contract, keyring, pin, bootstrap envelope and both plans must be direct files in
`/etc/ai-quant/trust`, owned by root and mode `0444`. The database credential remains a distinct
root-only `0400` file under `/run/ai-quant-secrets`; it is never part of either plan.

The database snapshot now also returns the sorted effective authority-block list through the same
security-definer function. The authority-observation rows use a second fixed reader capped to the
last 300 seconds. The runtime role is limited to five operational table reads and six named function
entry points; observation and authority-block tables cannot be read directly.
Readiness is derived from the verified inputs, database block state and live socket probes; the plan
has no field that can assert `READY`.

Two Debian unit files are staged under `deploy/systemd/`. They are repository artifacts only: this
change neither installs nor enables them. The measurement unit has access to the Docker socket,
nftables netlink, the local PostgreSQL socket and its two output directories; the local-facts unit
has a private network namespace and only reads the generated measurements. Both remove their
published state on handled stop or refresh failure.

The PostgreSQL Compose service binds its Unix socket to `/run/ai-quant-host-postgres` without
publishing a TCP port. Activation still requires an out-of-band `LOGIN` credential for
`aiq_rate_authority`; the repository does not create, store or guess that credential.

## Host firewall artifact

`tools/render_nftables_policy.py` renders a deterministic `inet ai_quant_egress` table from resolved
deployment addresses. The table touches only Docker-forwarded and host-output traffic to the
resolved Binance sets; it has no input hook and cannot alter SSH, OCI boot-volume iSCSI or the
provider firewall. `make validate-nftables-policy` runs nftables parser validation with `--check`
and does not apply rules. The checked-in plan uses documentation-only addresses and is never
deployment evidence. Applying a real rendered table remains a separate, explicitly reviewed host
operation after address resolution and SSH recovery checks.

## Failure semantics

Non-root execution, missing coverage, duplicate keys, unsafe paths, stale sources, hash mismatch,
Schema failure, socket replacement, artifact replacement or an unsafe output directory fails
closed. No failure path enables the signer, gateway or exchange transport. Until real measurement
inputs and deployment evidence exist, `deploy/host-control.compose.yaml` must continue to run
the signer as `ai_quant.services.locked_process` with `RISK_LOCKED`.
