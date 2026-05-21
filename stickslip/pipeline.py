"""
Pipeline composition.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def compose(*fns: Callable) -> Callable:
    """Compose functions left-to-right."""
    if not fns:
        raise ValueError("compose() requires at least one function")
    return functools.reduce(lambda f, g: lambda x: g(f(x)), fns)


def pipe(value: Any, *fns: Callable) -> Any:
    """Apply a sequence of functions to one value."""
    return functools.reduce(lambda v, f: f(v), fns, value)


def tap(side_effect: Callable[[T], Any]) -> Callable[[T], T]:
    """Insert an observing side effect without changing the value."""

    def _tap(value: T) -> T:
        side_effect(value)
        return value

    return _tap


def fanout(*fns: Callable[[T], Any]) -> Callable[[T], tuple]:
    """Apply multiple functions to the same input and return all results."""

    def _fanout(value: T) -> tuple:
        return tuple(f(value) for f in fns)

    return _fanout
