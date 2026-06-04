from stickslip.config import (
    AssessmentConfig,
    BHAConfig,
    Config,
    DrillStringConfig,
    FilterConfig,
    MitigationConfig,
    PipelineConfig,
    SidebandConfig,
    load_config,
)
from stickslip.types import SegmentSpec


def test_config_defaults():
    cfg = Config()
    assert cfg.pipeline.window_seconds == 5.0
    assert cfg.pipeline.sample_rate == 50.0
    assert cfg.pipeline.chunk_size == 10
    assert cfg.pipeline.baseline_rpm == 100.0
    assert cfg.pipeline.baseline_wob == 50000.0
    assert cfg.filter.low_hz == 0.5
    assert cfg.filter.high_hz == 8.0
    assert cfg.filter.order == 4
    assert cfg.drill_string.shear_modulus == 80e9
    assert cfg.drill_string.length == 1000.0
    assert cfg.sideband.max_order == 3
    assert cfg.sideband.min_ratio == 0.05
    assert cfg.assessment.mitigate_threshold == 0.005
    assert cfg.mitigation.rpm_boost == 1.15
    assert cfg.mitigation.wob_cut == 0.70
    assert not cfg.dashboard


def test_sub_configs():
    assert PipelineConfig().window_seconds == 5.0
    assert FilterConfig().low_hz == 0.5
    assert DrillStringConfig().shear_modulus == 80e9
    assert SidebandConfig().max_order == 3
    assert AssessmentConfig().mitigate_threshold == 0.005
    assert MitigationConfig().rpm_boost == 1.15


def test_config_default_bha():
    cfg = Config()
    assert cfg.bha.g_base == 79e9
    assert len(cfg.bha.fixed_components) == 2
    assert cfg.bha.fixed_components[0].label == "drill_collar"
    assert cfg.bha.fixed_components[1].label == "hwdp"
    assert cfg.bha.pipe_geometry.label == "drill_pipe"


def test_config_override_pipeline():
    cfg = Config(pipeline=PipelineConfig(window_seconds=3.0, baseline_rpm=120.0))
    assert cfg.pipeline.window_seconds == 3.0
    assert cfg.pipeline.baseline_rpm == 120.0
    assert cfg.pipeline.sample_rate == 50.0  # unchanged default


def test_config_override_sideband():
    cfg = Config(sideband=SidebandConfig(max_order=2, min_ratio=0.1))
    assert cfg.sideband.max_order == 2
    assert cfg.sideband.min_ratio == 0.1
    assert cfg.sideband.search_window_hz == 0.15  # unchanged


def test_load_config_toml(tmp_path):
    toml = tmp_path / "test.toml"
    toml.write_text("""
[pipeline]
window_seconds = 3.0
duration_seconds = 5.0
baseline_rpm = 120.0

[mitigation]
rpm_boost = 1.25
wob_cut = 0.60

[sideband]
max_order = 2
min_ratio = 0.1
""")
    cfg = load_config(toml)
    assert cfg.pipeline.window_seconds == 3.0
    assert cfg.pipeline.duration_seconds == 5.0
    assert cfg.pipeline.baseline_rpm == 120.0
    assert cfg.pipeline.sample_rate == 50.0  # default
    assert cfg.mitigation.rpm_boost == 1.25
    assert cfg.mitigation.wob_cut == 0.60
    assert cfg.mitigation.energy_wob_cut == 0.50  # default
    assert cfg.sideband.max_order == 2
    assert cfg.sideband.min_ratio == 0.1
    assert cfg.sideband.search_window_hz == 0.15  # default
    assert cfg.bha.g_base == 79e9  # default
    assert len(cfg.bha.fixed_components) == 2  # default


def test_load_config_bha_full(tmp_path):
    toml = tmp_path / "bha.toml"
    toml.write_text("""
[bha]
g_base = 80e9
t_off_bottom = 3000.0

[[bha.fixed_components]]
od = 0.2
id = 0.1
length = 50.0
label = "custom_collar"

[bha.pipe_geometry]
od = 0.13
id = 0.11
length = 0.0
label = "custom_pipe"
""")
    cfg = load_config(toml)
    assert cfg.bha.g_base == 80e9
    assert cfg.bha.t_off_bottom == 3000.0
    assert len(cfg.bha.fixed_components) == 1
    assert cfg.bha.fixed_components[0].label == "custom_collar"
    assert cfg.bha.pipe_geometry.od == 0.13
