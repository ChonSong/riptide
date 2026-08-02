"""Tests for riptide.test_utils (pipeline verification module)."""

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
