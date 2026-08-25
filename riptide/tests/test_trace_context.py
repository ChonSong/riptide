#!/usr/bin/env python3
"""Tests for trace context propagation (contextvars + structlog)."""

import pytest
from riptide.webhook import bind_trace_context, get_delivery_id, _delivery_id_var


class TestTraceContext:
    def test_bind_trace_context_sets_delivery_id(self):
        bind_trace_context("test-delivery-123")
        assert get_delivery_id() == "test-delivery-123"

    def test_bind_trace_context_with_extra(self):
        bind_trace_context("test-delivery-456", repo="test/repo", event="pull_request")
        assert get_delivery_id() == "test-delivery-456"

    def test_get_delivery_id_default_is_none(self):
        _delivery_id_var.set(None)
        assert get_delivery_id() is None

    def test_bind_trace_context_overrides_previous(self):
        bind_trace_context("first-delivery")
        bind_trace_context("second-delivery")
        assert get_delivery_id() == "second-delivery"
