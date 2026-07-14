#!/usr/bin/env python3
"""Execute the fully offline paper trading acceptance flow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_quant.demo.paper_flow import run_paper_flow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    print(json.dumps(run_paper_flow(args.workdir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
