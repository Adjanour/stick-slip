"""Tests for CLI routing --tui, --web, and default modes."""

from stickslip.cli import _run_rich, _run_tui, _run_web


def test_run_rich_function_exists():
    """_run_rich should be callable (requires config)."""
    assert callable(_run_rich)


def test_run_tui_function_exists():
    """_run_tui should be callable."""
    assert callable(_run_tui)


def test_run_web_function_exists():
    """_run_web should be callable."""
    assert callable(_run_web)


def test_cli_imports():
    """Verify all cli functions import correctly."""
    from stickslip.cli import (
        _energy_track,
        _sideband_track,
        build_pipeline,
        main,
        run,
    )

    assert callable(run)
    assert callable(main)
    assert callable(build_pipeline)
    assert callable(_sideband_track)
    assert callable(_energy_track)
