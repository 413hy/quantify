#!/usr/bin/env python3
"""Render a checked nftables fragment to standard output without applying it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ai_quant.binance_egress.nftables_policy import render_nftables_policy
from ai_quant.common.config import load_strict_document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    document: Any = load_strict_document(args.plan)
    if not isinstance(document, dict):
        raise SystemExit("nftables plan must be an object")
    sys.stdout.write(render_nftables_policy(document))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
