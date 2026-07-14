import pytest

from ai_quant.services.locked_process import validated_socket_path


def test_locked_process_accepts_only_fixed_runtime_directories() -> None:
    assert str(validated_socket_path("/run/ai-quant-rate/rate.sock")) == (
        "/run/ai-quant-rate/rate.sock"
    )
    with pytest.raises(ValueError, match="outside"):
        validated_socket_path("/var/invalid/rate.sock")
    with pytest.raises(ValueError, match="outside"):
        validated_socket_path("relative.sock")
