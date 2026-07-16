# Chat continuity package

This directory preserves a sanitized, repository-local handoff for continuing the project after a
host reinstall or in a new AI session.

Files:

- `SESSION_HANDOFF_2026-07-14.md`: historical sanitized session decisions and early implementation
  context.
- `SESSION_HANDOFF_2026-07-16.md`: current Debian/Testnet/V5.6 deployment and publication handoff.
- `CONTINUE_WITH_ANOTHER_AI.md`: a ready-to-copy prompt for another Codex/AI agent.

These are sanitized summaries, not verbatim raw conversations. The raw `/root/.codex` directory is
intentionally not committed. It contains authentication state,
internal tool records and product metadata that do not belong in source control. In particular,
`auth.json`, SQLite state, shell snapshots and raw JSONL rollouts must never be added to this
repository. This handoff contains no credentials or production secrets.

The original `/root/quantify/reference-materials` directory is outside this Git repository. It must
be backed up and restored separately as read-only source material. Never reconstruct, edit or
silently replace it from this handoff.
