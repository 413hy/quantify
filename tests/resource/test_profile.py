from __future__ import annotations

import resource
import time
from pathlib import Path

from ai_quant.demo.paper_flow import run_paper_flow


def test_paper_flow_fits_kr_2c_12g_profile(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run_paper_flow(tmp_path)
    elapsed = time.monotonic() - started
    peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert result["protection_healthy"] is True
    assert elapsed < 30
    assert peak_rss_kib < 2_621_440
