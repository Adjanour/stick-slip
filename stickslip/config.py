"""
Centralised configuration — all tunable parameters in one place.

Load from TOML or use defaults.  CLI args override individual fields.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .types import BHAConfig, SegmentSpec


@dataclass(frozen=True)
class PipelineConfig:
    window_seconds: float = 5.0
    sample_rate: float = 50.0
    chunk_size: int = 10
    channel: str = "RPM"
    duration_seconds: float = 15.0
    bit_depth: float = 1000.0
    baseline_rpm: float = 100.0
    baseline_wob: float = 50000.0


@dataclass(frozen=True)
class FilterConfig:
    low_hz: float = 0.5
    high_hz: float = 8.0
    order: int = 4


@dataclass(frozen=True)
class DrillStringConfig:
    shear_modulus: float = 80e9
    length: float = 1000.0
    material_density: float = 7850.0


@dataclass(frozen=True)
class SidebandConfig:
    max_order: int = 3
    min_ratio: float = 0.05
    search_window_hz: float = 0.15


@dataclass(frozen=True)
class AssessmentConfig:
    growing_threshold: float = 0.001
    mitigate_threshold: float = 0.005


@dataclass(frozen=True)
class MitigationConfig:
    rpm_boost: float = 1.15
    wob_cut: float = 0.70
    energy_wob_cut: float = 0.50
    ramp_step: float = 0.05
    mitigate_rpm_boost: float = 1.10
    mitigate_wob_cut: float = 0.80
    energy_rpm_boost: float = 1.20
    energy_building_wob_cut: float = 0.85


def _default_bha() -> BHAConfig:
    return BHAConfig(
        g_base=79e9,
        surface_temp=25.0,
        geothermal_gradient=0.03,
        g_temp_derating=0.023,
        t_off_bottom=5000.0,
        fixed_components=(
            SegmentSpec(od=0.171, id=0.071, length=120.0, label="drill_collar"),
            SegmentSpec(od=0.127, id=0.076, length=200.0, label="hwdp"),
        ),
        pipe_geometry=SegmentSpec(od=0.127, id=0.108, length=0.0, label="drill_pipe"),
    )


@dataclass(frozen=True)
class Config:
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    drill_string: DrillStringConfig = field(default_factory=DrillStringConfig)
    bha: BHAConfig = field(default_factory=_default_bha)
    sideband: SidebandConfig = field(default_factory=SidebandConfig)
    assessment: AssessmentConfig = field(default_factory=AssessmentConfig)
    mitigation: MitigationConfig = field(default_factory=MitigationConfig)
    dashboard: bool = False


# ---------------------------------------------------------------------------
# TOML loading
# ---------------------------------------------------------------------------


def _parse_segments(raw: list[dict]) -> tuple[SegmentSpec, ...]:
    return tuple(SegmentSpec(**s) for s in raw)


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    kw = {}

    if "pipeline" in raw:
        kw["pipeline"] = PipelineConfig(**raw["pipeline"])
    if "filter" in raw:
        kw["filter"] = FilterConfig(**raw["filter"])
    if "drill_string" in raw:
        kw["drill_string"] = DrillStringConfig(**raw["drill_string"])
    if "sideband" in raw:
        kw["sideband"] = SidebandConfig(**raw["sideband"])
    if "assessment" in raw:
        kw["assessment"] = AssessmentConfig(**raw["assessment"])
    if "mitigation" in raw:
        kw["mitigation"] = MitigationConfig(**raw["mitigation"])
    if "bha" in raw:
        bha_raw = raw["bha"]
        if "fixed_components" in bha_raw:
            bha_raw["fixed_components"] = _parse_segments(
                bha_raw.pop("fixed_components")
            )
        if "pipe_geometry" in bha_raw:
            bha_raw["pipe_geometry"] = SegmentSpec(**bha_raw.pop("pipe_geometry"))
        kw["bha"] = BHAConfig(**bha_raw)

    return Config(**kw)
