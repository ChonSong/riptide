#!/usr/bin/env python3
"""Tests for riptide/metrics.py — Prometheus instrumentation."""

import pytest
from prometheus_client import CollectorRegistry

from riptide import metrics


@pytest.fixture(autouse=True)
def fresh_registry():
    """Reset Prometheus registry between tests to avoid duplicate metrics."""
    # Create a fresh registry for each test
    import prometheus_client
    old_registry = prometheus_client.REGISTRY
    prometheus_client.REGISTRY = CollectorRegistry()
    yield
    prometheus_client.REGISTRY = old_registry


class TestMetricsPayload:
    def test_get_metrics_payload_returns_bytes(self):
        payload = metrics.get_metrics_payload()
        assert isinstance(payload, bytes)

    def test_get_metrics_content_type(self):
        ct = metrics.get_metrics_content_type()
        assert "text/plain" in ct
        assert "version" in ct

    def test_metrics_endpoint_includes_spawns(self):
        metrics.SPAWNS_TOTAL.labels(worker="companion", status="success").inc()
        payload = metrics.get_metrics_payload().decode()
        assert "riptide_spawns_total" in payload
        assert 'worker="companion"' in payload

    def test_metrics_endpoint_includes_fix_duration(self):
        metrics.FIX_DURATION.labels(status="success").observe(42.5)
        payload = metrics.get_metrics_payload().decode()
        assert "riptide_fix_duration_seconds" in payload

    def test_metrics_endpoint_includes_db_lock_waits(self):
        metrics.DB_LOCK_WAITS_TOTAL.labels(operation="reserve_delivery").inc(3)
        payload = metrics.get_metrics_payload().decode()
        assert "riptide_db_lock_waits_total" in payload

    def test_metrics_endpoint_includes_api_latency(self):
        metrics.API_CALL_DURATION.labels(endpoint="get_pr_details", status="200").observe(0.3)
        payload = metrics.get_metrics_payload().decode()
        assert "riptide_api_call_duration_seconds" in payload

    def test_metrics_endpoint_includes_active_workers(self):
        metrics.ACTIVE_WORKERS.labels(worker="fixer").set(2)
        payload = metrics.get_metrics_payload().decode()
        assert "riptide_active_workers" in payload

    def test_metrics_endpoint_includes_deploys(self):
        metrics.DEPLOY_TOTAL.labels(status="success").inc()
        payload = metrics.get_metrics_payload().decode()
        assert "riptide_deploys_total" in payload


class TestSpawnMetrics:
    def test_spawn_counter_increments(self):
        metrics.SPAWNS_TOTAL.labels(worker="deepthink", status="success").inc()
        metrics.SPAWNS_TOTAL.labels(worker="deepthink", status="success").inc()
        payload = metrics.get_metrics_payload().decode()
        assert "riptide_spawns_total" in payload

    def test_spawn_failures_tracked(self):
        metrics.SPAWN_FAILURES_TOTAL.labels(worker="fixer", reason="timeout").inc()
        payload = metrics.get_metrics_payload().decode()
        assert "riptide_spawn_failures_total" in payload
        assert 'reason="timeout"' in payload
