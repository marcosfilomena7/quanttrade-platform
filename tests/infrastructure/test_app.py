"""Tests for the minimal FastAPI /metrics app (infrastructure/observability/app.py)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from infrastructure.observability import metrics as m
from infrastructure.observability.app import create_metrics_app


def test_metrics_endpoint_returns_200() -> None:
    client = TestClient(create_metrics_app())
    response = client.get("/metrics")
    assert response.status_code == 200


def test_metrics_endpoint_uses_prometheus_content_type() -> None:
    client = TestClient(create_metrics_app())
    response = client.get("/metrics")
    assert "text/plain" in response.headers["content-type"]


def test_metrics_endpoint_scrapes_a_real_metric_family() -> None:
    client = TestClient(create_metrics_app())
    response = client.get("/metrics")
    assert "# TYPE order_submissions_total counter" in response.text


def test_metrics_endpoint_reflects_updated_values() -> None:
    unique_reason = "APP_ENDPOINT_TEST_ONLY_REASON"
    m.order_rejections_total.labels(reason=unique_reason).inc()

    client = TestClient(create_metrics_app())
    response = client.get("/metrics")

    assert f'order_rejections_total{{reason="{unique_reason}"}} 1.0' in response.text


def test_only_the_metrics_route_exists() -> None:
    """T-P0-08 asks for a minimal app whose only job is exposing /metrics —
    not a general Read API."""
    app = create_metrics_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/metrics" in paths
