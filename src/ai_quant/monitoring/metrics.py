"""Dependency-free Prometheus text exposition for core safety gauges."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlertRule:
    name: str
    metric: str
    threshold: float
    comparison: str
    severity: str
    runbook: str


ALERT_RULES = (
    AlertRule(
        "OrderUnknownTooOld",
        "aiq_order_unknown_age_seconds",
        5,
        ">",
        "P0",
        "runbooks/06_RESTART_RECONCILIATION.md",
    ),
    AlertRule(
        "UnprotectedPosition",
        "aiq_unprotected_position_quantity",
        0,
        ">",
        "P0",
        "runbooks/05_PAUSE_CANCEL_FLATTEN.md",
    ),
    AlertRule(
        "ArchiveDiskHigh",
        "aiq_archive_bytes",
        72 * 1024**3,
        ">=",
        "P1",
        "runbooks/07_DISK_ARCHIVE_INCIDENT.md",
    ),
    AlertRule(
        "DatabaseUnwritable",
        "aiq_database_writable",
        1,
        "<",
        "P0",
        "runbooks/08_DATA_RECOVERY.md",
    ),
)


class MetricRegistry:
    def __init__(self) -> None:
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        if not name.startswith("aiq_"):
            raise ValueError("application metrics must use the aiq_ prefix")
        self._gauges[(name, tuple(sorted(labels.items())))] = value

    def render(self) -> str:
        rows: list[str] = []
        for (name, labels), value in sorted(self._gauges.items()):
            rendered_labels = ""
            if labels:
                values = ",".join(
                    f'{key}="{label.replace(chr(34), chr(92) + chr(34))}"' for key, label in labels
                )
                rendered_labels = "{" + values + "}"
            rows.append(f"{name}{rendered_labels} {value}")
        return "\n".join(rows) + ("\n" if rows else "")
