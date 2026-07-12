"""Venue adapters — one subpackage per exchange.

ARCHITECTURE.md §8.6: "Adapters — Binance, Bybit, Coinbase, IBKR
(future), Simulated (backtest), Paper (live data, fake fills) — all
implement this same [`Venue`] port." The full `VenuePort` adapters
(T-P6-01 for Binance) live here too, once implemented; this package only
holds venue-facing infrastructure, never anything domain/application
depend on directly.
"""
