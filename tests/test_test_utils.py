"""Tests for riptide.test_utils (pipeline verification module)."""

import time

import pytest

from riptide.test_utils import format_elapsed, retry_until


class TestFormatElapsed:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0.5, "500ms"),
            (5.5, "5.5s"),
            (90.0, "1.5m"),
        ],
    )
    def test_format_elapsed(self, seconds, expected):
        assert format_elapsed(seconds) == expected


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

    @pytest.mark.parametrize(
        "timeout,interval",
        [
            (0, 1),
            (1, 0),
        ],
    )
    def test_rejects_bad_args(self, timeout, interval):
        with pytest.raises(ValueError):
            retry_until(lambda: True, timeout=timeout, interval=interval)

    @pytest.mark.parametrize(
        "timeout,interval,max_elapsed",
        [
            (0.3, 5.0, 1.0),    # capped sleep, no overshoot
            (0.01, 100.0, 0.5), # negative remaining guard
        ],
    )
    def test_does_not_exceed_timeout(self, timeout, interval, max_elapsed):
        start = time.monotonic()
        result = retry_until(lambda: None, timeout=timeout, interval=interval)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < max_elapsed, f"exceeded timeout: {elapsed:.2f}s"
