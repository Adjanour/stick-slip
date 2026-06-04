"""Smoke tests for the Textual TUI module."""

from stickslip.config import Config
from stickslip.tui import StickSlipTUI, PanelWidget


def test_panel_widget_can_be_created():
    w = PanelWidget()
    assert w is not None


def test_panel_widget_accepts_content():
    w = PanelWidget()
    w.update_content("hello")  # should not raise


def test_tui_app_can_be_instantiated():
    cfg = Config()
    app = StickSlipTUI(cfg)
    assert app.config is cfg
    assert app._queue is not None
    assert app._stop_event is not None
    assert app._thread is None


def test_tui_bindings_defined():
    cfg = Config()
    app = StickSlipTUI(cfg)
    keys = {b.action for b in app.BINDINGS}
    assert "quit" in keys
    assert "toggle" in keys
    assert "restart" in keys
    assert "params" in keys


def test_tui_pipeline_stop_event():
    cfg = Config()
    app = StickSlipTUI(cfg)
    assert not app._stop_event.is_set()
    app._stop_event.set()
    assert app._stop_event.is_set()


def test_tui_status_transitions():
    cfg = Config()
    app = StickSlipTUI(cfg)
    assert app.status_text == "STOPPED"
    app.status_text = "RUNNING"
    assert app.status_text == "RUNNING"
