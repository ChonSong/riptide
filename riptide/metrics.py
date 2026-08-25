#!/usr/bin/env python3
"""metrics.py — Prometheus instrumentation for Riptide.

Exposes counters, histograms, and gauges for webhook spawns,
fix durations, DB lock waits, and API call latencies. Served via
the /metrics endpoint on the FastAPI app.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ── Spawn metrics ────────────────────────────────────────────────────────────
SPAWNS_TOTAL = Counter(
    "riptide_spawns_total",
    "Total spawned Hermes cron jobs",
    ["worker", "status"],  # worker=companion|fixer|deepthink, status=success|failure
)

SPAWN_FAILURES_TOTAL = Counter(
    "riptide_spawn_failures_total",
    "Total spawn failures by reason",
    ["worker", "reason"],  # reason=db_locked|timeout|unauthorized|error
)

# ── Fix duration ─────────────────────────────────────────────────────────────
FIX_DURATION = Histogram(
    "riptide_fix_duration_seconds",
    "Time from fix trigger to completion",
    ["status"],  # success|failed|timeout
    buckets=(30, 60, 120, 300, 600, 1800, 3600),
)

REVIEW_DURATION = Histogram(
    "riptide_review_duration_seconds",
    "Time from review trigger to comment posted",
    ["status"],
    buckets=(10, 30, 60, 120, 300, 600),
)

# ── DB lock metrics ──────────────────────────────────────────────────────────
DB_LOCK_WAITS_TOTAL = Counter(
    "riptide_db_lock_waits_total",
    "Total DB busy_timeout encounters",
    ["operation"],  # reserve_job|mark_complete|reserve_delivery|etc
)

DB_RETRY_ATTEMPTS_TOTAL = Counter(
    "riptide_db_retry_attempts_total",
    "Total tenacity retry attempts on DB ops",
    ["operation"],
)

# ── API call latency ─────────────────────────────────────────────────────────
API_CALL_DURATION = Histogram(
    "riptide_api_call_duration_seconds",
    "GitHub API call latency",
    ["endpoint", "status"],  # endpoint=get_pr_details|post_pr_comment|...
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ── Active workers gauge ────────────────────────────────────────────────────
ACTIVE_WORKERS = Gauge(
    "riptide_active_workers",
    "Current number of active daemon worker threads",
    ["worker"],  # companion|fixer|deepthink|labeler
)

# ── Deploy metrics ───────────────────────────────────────────────────────────
DEPLOY_TOTAL = Counter(
    "riptide_deploys_total",
    "Total deploy triggers",
    ["status"],  # success|skipped|error
)

DEPLOY_DURATION = Histogram(
    "riptide_deploy_duration_seconds",
    "Deploy script execution time",
    buckets=(5, 15, 30, 60, 120, 300),
)


def get_metrics_payload() -> bytes:
    """Return Prometheus exposition format payload for /metrics endpoint."""
    return generate_latest()


def get_metrics_content_type() -> str:
    """Return the Prometheus content type for the /metrics response."""
    return CONTENT_TYPE_LATEST
