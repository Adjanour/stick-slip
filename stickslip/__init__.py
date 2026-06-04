"""stickslip package."""

from .buffer import RollingBuffer, make_buffer
from .assessment import assess
from .shell import (
    csv_chunk_stream,
    csv_source,
    sensor_stream,
    simulate_signal,
)
from .history import ModulationHistory
from .sidebands import compute_fm, detect_sidebands
from .transforms import bandpass, detrend, fft_analyze, lowpass, windowed
from .config import (
    AssessmentConfig,
    Config,
    DrillStringConfig,
    FilterConfig,
    MitigationConfig,
    PipelineConfig,
    SidebandConfig,
    load_config,
)
from .dashboard import Dashboard
from .energy import DEFAULT_BHA, EnergyHistory, OffBottomTracker, compute_energy
from .mitigation import MitigationController

# Re-export CLI entry point for convenience
from .cli import run, main
from .types import (
    BHAConfig,
    DrillStringParams,
    ENERGY_BUILDING,
    ENERGY_NORMAL,
    ENERGY_RELEASE,
    EnergyAssessment,
    EnergyEvent,
    FilterSpec,
    INTENSIFYING,
    MITIGATE,
    MINIMAL,
    MitigationSignal,
    STABLE,
    SegmentSpec,
    Signal,
    SidebandResult,
    SpectralResult,
    StickSlipAssessment,
    StickSlipEvent,
    TorsionalEnergyResult,
)
