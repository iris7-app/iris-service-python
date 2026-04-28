"""Risk-band classification — mirrors Java :class:`com.mirador.ml.RiskBand`.

Maps a probability ∈ [0, 1] to one of three bands (LOW / MEDIUM / HIGH)
that drive the UI affordance (green / orange / red dot). The band
boundaries are the load-bearing contract :

- ``probability ≤ low_threshold``  → LOW
- ``low_threshold < probability ≤ high_threshold`` → MEDIUM
- ``probability > high_threshold`` → HIGH

Defaults : ``low_threshold = 0.3``, ``high_threshold = 0.7`` (per
shared ADR-0061 §"Risk band thresholds"). Phase E (per ADR) will
move these to a config-server endpoint so ops can re-tune without
re-deploy.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

#: Default low-band ceiling — LOW iff ``p ≤ 0.30``.
DEFAULT_LOW_THRESHOLD: Final[float] = 0.30

#: Default high-band floor — HIGH iff ``p > 0.70``.
DEFAULT_HIGH_THRESHOLD: Final[float] = 0.70


class RiskBand(StrEnum):
    """Classification bucket for a customer's churn probability."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def classify_risk(
    probability: float,
    *,
    low_threshold: float = DEFAULT_LOW_THRESHOLD,
    high_threshold: float = DEFAULT_HIGH_THRESHOLD,
) -> RiskBand:
    """Classify a probability into a risk band.

    Boundary semantics chosen to match Java :class:`RiskBand` exactly :
    ``probability == low_threshold`` is LOW (≤ inclusive on the low
    side), ``probability == high_threshold`` is MEDIUM (≤ inclusive
    on the high side, ``>`` exclusive).

    Raises :exc:`ValueError` if ``low_threshold > high_threshold`` —
    inverted thresholds would silently mis-classify everything as
    MEDIUM, which is worse than a fast failure.
    """
    if low_threshold > high_threshold:
        msg = f"low_threshold must be ≤ high_threshold (got low={low_threshold}, high={high_threshold})"
        raise ValueError(msg)
    if probability <= low_threshold:
        return RiskBand.LOW
    if probability <= high_threshold:
        return RiskBand.MEDIUM
    return RiskBand.HIGH
