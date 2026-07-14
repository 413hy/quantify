from ai_quant.common.runtime import RuntimeState, RuntimeStatus


def test_runtime_defaults_fail_closed() -> None:
    status = RuntimeStatus()
    assert status.state is RuntimeState.RISK_LOCKED
    assert status.new_entries_allowed is False
    assert status.reason_code == "STARTUP_EVIDENCE_MISSING"


def test_runtime_rejects_unknown_fields() -> None:
    try:
        RuntimeStatus(unexpected=True)  # type: ignore[call-arg]
    except ValueError:
        pass
    else:
        raise AssertionError("unknown runtime field was accepted")
