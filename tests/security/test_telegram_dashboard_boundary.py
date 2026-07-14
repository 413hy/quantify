from pathlib import Path

from ai_quant.services import telegram_dashboard


def test_dashboard_has_no_exchange_or_execution_capability() -> None:
    source = Path(telegram_dashboard.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "BinanceTestnetClient",
        "api_secret_file",
        "place_order",
        "place_algo_order",
        "change_initial_leverage",
        "execution.sock",
    ):
        assert forbidden not in source


def test_dashboard_unit_mounts_only_read_evidence_and_telegram_state() -> None:
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy/systemd/aiq-telegram-dashboard.service").read_text(
        encoding="utf-8"
    )

    assert "ReadOnlyPaths=" in unit
    assert "/var/lib/ai-quant/evidence/testnet" in unit
    assert "ReadWritePaths=/var/lib/ai-quant/telegram /run/ai-quant" in unit
    assert "binance-testnet-api-secret" not in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit
