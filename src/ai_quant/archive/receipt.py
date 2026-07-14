"""Cryptographic verification of an exact remote archive object receipt."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_quant.archive.parquet import ArchivedObject


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
