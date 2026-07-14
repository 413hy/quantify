"""Cryptographic verification of an exact remote archive object receipt."""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_quant.archive.parquet import ArchivedObject

_RELATIVE_ARCHIVE_PATH = re.compile(r"^[A-Za-z0-9._=/-]+$")


class RemoteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0.0"
    object_path: str
    object_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_size_bytes: int = Field(gt=0)
    remote_uri: str
    remote_etag: str
    uploaded_at: datetime
    signer_key_id: str
    signature_base64: str

    @field_validator("uploaded_at")
    @classmethod
    def uploaded_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("uploaded_at must be timezone-aware UTC")
        return value

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"signature_base64"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def verify_remote_receipt(
    receipt: RemoteReceipt,
    archived: ArchivedObject,
    public_key: Ed25519PublicKey,
) -> bool:
    if (
        receipt.object_path != archived.relative_path
        or receipt.object_sha256 != archived.sha256
        or receipt.object_size_bytes != archived.size_bytes
    ):
        return False
    try:
        signature = base64.b64decode(receipt.signature_base64, validate=True)
        public_key.verify(signature, receipt.signing_bytes())
    except (InvalidSignature, ValueError):
        return False
    return True


class RemoteDecryptionReceipt(BaseModel):
    """Signed proof that the remote endpoint decrypted and inspected one exact object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.1.0"] = "1.1.0"
    receipt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    object_path: str
    remote_ciphertext_path: str
    plaintext_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plaintext_size_bytes: int = Field(gt=0)
    ciphertext_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ciphertext_size_bytes: int = Field(gt=0)
    remote_uri: str
    remote_etag: str
    uploaded_at: datetime
    decrypted_at: datetime
    encryption_format: Literal["age-v1-x25519"]
    inspection_type: Literal["PARQUET"]
    inspection_status: Literal["PASS"]
    parquet_row_count: int = Field(gt=0)
    parquet_schema_version: str
    signer_key_id: str
    signature_base64: str

    @field_validator("uploaded_at", "decrypted_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("receipt timestamps must be timezone-aware UTC")
        return value

    @field_validator("object_path", "remote_ciphertext_path")
    @classmethod
    def paths_are_relative_and_bounded(cls, value: str) -> str:
        components = value.split("/")
        if (
            not value
            or value.startswith("/")
            or not _RELATIVE_ARCHIVE_PATH.fullmatch(value)
            or any(item in {"", ".", ".."} for item in components)
        ):
            raise ValueError("receipt paths must be normalized relative paths")
        return value

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"signature_base64"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def verify_remote_decryption_receipt(
    receipt: RemoteDecryptionReceipt,
    archived: ArchivedObject,
    public_key: Ed25519PublicKey,
    *,
    expected_ciphertext_sha256: str,
    expected_ciphertext_size_bytes: int,
    expected_signer_key_id: str,
    seen_receipt_ids: set[str] | None = None,
) -> bool:
    """Verify exact object binding, remote inspection proof, signature, and replay identity."""
    if (
        receipt.object_path != archived.relative_path
        or receipt.plaintext_sha256 != archived.sha256
        or receipt.plaintext_size_bytes != archived.size_bytes
        or receipt.ciphertext_sha256 != expected_ciphertext_sha256
        or receipt.ciphertext_size_bytes != expected_ciphertext_size_bytes
        or receipt.remote_etag != expected_ciphertext_sha256
        or receipt.parquet_row_count != archived.row_count
        or receipt.parquet_schema_version != archived.schema_version
        or receipt.signer_key_id != expected_signer_key_id
        or receipt.decrypted_at < receipt.uploaded_at
        or (seen_receipt_ids is not None and receipt.receipt_id in seen_receipt_ids)
    ):
        return False
    try:
        signature = base64.b64decode(receipt.signature_base64, validate=True)
        public_key.verify(signature, receipt.signing_bytes())
    except (InvalidSignature, ValueError):
        return False
    return True
