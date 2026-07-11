"""Observability skeleton: structured logging, correlation IDs, and metrics.

ARCHITECTURE.md §3.7 (Observability) and §4.2 (Layer 3 lists "Metrics/Log
Exporters" under Infrastructure). Nothing here depends on domain or
application — this package stands alone (TASKS.md T-P0-08 depends only on
T-P0-01) and is wired into the rest of the system by later tasks.
"""
