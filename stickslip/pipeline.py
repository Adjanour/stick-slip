"""
Left-to-right function composition for building the processing pipeline.
"""

from __future__ import annotations

import functools
from typing import Callable


def compose(*fns: Callable) -> Callable:
    """Left-to-right composition: compose(f, g, h)(x) → h(g(f(x)))."""
    if not fns:
        raise ValueError("compose() requires at least one function")
    return functools.reduce(lambda f, g: lambda x: g(f(x)), fns)
