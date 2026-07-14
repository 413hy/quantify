"""Executable fail-closed host rate-budget Unix-domain-socket service."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

from ai_quant.rate_budget.application import RateBudgetApplication
from ai_quant.rate_budget.authorization import load_runtime_trust_bundle
from ai_quant.rate_budget.policy import load_runtime_endpoint_catalog
from ai_quant.rate_budget.postgres import PostgresRateAuthority, load_database_dsn
from ai_quant.services.locked_process import validated_socket_path
from ai_quant.services.uds import BoundedUnixServer


def _path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required file setting: {name}")
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError(f"file setting must be absolute: {name}")
    return path


def _sha256_pin(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError(f"invalid SHA-256 pin: {name}")
    return value


def run() -> None:
    now = datetime.now(UTC)
    keyring_path = _path("AIQ_HOST_CONFIG_KEYRING_FILE")
    keyring_schema_path = _path("AIQ_HOST_CONFIG_KEYRING_SCHEMA_FILE")
    keyring_hash = _sha256_pin("AIQ_HOST_CONFIG_KEYRING_HASH")
    trust_bundle = load_runtime_trust_bundle(
        _path("AIQ_CAPABILITY_TRUST_BUNDLE_FILE"),
        _path("AIQ_CAPABILITY_TRUST_BUNDLE_SCHEMA_FILE"),
        keyring_path,
        keyring_schema_path,
        expected_keyring_hash=keyring_hash,
        now=now,
    )
    catalog = load_runtime_endpoint_catalog(
        _path("AIQ_ENDPOINT_CATALOG_FILE"),
        _path("AIQ_ENDPOINT_CATALOG_SCHEMA_FILE"),
        keyring_path,
        keyring_schema_path,
        expected_keyring_hash=keyring_hash,
        request_schema_path=_path("AIQ_GATEWAY_REQUEST_SCHEMA_FILE"),
        source_artifact_root=_path("AIQ_ENDPOINT_SOURCE_ARTIFACT_ROOT"),
        now=now,
    )
    authority = PostgresRateAuthority(
        dsn=load_database_dsn(_path("AIQ_HOST_CONTROL_DATABASE_DSN_FILE")),
        instance_id=os.environ["AIQ_RATE_ALLOCATOR_INSTANCE_ID"],
    )
    authority.assert_runtime_ready(catalog)
    fencing_epoch = authority.acquire_or_renew_lease(ttl_seconds=30)
    application = RateBudgetApplication(
        protocol_schema_path=_path("AIQ_RATE_UDS_SCHEMA_FILE"),
        trust_bundle=trust_bundle,
        endpoint_catalog=catalog,
        authority=authority,
    )
    server = BoundedUnixServer(
        validated_socket_path(os.environ["AIQ_SOCKET_PATH"]),
        application,
        accept_timeout_seconds=5,
    )
    server.start()
    next_renewal = time.monotonic() + 10
    try:
        while True:
            if time.monotonic() >= next_renewal:
                renewed_epoch = authority.acquire_or_renew_lease(ttl_seconds=30)
                if renewed_epoch != fencing_epoch:
                    raise RuntimeError("fencing epoch changed during lease renewal")
                next_renewal = time.monotonic() + 10
            try:
                server.serve_one()
            except TimeoutError:
                pass
    finally:
        server.close()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
