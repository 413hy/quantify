# ADR 0001: Implementation baseline and preflight conflicts

- Status: accepted for unaffected offline work; two affected integrations blocked pending owner decision
- Date: 2026-07-14
- Decision owners: account owner and implementation lead

## Context

The `vps.7z` package is immutable requirements input. The implementation must fit a single 2 vCPU / 12 GiB / roughly 200 GB Korean VPS, remain fail-closed, isolate all Binance egress through one gateway, and never let Codex hold an exchange secret or submit orders.

## Decision

Use the frozen technical baseline: Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, Polars, PyArrow, PostgreSQL 16 with TimescaleDB, Redis 7 as non-authoritative cache, Parquet/Zstd, and Docker Compose with pinned images/digests. Decimal arithmetic is mandatory for money, quantity, price, cost, and risk.

Maintain three lifecycle boundaries:

1. business services and databases;
2. persistent independent `aiq-host-control` with its own PostgreSQL authority, UDS, fencing, one-shot permits, and attestation signer;
3. persistent independent `aiq-binance-egress`, the only process/network namespace allowed to establish Binance TLS or WebSocket connections.

Every new Binance REST, WebSocket API, stream connection, or control send follows Reserve → gateway request revalidation → atomic PermitConsume → at most one send. Missing or unhealthy authority, database, fencing, evidence, UDS, or gateway means zero new egress and `RISK_LOCKED`; no emergency bypass is implemented.

The default runtime state is `RISK_LOCKED`. No deployment profile contains production credentials by default. Authentication mechanisms for Codex are mutually exclusive and external to this repository.

The complete strategy, risk, execution, data-retention, scheduling, model-isolation, monthly FIFO/quota, and live-gate invariants in the current task and immutable baseline remain unchanged. Louie/PYTA are methodology-only knowledge sources. No optional third-party project is adopted by this ADR.

## Preflight findings and decisions

### A. Immutable document anchors

Two internal runbook links target the wrong section number. This is low risk because the referenced file and intended “故障语义” heading are unambiguous. Do not mutate the source package; implementation runbooks may use the correct anchor and cite this defect.

### B. Binance Testnet WebSocket host — blocked

Frozen configuration requires `wss://fstream.binancefuture.com/{public,market,private}`. Binance official General Info accessed 2026-07-14 states the Testnet WebSocket base is `wss://demo-fstream.binance.com`. This changes an endpoint allowlist and deployment boundary, so no inference or silent compatibility alias is permitted.

Decision: freeze Testnet network execution and all acceptance claims depending on it. Offline adapters and fixtures may be implemented against the immutable contract, but no Testnet connection occurs until the account owner explicitly chooses whether to amend the frozen baseline to the current official host or provides primary-source evidence that the frozen routed hosts remain authoritative for this account/environment. Production endpoints are not changed.

### C. Required Codex model slug — blocked

OpenAI official model guidance accessed 2026-07-14 documents `gpt-5.6` as an alias for `gpt-5.6-sol`. However, the current authenticated Codex catalog from `codex-cli 0.144.4` contains explicit slugs `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, and hidden `codex-auto-review`; exact slug `gpt-5.6` is absent. Catalog SHA-256 is `a149f08e9519fccba540a91a6e87557ae8d9491d2d4001631b3b19de20548053`.

The user's rule is stricter than public API alias documentation: if the required model is not in the current account official Codex catalog, fail closed and do not substitute. Therefore `gpt-5.6-sol` is not selected automatically.

Decision: implement the catalog verifier and failure path, but freeze actual real-time/monthly Codex analysis runs and all affected M2/M9 acceptance until the exact model appears in the account catalog or the owner explicitly changes the immutable model requirement. Deterministic fallback design remains available and cannot weaken shared gates.

### D. Host OS and clock — platform conflict resolved

Observed host is Debian 12/aarch64. The account owner subsequently clarified that Debian is the
project's sole intended platform and approved ADR 0004, which supersedes conflicting OS selections
in the immutable source package. CPU, RAM and disk size match the required envelope. Deployment
qualification is no longer blocked by host distribution, but all remaining platform, network,
clock, restore and independent-review evidence is still required.

The clock was initially unsynchronized and chrony absent. Chrony 4.3 was installed during preflight; the first healthy sample showed normal leap status and roughly sub-millisecond system offset. A single sample is not the required 24-hour deployment proof.

## Consequences

- M0–M5 work that does not need the conflicting external integrations may proceed offline.
- No Testnet success, 72-hour validation, live readiness, deployment completion, or profitability claim is allowed.
- M0 cannot be declared accepted until independent review by a different actor/fresh context and all M0 command gates pass.
- Any change to the Testnet allowlist, required Codex model, hard risk, secret boundary, execution state machine, or egress topology requires a new ADR and account-owner approval.
