# ADR 0004: Debian 12 is the sole deployment platform

- Status: accepted; owner-approved baseline amendment
- Date: 2026-07-14
- Decision owner: account owner
- Scope: development, validation, recovery and production host platform
- Supersedes: every conflicting operating-system selection in the immutable source baseline

## Context

The account owner clarified that the project is intended to use Debian exclusively and that the
previous distribution named in the source package was a documentation error. The existing Oracle
Cloud host already runs Debian GNU/Linux 12 on aarch64 and has the required resource envelope.

The original archives and their byte-identical `config/`, `contracts/`, `runbooks/` and `diagrams/`
copies remain unchanged so provenance continues to prove what was originally supplied. They are
historical inputs, not the effective platform authority where they conflict with this amendment.

## Decision

The only supported host platform is:

- Oracle Cloud Infrastructure virtual machine;
- Debian GNU/Linux 12 (`bookworm`), aarch64;
- Linux kernel 6.1 or later;
- unified cgroup v2;
- systemd 252 or later;
- Docker Engine and Docker Compose with aarch64 support;
- chrony with normal leap status;
- nftables as the host firewall authority;
- exactly 2 vCPU, approximately 12 GiB RAM and 180–220 GB root storage.

No other host distribution is an accepted deployment target. Container image pins, Python 3.12,
PostgreSQL/TimescaleDB, Redis, application boundaries, secret isolation, fail-closed behavior and all
M0→M9 gates remain unchanged.

`scripts/validate/debian-platform.sh` is the effective host-platform gate. Any frozen command that
selects a different distribution is superseded and must not be executed as written. The operational
initialization overlay is `docs/deployment/debian-12-platform.md`.

## Compatibility findings

The current host was observed with Debian 12, aarch64 kernel `6.1.0-50-cloud-arm64`, cgroup v2,
systemd 252, Docker Engine 29.6.1, Docker Compose 5.3.1, chrony synchronized with normal leap status,
nftables 1.0.6, 2 vCPU, 12,536,565,760 bytes RAM and a 199,142,084,608-byte root filesystem.

Application containers already use digest-pinned multi-architecture images and the application base
is Debian bookworm-derived. The change therefore affects host qualification, package/bootstrap
commands, firewall operations and recovery evidence, not application protocol or authority logic.

## Consequences

- The former host-platform conflict is resolved by owner decision; it is no longer an external
  blocker.
- This host is a deployment candidate, not an accepted deployment merely because the OS matches.
- The 24-hour clock/network proof, destination firewall proof, signed runtime inputs, restore tests
  and independent M0 review are still mandatory.
- Future platform changes require a new owner-approved ADR and a fresh compatibility review.
