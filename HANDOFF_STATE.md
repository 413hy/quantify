# Handoff state

Updated: `2026-07-16T02:20:22Z`

## Start here

Detect the clone root with `git rev-parse --show-toplevel`; do not assume an absolute path. The
historical deployment path is `/root/quantify/ai-quant-system`.

Read, in order:

1. `IMPLEMENTATION_STATUS.md`
2. `docs/testnet-campaign.md`
3. `docs/adr/0037-three-coin-predictive-fast-context-v5-5.md`
4. `docs/adr/0038-one-minute-cadence-v5-6.md`
5. `chat/SESSION_HANDOFF_2026-07-16.md`
6. Earlier ADRs only when changing the subsystem they govern.

The separate `/root/quantify/reference-materials` tree is not part of Git. Restore it separately
and keep it read-only before provenance-dependent work.

## Current deployed state

- Platform: Debian 12 Bookworm/aarch64 on Oracle Cloud.
- Environment: Binance USDⓈ-M Futures Testnet only.
- Strategy: `TESTNET_EXPERIMENT_OF_PA_V5_6`.
- Universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT and XRPUSDT.
- Evaluation: once per minute; fast/sustained windows are approximately three/four minutes.
- Capacity: zero to five symbols; approximately 1 USDT margin each; exchange-maximum leverage.
- Entry: confirmed-signal market order.
- Exit: native stop/target, signal invalidation or confirmed reversal; no elapsed-time exit.
- Target economics: 22 bps BTC/ETH, 25 bps BNB/SOL/XRP, with at least 0.10 USDT estimated net after
  fees and buffer.
- Production: `RISK_LOCKED`; no production transport or credential is authorized.
- Strategy result: unvalidated. Do not promise profitability or a win rate.

Systemd services:

- `aiq-testnet-secrets.service`
- `aiq-testnet-campaign.service`
- `aiq-testnet-user-stream.service`
- `aiq-telegram-dashboard.service`

All four are intended to be enabled at boot. The campaign reconciles protected Testnet state before
considering a new entry and runs independently of Codex.

Useful read-only checks:

```bash
systemctl status aiq-testnet-campaign.service
systemctl status aiq-testnet-user-stream.service
systemctl status aiq-telegram-dashboard.service
jq . /var/lib/ai-quant/evidence/testnet/campaign/current/state.json
journalctl -u aiq-testnet-campaign.service -n 100 --no-pager
```

## Latest verified repository state

On 2026-07-16:

- `make ci`: PASS.
- 302 unit, 19 property, 2 contract and 19 security tests passed.
- Full `uv run pytest -q`: 371 passed.
- Ruff, strict mypy over 98 source files, Bandit and secret scan passed.
- Contract/config/provenance/Compose/deployment validators passed.
- Debian verifier passed on the deployed 2-vCPU, approximately 12-GiB, 200-GB OCI host.

Run these again after restoring or changing code:

```bash
make validate-debian-platform
make ci
uv run pytest -q
```

Use `make test-migrations`, `make test-locked-runtime` and `make paper-flow` when the changed scope
touches database, runtime isolation or shared strategy/execution behavior.

## Development boundaries

- Review relevant existing code and ADRs before implementing; do not duplicate an existing path.
- Debian is the sole supported platform. Do not reintroduce instructions for another distribution.
- Do not edit provenance-protected copies under `config/`, `contracts/` or `runbooks/` without an
  explicit owner-approved amendment.
- Do not commit Testnet/production secrets, Telegram token/chat IDs, server passwords, raw Codex
  state, runtime evidence containing account identifiers, or `/root/aiq-user-inputs`.
- Testnet changes may be deployed when owner-authorized and verified. Production must remain locked
  until its external gates and explicit approval exist.
- Keep old strategy ADRs and archived results. Historical losses and rejected experiments are part
  of the audit trail, not files to erase.

## GitHub publication

The owner has now explicitly authorized synchronizing the reviewed repository to
`git@github.com:413hy/quantify.git`. Before every push, run the secret scan, review staged files and
confirm that runtime credentials and raw chat/tool state are excluded.

## External inputs after a host rebuild

Restore these outside Git with root-only permissions:

- Immutable `/root/quantify/reference-materials` archive.
- Binance Testnet credential input files used by `aiq-testnet-secrets.service`.
- Telegram inputs under `/root/aiq-user-inputs/notifications/`.
- Runtime evidence/archive backups if historical continuity is required.

Never reconstruct missing secrets from documentation or commit them as placeholders.
