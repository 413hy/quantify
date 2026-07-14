# Debian 12 deployment platform overlay

This document is the effective platform-specific overlay for every frozen initialization, upgrade,
rollback and recovery instruction. ADR 0004 takes precedence whenever a historical source selects a
different host distribution. All non-platform safety requirements in the frozen runbooks remain in
force.

## Fixed host profile

```text
provider=Oracle Cloud Infrastructure
os=Debian GNU/Linux 12 (bookworm)
architecture=aarch64
kernel_minimum=6.1
cgroup=unified-v2
init=systemd>=252
firewall=nftables
cpu=2
memory_approx_gib=12
root_storage_gb=180..220
```

## Qualification order

1. Preserve the Git remote and an off-host backup before host-level changes.
2. Run `make validate-debian-platform` and retain its exact output.
3. Run `make ci`, `make test-migrations` and `make test-locked-runtime`.
4. Prove chrony, static public IP, DNS behavior and host network policy continuously for 24 hours.
5. Prove the gateway is the sole Binance socket owner and business containers have zero Binance
   routes.
6. Provision real signed runtime inputs and least-privilege credentials only after the preceding
   gates pass.
7. Generate deployment-bound startup evidence and obtain an independent M0 review before M1.

## Host package boundary

Use Debian bookworm repositories plus Docker's signed Debian repository. Pin or record every
installed version in stage evidence. Python application dependencies remain managed by the locked
`uv.lock`; do not replace them with distribution Python packages.

Use nftables directly for host enforcement. Preserve Oracle Cloud boot-volume iSCSI rules and the
VCN security-list/network-security-group boundary. Do not interpret a VCN rule alone as proof that
business containers cannot reach Binance.

The platform gate is intentionally read-only. It verifies the host but does not install packages,
modify firewall state, inject credentials or open egress.

## Recovery and image handling

An OCI custom image of this instance is a Debian rollback artifact, not a release attestation. It
contains the boot volume only; separately attached block volumes require independent backups. Before
creating a reusable image, remove or revoke temporary repository deploy keys and local product
authentication material that must not be cloned.

Restoring a custom image does not waive migration, fencing, counter, nonce, permit, network,
startup-evidence or independent-review gates. Restored runtime authority must move forward and must
never be replaced by an empty database merely to make startup succeed.
