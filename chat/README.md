# Chat continuity package

This directory contains only a sanitized continuation prompt for the strategy-free framework.

File: `CONTINUE_WITH_ANOTHER_AI.md`, a ready-to-copy prompt for another Codex/AI agent.

This is a sanitized handoff, not a verbatim raw conversation. The raw `/root/.codex` directory is
intentionally not committed. It contains authentication state,
internal tool records and product metadata that do not belong in source control. In particular,
`auth.json`, SQLite state, shell snapshots and raw JSONL rollouts must never be added to this
repository. This handoff contains no credentials or production secrets.

The original `/root/quantify/reference-materials` directory is outside this Git repository. It must
be backed up and restored separately as read-only source material. Never reconstruct, edit or
silently replace it from this handoff.
