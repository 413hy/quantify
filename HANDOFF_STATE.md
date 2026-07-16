# Handoff state

Updated: `2026-07-16T02:45:14Z`

## Current repository purpose

This is a strategy-free trading execution framework. Do not assume the deleted V4/V5 campaign is
still present and do not restore it from Git history unless the owner explicitly asks.

Read first:

1. `README.md`
2. `IMPLEMENTATION_STATUS.md`
3. `docs/FRAMEWORK_SCOPE.md`
4. `docs/adr/0039-strategy-free-framework-reset.md`
5. `chat/CONTINUE_WITH_ANOTHER_AI.md`

## Runtime state

- `aiq-testnet-campaign.service`: stopped, disabled and removed.
- `aiq-telegram-dashboard.service`: stopped, disabled and removed.
- `aiq-testnet-user-stream.service`: retained, enabled and read-only.
- `aiq-testnet-secrets.service`: retained for the read-only Testnet observer.
- Production remains `RISK_LOCKED`.

The retained services cannot generate an order. Before a new project adds any automated service,
review and test its decision and execution authority explicitly.

## Reusable packages

- `market_data`, `orderbook`, `archive`
- `features` (observation calculation only)
- `universe`, `cost`, `risk`
- `execution`, `binance_egress`
- `control`, `notifications`, `monitoring`, `backup`
- `rate_budget`, `orchestration`, `iteration`, `validation`

`strategy` is intentionally empty. The generic `execution` package does not choose when or what to
trade.

## Verification

The post-reset release check passed: 253 full tests; 190 unit, 19 property, 2 contract and 17
security tests; Ruff; strict mypy over 86 source files; Bandit; secret scan; contract/config/
provenance/Compose/deployment validators; and Debian 12/aarch64 host validation.

```bash
make validate-debian-platform
make ci
uv run pytest -q
```

Do not commit credentials, Telegram tokens, passwords, `/run/ai-quant-secrets`,
`/root/aiq-user-inputs`, raw Codex state or runtime account evidence.

The separate immutable `/root/quantify/reference-materials` archive remains outside Git and must be
restored read-only only when provenance-dependent work requires it.
