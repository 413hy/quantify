# Prompt for another AI/Codex

Copy the text inside the block into a fresh AI session after cloning this repository.

```text
Continue this quantitative trading project from the repository's actual state. Treat repository
code, current documentation and preserved immutable source materials as authoritative; do not rely
only on this prompt or previous-agent claims.

Before changing anything:
1. Detect the clone root with `git rev-parse --show-toplevel` and inspect `git status`, current
   branch, remotes and recent history.
2. Read completely:
   - IMPLEMENTATION_STATUS.md
   - HANDOFF_STATE.md
   - docs/testnet-campaign.md
   - docs/adr/0037-three-coin-predictive-fast-context-v5-5.md
   - docs/adr/0038-one-minute-cadence-v5-6.md
   - chat/SESSION_HANDOFF_2026-07-16.md
3. Review the relevant existing implementation deeply against those documents and explicit user
   requirements before editing. Do not superficially inspect or duplicate an existing feature.
4. Run `make validate-debian-platform`, `make ci` and `uv run pytest -q` as appropriate. Record
   actual results; never copy expected results as proof.

Current facts to verify:
- Debian 12 Bookworm/aarch64 on Oracle Cloud is the sole supported application host. Do not add or
  recommend instructions for another distribution.
- The running environment is Binance USDⓈ-M Futures Testnet only. Production remains RISK_LOCKED.
- The current strategy is TESTNET_EXPERIMENT_OF_PA_V5_6.
- It evaluates once per minute over BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT and XRPUSDT.
- It can hold zero to five symbols, uses approximately 1 USDT margin and the exchange maximum
  initial leverage, submits confirmed market entries, and installs native stop/target protection.
- There is no elapsed holding-time exit. Five positions are capacity, not a target.
- Three-coin fast breadth requires strong predictive and order-flow authority; weak signals must
  remain rejected. The strategy is unvalidated and no profitability/win-rate claim is allowed.
- Campaign, user stream, Telegram dashboard and secret materializer are systemd services that run
  independently of Codex and are intended to restart at boot.

Safety and repository boundaries:
- Never commit Binance credentials, Telegram token/chat IDs, server passwords, /run secrets,
  /root/aiq-user-inputs, raw Codex state or account-identifying runtime evidence.
- The separate historical `/root/quantify/reference-materials` directory is not in Git. If
  provenance-dependent work requires it and it is missing, ask the owner to restore the original
  read-only archive; do not recreate it from memory.
- Do not modify provenance-protected config/contracts/runbooks without an explicit owner-approved
  amendment.
- Testnet changes may be implemented and deployed within owner authorization. Do not enable
  production transport, inject production credentials or weaken RISK_LOCKED.
- Preserve historical ADRs and losing/rejected evidence. Do not manufacture samples or erase audit
  history to improve reported performance.

Work autonomously within these boundaries. When asked to change or build, implement, verify and
deploy the safe Testnet change instead of only analyzing it. Explain exact evidence and remaining
limits without promising profit or a 100% win rate.
```
