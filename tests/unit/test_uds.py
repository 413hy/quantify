from __future__ import annotations

import socket
import struct
import threading
from pathlib import Path

import pytest

from ai_quant.services.uds import (
    BoundedUnixClient,
    BoundedUnixServer,
    UdsProtocolError,
    encode_frame,
    receive_frame,
)


def test_frame_round_trip_over_unix_socket() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        left.sendall(encode_frame({"message_type": "ReserveRequest", "message_id": "msg-00000001"}))
        assert receive_frame(right) == {
            "message_type": "ReserveRequest",
            "message_id": "msg-00000001",
        }
    finally:
        left.close()
        right.close()


def test_oversized_frame_is_rejected_before_payload_read() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        left.sendall(struct.pack("!I", 4097))
        with pytest.raises(UdsProtocolError, match="FRAME_SIZE_INVALID"):
            receive_frame(right, max_bytes=4096)
    finally:
        left.close()
        right.close()


def test_duplicate_json_key_is_rejected() -> None:
    payload = b'{"message_id":"one","message_id":"two"}'
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        left.sendall(struct.pack("!I", len(payload)) + payload)
        with pytest.raises(UdsProtocolError, match="DUPLICATE_JSON_KEY"):
            receive_frame(right)
    finally:
        left.close()
        right.close()


def test_non_object_json_is_rejected() -> None:
    payload = b"[]"
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        left.sendall(struct.pack("!I", len(payload)) + payload)
        with pytest.raises(UdsProtocolError, match="FRAME_DOCUMENT_NOT_OBJECT"):
            receive_frame(right)
    finally:
        left.close()
        right.close()


def test_server_refuses_world_accessible_runtime_directory(tmp_path: Path) -> None:
    tmp_path.chmod(0o777)
    server = BoundedUnixServer(tmp_path / "rate.sock", lambda request, peer: request)
    with pytest.raises(UdsProtocolError, match="RUNTIME_DIRECTORY_UNSAFE"):
        server.start()


def test_server_socket_mode_and_cleanup(tmp_path: Path) -> None:
    tmp_path.chmod(0o770)
    socket_path = tmp_path / "rate.sock"
    server = BoundedUnixServer(socket_path, lambda request, peer: request)
    server.start()
    try:
        assert socket_path.stat().st_mode & 0o777 == 0o660
        assert server._listener is not None
        assert server._listener.family == socket.AF_UNIX
    finally:
        server.close()
    assert not socket_path.exists()


def test_server_sends_no_frame_for_one_way_message(tmp_path: Path) -> None:
    tmp_path.chmod(0o770)
    socket_path = tmp_path / "rate.sock"
    server = BoundedUnixServer(socket_path, lambda request, peer: None)
    server.start()
    worker = threading.Thread(target=server.serve_one)
    worker.start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(1)
        client.connect(str(socket_path))
        client.sendall(encode_frame({"message_type": "HeaderObservation"}))
        assert client.recv(1) == b""
    finally:
        client.close()
        worker.join(timeout=1)
        server.close()
    assert not worker.is_alive()


def test_bounded_client_supports_request_and_one_way_message(tmp_path: Path) -> None:
    tmp_path.chmod(0o770)
    socket_path = tmp_path / "rate.sock"
    responses = iter(({"status": "ok"}, None))
    server = BoundedUnixServer(socket_path, lambda request, peer: next(responses))
    server.start()
    client = BoundedUnixClient(socket_path)
    try:
        worker = threading.Thread(target=server.serve_one)
        worker.start()
        assert client.request({"message_type": "ReserveRequest"}) == {"status": "ok"}
        worker.join(timeout=1)
        worker = threading.Thread(target=server.serve_one)
        worker.start()
        client.notify({"message_type": "SendOutcome"})
        worker.join(timeout=1)
    finally:
        server.close()
    assert not worker.is_alive()
