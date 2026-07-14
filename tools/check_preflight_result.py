#!/usr/bin/env python3
"""Accept only the two known immutable-document anchor defects."""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED = {
    "runbooks/07_DISK_ARCHIVE_INCIDENT.md: missing anchor: 00_HOST_RATE_CONTROL.md#5-故障语义",
    "runbooks/09_UPGRADE_ROLLBACK.md: missing anchor: 00_HOST_RATE_CONTROL.md#5-故障语义",
}


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_preflight_result.py REPORT ORIGINAL_EXIT")
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    failures = set(report["checks"]["markdown_links"]["failures"])
    all_other = []
    for name, check in report["checks"].items():
        if name == "markdown_links" or not isinstance(check, dict):
            continue
        all_other.extend(check.get("failures", []))
    if failures != EXPECTED or all_other:
        print(
            json.dumps(
                {"unexpected_anchor_failures": sorted(failures ^ EXPECTED), "other": all_other}
            )
        )
        return 1
    if int(sys.argv[2]) != 1 or report.get("failure_count") != 2:
        print("preflight exit/failure count changed unexpectedly")
        return 1
    print("preflight PASS with exactly two documented immutable-source anchor defects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
