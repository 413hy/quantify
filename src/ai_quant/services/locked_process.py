"""Fail-closed Unix-socket process used until startup evidence is implemented."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

ALLOWED_SOCKET_DIRECTORIES = frozenset(
    {
        Path("/run/ai-quant-business"),
        Path("/run/ai-quant-egress"),
        Path("/run/ai-quant-rate"),
    }
)


def validated_socket_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or path.parent not in ALLOWED_SOCKET_DIRECTORIES:
        raise ValueError("socket path is outside the fixed runtime directories")
    if path.name in {"", ".", ".."}:
        raise ValueError("socket filename is invalid")
    return path


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await asyncio.wait_for(reader.readline(), timeout=1.0)
        response: dict[str, Any] = {
            "status": "RISK_LOCKED",
            "new_egress_allowed": False,
            "reason_code": "STARTUP_EVIDENCE_MISSING",
        }
        writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        await writer.drain()
    except (TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def run() -> None:
    socket_path = validated_socket_path(os.environ["AIQ_SOCKET_PATH"])
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=socket_path)
    socket_path.chmod(0o660)
    async with server:
        await server.serve_forever()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
