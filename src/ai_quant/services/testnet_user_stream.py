"""Run the exact-destination Testnet user-data stream evidence observer."""

from __future__ import annotations

import argparse
import fcntl
import signal
from pathlib import Path

from ai_quant.binance_egress.testnet_probe import BinanceTestnetClient, _credential
from ai_quant.binance_egress.testnet_user_stream import (
    TestnetUserDataStream,
    UserDataEventJournal,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--api-key-file", required=True, type=Path)
    result.add_argument("--api-secret-file", required=True, type=Path)
    result.add_argument("--repository-root", required=True, type=Path)
    result.add_argument("--evidence-file", required=True, type=Path)
    result.add_argument("--state-file", required=True, type=Path)
    result.add_argument("--lock-file", required=True, type=Path)
    result.add_argument("--keepalive-interval-seconds", type=int, default=1_800)
    result.add_argument("--rotate-no-later-than-seconds", type=int, default=84_600)
    return result


def main() -> int:
    arguments = parser().parse_args()
    arguments.lock_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with arguments.lock_file.open("w", encoding="ascii") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("TESTNET_USER_STREAM_ALREADY_RUNNING")
            return 3
        client = BinanceTestnetClient(
            _credential(arguments.api_key_file, arguments.repository_root),
            _credential(arguments.api_secret_file, arguments.repository_root),
        )
        client.synchronize_time()
        observer = TestnetUserDataStream(
            client,
            UserDataEventJournal(arguments.evidence_file, arguments.state_file),
            keepalive_interval_seconds=arguments.keepalive_interval_seconds,
            rotate_no_later_than_seconds=arguments.rotate_no_later_than_seconds,
        )

        def request_stop(_signal_number: int, _frame: object) -> None:
            observer.stop()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        return observer.run()


if __name__ == "__main__":
    raise SystemExit(main())
