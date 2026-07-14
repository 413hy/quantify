#!/usr/bin/env python3
"""Probe a local Unix socket and require the fail-closed startup response."""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("socket", type=Path)
    args = parser.parse_args()

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(args.socket))
        client.sendall(b"{}\n")
        response = json.loads(client.recv(4096))
    expected = {
        "status": "RISK_LOCKED",
        "new_egress_allowed": False,
        "reason_code": "STARTUP_EVIDENCE_MISSING",
    }
    if response != expected:
        raise SystemExit(f"unexpected response: {response!r}")
    print("locked runtime PASS status=RISK_LOCKED new_egress_allowed=false network=none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
