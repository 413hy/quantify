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

All six source snapshots must be no more than five seconds old. The collector validates the
combined facts against the immutable startup-evidence Schema before publication. The independent
signer then reloads those same sources and remeasures the boot ID, artifacts, release files, image
digests and sockets; changing a source after collection therefore invalidates issuance.

## Failure semantics

Non-root execution, missing coverage, duplicate keys, unsafe paths, stale sources, hash mismatch,
Schema failure, socket replacement, artifact replacement or an unsafe output directory fails
closed. No failure path enables the signer, gateway or exchange transport. Until real measurement
producers and deployment evidence exist, `deploy/host-control.compose.yaml` must continue to run
the signer as `ai_quant.services.locked_process` with `RISK_LOCKED`.
