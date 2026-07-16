# Prompt for another AI/Codex

```text
Continue from this repository as a strategy-free trading execution framework.

Before editing:
1. Detect the clone root and inspect git status/history/remotes.
2. Read README.md, IMPLEMENTATION_STATUS.md, HANDOFF_STATE.md,
   docs/FRAMEWORK_SCOPE.md and docs/adr/0039-strategy-free-framework-reset.md.
3. Review relevant existing code before implementing; do not duplicate retained framework
   functionality.
4. Run make validate-debian-platform, make ci and uv run pytest -q as appropriate.

Important current facts:
- Debian 12 Bookworm/aarch64 is the sole application-host platform.
- The old V4/V5 strategy, automatic campaign, strategy dashboard, strategy replay and strategy
  evidence were intentionally removed.
- ai_quant.strategy is an empty extension point.
- Retained market-data, feature, risk, execution, Binance Testnet probe, native protection,
  user-stream, notification, database and validation modules do not choose trades by themselves.
- The retained Testnet user-stream observer is read-only. No unattended order-submission service
  should exist.
- Production remains RISK_LOCKED.

If the owner asks to build a new trading project, create a new versioned strategy/decision contract
and tests rather than restoring the old strategy. Keep order authority separated, start in Paper or
Testnet, and leave deployment disabled until its own review passes.

Never commit Binance credentials, Telegram tokens, server passwords, raw Codex state,
/run secrets, /root/aiq-user-inputs or account-identifying runtime evidence. Preserve the separate
immutable reference-material archive outside Git.
```
