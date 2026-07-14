"""Race-aware loading for service-owned private files outside the release tree."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ai_quant.rate_budget.authorization import AuthorizationDenied


def read_private_file(
    path: Path,
    *,
    forbidden_repository_root: Path,
    maximum_bytes: int,
    unsafe_reason: str,
) -> bytes:
    """Read an exact 0400 current-EUID file without following path links."""
    if not path.is_absolute() or maximum_bytes < 1:
        raise AuthorizationDenied(unsafe_reason)
    try:
        resolved = path.resolve(strict=True)
        repository_root = forbidden_repository_root.resolve(strict=True)
    except OSError as exc:
        raise AuthorizationDenied(unsafe_reason) from exc
    if resolved != path or resolved.is_relative_to(repository_root):
        raise AuthorizationDenied(unsafe_reason)

    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_uid != os.geteuid()
            or metadata.st_size < 1
            or metadata.st_size > maximum_bytes
        ):
            raise AuthorizationDenied(unsafe_reason)
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        final_metadata = os.fstat(descriptor)
        if (
            len(content) != metadata.st_size
            or final_metadata.st_dev != metadata.st_dev
            or final_metadata.st_ino != metadata.st_ino
            or final_metadata.st_size != metadata.st_size
            or final_metadata.st_mtime_ns != metadata.st_mtime_ns
            or final_metadata.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise AuthorizationDenied(unsafe_reason)
        return content
    except OSError as exc:
        raise AuthorizationDenied(unsafe_reason) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
