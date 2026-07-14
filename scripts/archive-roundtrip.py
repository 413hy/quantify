#!/usr/bin/env python3
"""Encrypt one Parquet object, upload it over pinned SFTP, and verify its receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ai_quant.archive.parquet import ArchivedObject
from ai_quant.archive.receipt import (
    RemoteDecryptionReceipt,
    verify_remote_decryption_receipt,
)

_REMOTE_BASE_PATH = re.compile(r"^/[A-Za-z0-9._/-]*$")
_SSH_HOST = re.compile(r"^[A-Za-z0-9.-]+$")
_SSH_USERNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_connection(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "transport",
        "host",
        "port",
        "username",
        "remote_base_path",
        "known_hosts_file",
        "ssh_private_key_file",
        "age_recipient_file",
        "receipt_key_id",
        "receipt_verify_public_key_file",
        "remote_can_decrypt_and_sign_receipt",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("archive connection field set is not exact")
    if document["schema_version"] != "1.0.0" or document["transport"] != "SFTP":
        raise ValueError("unsupported archive connection")
    if document["remote_can_decrypt_and_sign_receipt"] is not True:
        raise ValueError("remote decryption receipt capability is required")
    if not isinstance(document["port"], int) or not 1 <= document["port"] <= 65535:
        raise ValueError("invalid SFTP port")
    if not isinstance(document["host"], str) or not _SSH_HOST.fullmatch(document["host"]):
        raise ValueError("invalid SFTP host")
    if not isinstance(document["username"], str) or not _SSH_USERNAME.fullmatch(
        document["username"]
    ):
        raise ValueError("invalid SFTP username")
    return document


def _sftp_command(connection: dict[str, Any]) -> list[str]:
    return [
        "/usr/bin/sftp",
        "-q",
        "-b",
        "-",
        "-P",
        str(connection["port"]),
        "-i",
        connection["ssh_private_key_file"],
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={connection['known_hosts_file']}",
        f"{connection['username']}@{connection['host']}",
    ]


def _run_sftp(connection: dict[str, Any], batch: str, *, check: bool = True) -> bool:
    completed = subprocess.run(  # noqa: S603
        _sftp_command(connection),
        input=batch.encode(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError("SFTP operation failed")
    return completed.returncode == 0


def _remote_path(base: object, *parts: str) -> str:
    if not isinstance(base, str) or not _REMOTE_BASE_PATH.fullmatch(base):
        raise ValueError("remote_base_path must be absolute inside the SFTP root")
    path = PurePosixPath(base, *parts)
    if ".." in path.parts:
        raise ValueError("unsafe remote path")
    return str(path)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="ascii").splitlines() if line.strip()}


def _record_seen(path: Path, receipt_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="ascii") as handle:
        handle.write(receipt_id + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", type=Path, required=True)
    parser.add_argument("--object-root", type=Path, required=True)
    parser.add_argument("--relative-path", required=True)
    parser.add_argument("--row-count", type=int, required=True)
    parser.add_argument("--schema-version", required=True)
    parser.add_argument("--stream", choices=("depth", "aggTrade"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--hour", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--seen-receipts", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    arguments = parser.parse_args()

    connection = _load_connection(arguments.connection)
    root = arguments.object_root.resolve()
    object_path = (root / arguments.relative_path).resolve()
    if not object_path.is_relative_to(root) or not object_path.is_file():
        raise ValueError("object must be a regular file below object root")
    hour = datetime.fromisoformat(arguments.hour.replace("Z", "+00:00"))
    if hour.tzinfo is None or hour.utcoffset() != UTC.utcoffset(hour):
        raise ValueError("hour must be UTC")
    plaintext_sha256 = _sha256(object_path)
    archived = ArchivedObject(
        relative_path=arguments.relative_path,
        absolute_path=object_path,
        sha256=plaintext_sha256,
        size_bytes=object_path.stat().st_size,
        row_count=arguments.row_count,
        schema_version=arguments.schema_version,
        stream=arguments.stream,
        symbol=arguments.symbol,
        hour=hour,
    )
    recipient = Path(connection["age_recipient_file"]).read_text(encoding="ascii").strip()
    if not recipient.startswith("age1") or "\n" in recipient:
        raise ValueError("age recipient must be one X25519 recipient")
    evidence_dir = arguments.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    receipt_id = uuid.uuid4().hex

    with tempfile.TemporaryDirectory(prefix="aiq-archive-send-") as temp:
        staging = Path(temp)
        ciphertext = staging / f"{receipt_id}.age"
        completed = subprocess.run(  # noqa: S603
            [
                "/usr/bin/age",
                "--recipient",
                recipient,
                "--output",
                str(ciphertext),
                str(object_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("age encryption failed")
        ciphertext_sha256 = _sha256(ciphertext)
        uploaded_at = datetime.now(UTC)
        metadata = {
            "schema_version": "1.0.0",
            "receipt_id": receipt_id,
            "object_path": arguments.relative_path,
            "plaintext_sha256": archived.sha256,
            "plaintext_size_bytes": archived.size_bytes,
            "ciphertext_sha256": ciphertext_sha256,
            "ciphertext_size_bytes": ciphertext.stat().st_size,
            "uploaded_at": uploaded_at.isoformat(),
            "encryption_format": "age-v1-x25519",
            "inspection_type": "PARQUET",
            "expected_parquet_row_count": arguments.row_count,
            "expected_parquet_schema_version": arguments.schema_version,
        }
        metadata_path = staging / f"{receipt_id}.metadata.json"
        _write_json(metadata_path, metadata)
        incoming_ciphertext = _remote_path(
            connection["remote_base_path"], "incoming", f"{receipt_id}.age"
        )
        incoming_metadata = _remote_path(
            connection["remote_base_path"], "incoming", f"{receipt_id}.metadata.json"
        )
        _run_sftp(
            connection,
            f"put {ciphertext} {incoming_ciphertext}.part\n"
            f"chmod 0640 {incoming_ciphertext}.part\n"
            f"rename {incoming_ciphertext}.part {incoming_ciphertext}\n"
            f"put {metadata_path} {incoming_metadata}.part\n"
            f"chmod 0640 {incoming_metadata}.part\n"
            f"rename {incoming_metadata}.part {incoming_metadata}\n",
        )

        receipt_path = staging / f"{receipt_id}.receipt.json"
        remote_receipt = _remote_path(
            connection["remote_base_path"], "receipts", f"{receipt_id}.json"
        )
        deadline = time.monotonic() + arguments.timeout_seconds
        while time.monotonic() < deadline:
            if _run_sftp(connection, f"get {remote_receipt} {receipt_path}\n", check=False):
                break
            time.sleep(2)
        else:
            raise TimeoutError("remote signed receipt was not available before timeout")

        receipt = RemoteDecryptionReceipt.model_validate_json(receipt_path.read_bytes())
        public_key = serialization.load_pem_public_key(
            Path(connection["receipt_verify_public_key_file"]).read_bytes()
        )
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("receipt verification key must be Ed25519")
        seen = _load_seen(arguments.seen_receipts)
        if not verify_remote_decryption_receipt(
            receipt,
            archived,
            public_key,
            expected_ciphertext_sha256=ciphertext_sha256,
            expected_ciphertext_size_bytes=ciphertext.stat().st_size,
            expected_signer_key_id=connection["receipt_key_id"],
            seen_receipt_ids=seen,
        ):
            raise ValueError("remote decryption receipt verification failed")

        final_ciphertext = evidence_dir / ciphertext.name
        final_metadata = evidence_dir / metadata_path.name
        final_receipt = evidence_dir / receipt_path.name
        for source, target in (
            (ciphertext, final_ciphertext),
            (metadata_path, final_metadata),
            (receipt_path, final_receipt),
        ):
            os.replace(source, target)
            os.chmod(target, 0o600)
        _record_seen(arguments.seen_receipts, receipt_id)
        result = {
            "status": "REMOTE_VERIFIED",
            "receipt_id": receipt_id,
            "object_path": arguments.relative_path,
            "plaintext_sha256": plaintext_sha256,
            "ciphertext_sha256": ciphertext_sha256,
            "remote_uri": receipt.remote_uri,
            "receipt_path": str(final_receipt),
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
