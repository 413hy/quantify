#!/usr/bin/env python3
"""Summarize fee-adjusted Testnet experiment outcomes by strategy and symbol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_quant.research.testnet_result_review import review_testnet_results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--strategy")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    documents: list[dict[str, Any]] = []
    with arguments.observations.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}") from exc
            if not isinstance(document, dict):
                raise ValueError(f"non-object JSONL at line {line_number}")
            documents.append(document)
    report = review_testnet_results(documents, strategy=arguments.strategy)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
