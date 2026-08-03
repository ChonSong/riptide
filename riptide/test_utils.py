"""Test utility for pipeline verification.

This module exists solely to exercise the Riptide review pipeline
end-to-end with a real PR. It will be removed after verification.
"""

import time
from typing import Optional


def format_elapsed(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string."""
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


def retry_until(predicate, timeout: float = 30.0, interval: float = 1.0, description: str = "condition"):
    """Poll `predicate` until it returns truthy or timeout elapses.

    Args:
        predicate: zero-arg callable returning truthy when ready.
        timeout: max seconds to poll.
        interval: seconds between polls.
        description: human-readable name for error messages.

    Returns:
        The truthy result, or None if timeout elapsed.

    Raises:
        ValueError: if timeout/interval are invalid.
    """
    if timeout <= 0 or interval <= 0:
        raise ValueError("timeout and interval must be positive")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        # Cap the sleep so we don't overshoot the deadline
        remaining = deadline - time.monotonic()
        time.sleep(min(interval, remaining))

    return None
