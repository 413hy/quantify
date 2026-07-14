# Python dependency vulnerability audit method

`pip-audit 2.10.1` did not recognize `uv.lock` as a supported lockfile. The attempted locked-project
mode stopped with `no lockfiles found`; it did not produce a successful audit. Strict local mode
also correctly reported the editable root project as a collection error.

The retained `pip-audit.json` therefore audits the isolated `.venv` created by
`uv sync --frozen --all-groups`, with the editable root distribution skipped. It contains 104
dependency records, one documented skip (the root project), and zero known vulnerabilities as of
2026-07-14. The root project's own Python source was scanned separately by Bandit and the test
suite. This is a time-bounded development scan, not an image OS vulnerability attestation.
