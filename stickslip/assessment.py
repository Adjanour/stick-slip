"""
Stick-slip assessment — flowchart decision tree.

  Sidebands? → No  → MINIMAL
             → Yes → Growing? → No  → STABLE
                               → Yes → dMI/dt ≥ threshold? → No  → INTENSIFYING
                                                            → Yes → MITIGATE
"""

from __future__ import annotations

from .types import (
    INTENSIFYING,
    MITIGATE,
    MINIMAL,
    STABLE,
    SidebandResult,
    StickSlipAssessment,
)


# 0.5 % of carrier magnitude per second — above this, mitigation is recommended
DEFAULT_MITIGATE_THRESHOLD = 0.005


def assess(
    sideband_result: SidebandResult,
    growth_rate: float,
    is_growing: bool,
    mitigate_threshold: float = DEFAULT_MITIGATE_THRESHOLD,
) -> StickSlipAssessment:
    if not sideband_result.sidebands_present:
        return StickSlipAssessment(
            status=MINIMAL,
            carrier_frequency=sideband_result.carrier_frequency,
            modulation_frequency=sideband_result.modulation_frequency,
            modulation_index=sideband_result.modulation_index,
            growth_rate=growth_rate,
            sidebands_present=False,
            sidebands_growing=False,
            timestamp=sideband_result.timestamp,
            channel=sideband_result.channel,
        )

    if not is_growing:
        return StickSlipAssessment(
            status=STABLE,
            carrier_frequency=sideband_result.carrier_frequency,
            modulation_frequency=sideband_result.modulation_frequency,
            modulation_index=sideband_result.modulation_index,
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
        modulation_index=sideband_result.modulation_index,
        growth_rate=growth_rate,
        sidebands_present=True,
        sidebands_growing=True,
        timestamp=sideband_result.timestamp,
        channel=sideband_result.channel,
    )
