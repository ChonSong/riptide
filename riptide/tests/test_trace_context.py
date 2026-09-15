#!/usr/bin/env python3
"""Tests for trace context propagation (contextvars + structlog)."""

import pytest

# Functions only — do NOT import ``_delivery_id_var`` here. The ``client``
# fixture in conftest.py does ``importlib.reload(riptide.webhook)`` (needed
# because WEBHOOK_SECRET/DATA_DIR are bound at import time); a reload
# re-executes the module body in the *same* module object, so a module-level
# ``from riptide.webhook import _delivery_id_var`` keeps the pre-reload
# ContextVar while the functions keep reading the module global — i.e. the
# captured object and the getter would disagree. Functions are safe here
# because they resolve the global at call time; tests that need the variable
# itself go through the live module.
from riptide.webhook import bind_trace_context, get_delivery_id


class TestTraceContext:
    def test_bind_trace_context_sets_delivery_id(self):
        bind_trace_context("test-delivery-123")
        assert get_delivery_id() == "test-delivery-123"

    def test_bind_trace_context_with_extra(self):
        bind_trace_context("test-delivery-456", repo="test/repo", event="pull_request")
        assert get_delivery_id() == "test-delivery-456"

    def test_get_delivery_id_default_is_none(self):
        """The ContextVar's documented default is None (webhook.py:197, :213-215).

        Read and write through the live module so both sides see the same
        (possibly post-reload) ContextVar.
        """
        import riptide.webhook as webhook

        webhook._delivery_id_var.set(None)
        assert webhook.get_delivery_id() is None

    def test_bind_trace_context_overrides_previous(self):
        bind_trace_context("first-delivery")
        bind_trace_context("second-delivery")
        assert get_delivery_id() == "second-delivery"
