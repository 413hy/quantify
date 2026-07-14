from pathlib import Path

import pytest

from ai_quant.common.config import (
    ConfigurationError,
    reject_embedded_secret_values,
    validate_config,
)


def test_secret_value_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="embedded secret"):
        reject_embedded_secret_values({"binance_api_key": "actual-secret-material"})


def test_runtime_secret_placeholder_is_allowed() -> None:
    reject_embedded_secret_values({"binance_api_key": "${BINANCE_API_KEY}"})


def test_known_config_validates() -> None:
    root = Path(__file__).resolve().parents[2]
    config = validate_config(root / "config/risk.example.yaml", root / "config/risk.schema.json")
    assert isinstance(config, dict)
