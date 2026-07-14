"""Versioned response classifier: only ambiguous sends enter UNKNOWN."""

from __future__ import annotations

from enum import StrEnum


class SubmissionOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    DEFINITE_FAILURE = "DEFINITE_FAILURE"
    UNKNOWN = "UNKNOWN"


UNKNOWN_503 = "Unknown error, please check your request or try again later."
DEFINITE_503 = {
    "Service Unavailable.",
    "Internal error; unable to process your request. Please try again.",
}


def classify_submission(
    *,
    http_status: int | None,
    binance_code: int | None,
    message: str | None,
    send_timed_out: bool = False,
    connection_lost_after_send: bool = False,
) -> SubmissionOutcome:
    if send_timed_out or connection_lost_after_send:
        return SubmissionOutcome.UNKNOWN
    if http_status is not None and 200 <= http_status < 300:
        return SubmissionOutcome.ACCEPTED
    if http_status == 503 and message == UNKNOWN_503:
        return SubmissionOutcome.UNKNOWN
    if (
        message in DEFINITE_503
        or binance_code in {-1008, -4120}
        or http_status in {418, 429}
        or (http_status is not None and 400 <= http_status < 500)
    ):
        return SubmissionOutcome.DEFINITE_FAILURE
    if http_status is not None and http_status >= 500:
        return SubmissionOutcome.UNKNOWN
    return SubmissionOutcome.DEFINITE_FAILURE
