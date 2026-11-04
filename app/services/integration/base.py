"""Building blocks shared by all pull adapters."""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


class TransientSourceError(Exception):
    """Temporary failure (timeout, 5xx, rate limit) – safe to retry."""


@dataclass
class PageResult:
    records: list[dict[str, Any]]
    next_cursor: str | None


def with_retry(fn: Callable[[], T], attempts: int = 3, base_delay: float = 0.2) -> T:
    """Retry transient failures with exponential backoff and jitter."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except TransientSourceError:
            if attempt == attempts:
                raise
            time.sleep(base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay / 2))
    raise AssertionError("unreachable")
