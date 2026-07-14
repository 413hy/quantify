#!/usr/bin/env python3
"""Create a short-lived Ed25519 approval for one exact bootstrap plan."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--approver", required=True)
    parser.add_argument("--expires-minutes", type=int, default=30)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.expires_minutes <= 60:
        raise SystemExit("approval expiry must be 1..60 minutes")
    metadata = args.private_key.lstat()
    if args.private_key.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit("private key must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SystemExit("private key mode must be 0600")
    key_check = subprocess.run(  # noqa: S603
        [
            "/usr/bin/openssl",
            "pkey",
            "-in",
            str(args.private_key),
            "-text",
            "-noout",
        ],
        check=True,
        capture_output=True,
    )
    if b"ED25519" not in key_check.stdout.upper():
        raise SystemExit("private key must be Ed25519")
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    content = {
        "schema_version": "1.0.0",
        "action": "bootstrap-apply",
        "plan_sha256": sha256(args.plan),
        "approved_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + dt.timedelta(minutes=args.expires_minutes))
        .isoformat()
        .replace("+00:00", "Z"),
        "approver": args.approver,
    }
    with tempfile.NamedTemporaryFile() as message, tempfile.NamedTemporaryFile() as signature:
        message.write(canonical_bytes(content))
        message.flush()
        subprocess.run(  # noqa: S603
            [
                "/usr/bin/openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(args.private_key),
                "-rawin",
                "-in",
                message.name,
                "-out",
                signature.name,
            ],
            check=True,
        )
        encoded = base64.b64encode(signature.read()).decode()
    envelope = {"content": content, "signature": encoded}
    args.output.write_bytes(canonical_bytes(envelope) + b"\n")
    os.chmod(args.output, 0o400)
    print(f"bootstrap approval created output={args.output} plan_sha256={content['plan_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
