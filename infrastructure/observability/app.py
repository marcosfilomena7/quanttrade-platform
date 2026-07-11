"""Minimal FastAPI app exposing Prometheus metrics at `/metrics`.

TASKS.md T-P0-08: "Prometheus metrics are scrapeable at `/metrics` from a
minimal FastAPI app." This app has exactly one route — the real Read API
(positions, orders, ...) is a separate, later concern with no ticket in
Phase 0, and building it out here would be scope beyond this task.
"""

from __future__ import annotations

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from infrastructure.observability.metrics import REGISTRY


def create_metrics_app() -> FastAPI:
    """Build a FastAPI app whose only route is `GET /metrics`."""
    app = FastAPI(title="QuantTrade Observability")

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    return app
