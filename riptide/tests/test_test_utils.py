"""Tests for riptide.test_utils (pipeline verification module)."""

import time

import pytest

from riptide.test_utils import format_elapsed, retry_until


class TestFormatElapsed:
    def test_milliseconds(self):
        assert format_elapsed(0.5) == "500ms"

    def test_seconds(self):
        assert format_elapsed(5.5) == "5.5s"

    def test_minutes(self):
        assert format_elapsed(90.0) == "1.5m"


class TestRetryUntil:
    def test_returns_truthy_result(self):
        calls = []

        def predicate():
            calls.append(1)
            return "done" if len(calls) >= 3 else None

        assert retry_until(predicate, timeout=10, interval=0.05) == "done"
        assert len(calls) == 3

    def test_returns_none_on_timeout(self):
        assert retry_until(lambda: None, timeout=0.2, interval=0.05) is None

    def test_rejects_bad_args(self):
        with pytest.raises(ValueError):
            retry_until(lambda: True, timeout=0, interval=1)
        with pytest.raises(ValueError):
            retry_until(lambda: True, timeout=1, interval=0)

    def test_does_not_overshoot_deadline(self):
        """Sleep after last check is capped to remaining time, not full interval."""
        calls = []
        start = time.monotonic()

        def predicate():
            calls.append(time.monotonic())
            return None

        result = retry_until(predicate, timeout=0.3, interval=5.0)
        elapsed = time.monotonic() - start
        assert result is None
        # Must return within ~0.3s + small margin, not 5s
        assert elapsed < 1.0, f"overshot deadline: {elapsed:.2f}s"

    def test_negative_remaining_returns_none(self):
        """If clock jitter makes remaining negative, return None (no negative sleep)."""
        calls = []

        def predicate():
            calls.append(1)
            return None

        # timeout=0.01 with interval=100 — should return almost immediately
        start = time.monotonic()
        result = retry_until(predicate, timeout=0.01, interval=100.0)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 0.5, f"took too long: {elapsed:.2f}s"
