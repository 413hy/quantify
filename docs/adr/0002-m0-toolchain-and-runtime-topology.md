# ADR 0002: M0 toolchain and fail-closed runtime topology

- Status: accepted for M0 development; not a deployment approval
- Date: 2026-07-14
- Implementation commits: `3a5762e37a5311f0a7faeca2e93b6c77ab8500ff`,
  `fca378cf7e4f18457f46a381e29fc8599bb5baa8`

## Decision

Use a `uv`-locked Python 3.12.13 project with exact direct dependency versions. Build the
application from the multi-architecture Python 3.12.13 slim-bookworm base pinned by index digest
`sha256:a5d9a95a366e9cb09c32e2623ae98320433f169b2974b451969459ca585e009a`.
The runtime image uses UID/GID `65532:65532`, a read-only root filesystem, dropped capabilities,
`no-new-privileges`, and no embedded credentials.

Pin database/cache images by multi-architecture index digest:

- TimescaleDB 2.28.2 / PostgreSQL 16.14:
  `sha256:ba149561ad4ddff5940d6eb0a0df60aefd1355cee1a450928f271267038fc888`;
- PostgreSQL 16.14 Alpine:
  `sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777`;
- Redis 7.4.9 Alpine:
  `sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99`.

Keep the three Compose lifecycles separate: business, host-control, and the unique Binance egress
gateway. Business and host-control data networks are `internal`; only the gateway definition joins
the egress bridge. This is a topology skeleton, not proof of a production host firewall or
destination-specific DNS policy. Until signed startup evidence, allocator authority, peer ACL,
capability verification, and the complete gateway protocol exist, every service uses the locked
Unix-socket process and permits zero outbound traffic.

Docker CE was installed from Docker's signed Debian repository using the repository signing key
whose observed fingerprint was `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`. The installed versions
are recorded in the M0 toolchain evidence. This host remains a development host because it is
Debian 12 rather than the frozen Ubuntu 24 deployment target.

## Rejected alternatives

- Floating image tags or unpinned direct dependencies: not reproducible and fail the release gate.
- A single Compose project or shared database for host-control and business state: violates
  lifecycle, rollback, and authority isolation.
- Redis as rate authority: violates the PostgreSQL durable atomic authority requirement.
- Giving the gateway a Binance secret or signing key: violates the secret boundary.
- Enabling a real transport before startup evidence: violates fail-closed startup semantics.

## Consequences

The local image is reproducible for the same source and builder and currently has OCI image ID
`sha256:52516cf6272b8663c00e8fdb5b87155aefe0d9e49365ef9831c2e1ab15a45121`.
That local digest is evidence for this build only; deployment still requires a controlled registry
artifact, signed release manifest, and verification on the qualified target host.
