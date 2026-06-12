"""
Stick-slip assessment — flowchart decision tree.

  Sidebands? → No → MINIMAL
             → Yes → MI ≥ abs_threshold? → Yes → MITIGATE  (absolute severity)
                    → Growing? → No → STABLE
                               → Yes → dMI/dt ≥ rate_threshold? → No → INTENSIFYING
                                                                 → Yes → MITIGATE
"""

from __future__ import annotations

from .ssi import compute_ssi, ssi_class
from .types import (
    INTENSIFYING,
    MITIGATE,
    MINIMAL,
    STABLE,
    SidebandResult,
    StickSlipAssessment,
)


DEFAULT_MITIGATE_THRESHOLD = 0.005
DEFAULT_ABSOLUTE_MITIGATE_MI = 0.05  # SSI = 5% · anything above this is "mitigate now" regardless of trend


def assess(
    sideband_result: SidebandResult,
    growth_rate: float,
    is_growing: bool,
    mitigate_threshold: float = DEFAULT_MITIGATE_THRESHOLD,
    absolute_mitigate_mi: float = DEFAULT_ABSOLUTE_MITIGATE_MI,
    prev_status: str = MINIMAL,
    hysteresis_release_mi: float = 0.03,
    hysteresis_release_rate: float = 0.002,
) -> StickSlipAssessment:
    mi = sideband_result.modulation_index

    if not sideband_result.sidebands_present:
        return StickSlipAssessment(
            status=MINIMAL,
            carrier_frequency=sideband_result.carrier_frequency,
            modulation_frequency=sideband_result.modulation_frequency,
            modulation_index=mi,
            growth_rate=growth_rate,
            sidebands_present=False,
            sidebands_growing=False,
            timestamp=sideband_result.timestamp,
            channel=sideband_result.channel,
        )

    # Hysteresis: once MITIGATE, stay MITIGATE until MI drops well below trigger
    if prev_status == MITIGATE and mi < hysteresis_release_mi:
        return StickSlipAssessment(
            status=STABLE,
            carrier_frequency=sideband_result.carrier_frequency,
            modulation_frequency=sideband_result.modulation_frequency,
            modulation_index=mi,
            growth_rate=growth_rate,
            sidebands_present=True,
            sidebands_growing=False,
            timestamp=sideband_result.timestamp,
            channel=sideband_result.channel,
        )
    if prev_status == MITIGATE:
        return StickSlipAssessment(
            status=MITIGATE,
            carrier_frequency=sideband_result.carrier_frequency,
            modulation_frequency=sideband_result.modulation_frequency,
            modulation_index=mi,
            growth_rate=growth_rate,
            sidebands_present=True,
            sidebands_growing=is_growing,
            timestamp=sideband_result.timestamp,
            channel=sideband_result.channel,
        )

    # Hysteresis: once INTENSIFYING, stay until growth rate drops below release threshold
    if prev_status == INTENSIFYING and growth_rate < hysteresis_release_rate:
        return StickSlipAssessment(
            status=STABLE,
            carrier_frequency=sideband_result.carrier_frequency,
            modulation_frequency=sideband_result.modulation_frequency,
            modulation_index=mi,
            growth_rate=growth_rate,
            sidebands_present=True,
            sidebands_growing=False,
            timestamp=sideband_result.timestamp,
            channel=sideband_result.channel,
        )

    # Absolute MI threshold — immediate MITIGATE regardless of trend
    if mi >= absolute_mitigate_mi:
        return StickSlipAssessment(
            status=MITIGATE,
            carrier_frequency=sideband_result.carrier_frequency,
            modulation_frequency=sideband_result.modulation_frequency,
            modulation_index=mi,
            growth_rate=growth_rate,
            sidebands_present=True,
            sidebands_growing=is_growing,
            timestamp=sideband_result.timestamp,
            channel=sideband_result.channel,
        )

    if not is_growing:
        return StickSlipAssessment(
            status=STABLE,
            carrier_frequency=sideband_result.carrier_frequency,
            modulation_frequency=sideband_result.modulation_frequency,
            modulation_index=mi,
            growth_rate=growth_rate,
            sidebands_present=True,
            sidebands_growing=False,
            timestamp=sideband_result.timestamp,
            channel=sideband_result.channel,
        )

    status = MITIGATE if growth_rate >= mitigate_threshold else INTENSIFYING
    return StickSlipAssessment(
        status=status,
        carrier_frequency=sideband_result.carrier_frequency,
        modulation_frequency=sideband_result.modulation_frequency,
        modulation_index=mi,
        growth_rate=growth_rate,
        sidebands_present=True,
        sidebands_growing=True,
        timestamp=sideband_result.timestamp,
        channel=sideband_result.channel,
    )
