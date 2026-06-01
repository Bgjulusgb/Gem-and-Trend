"""CLI entry point:  python -m gemtrend [options]

Scans Yahoo Finance for momentum "trends" and overlooked "gems", scoring each
on real data with an honest data-availability traffic light.

SIMULATION / EDUCATIONAL ONLY — not investment advice.
"""
from __future__ import annotations

import argparse
import sys

from .config import AppConfig
from .cache import TTLCache
from .pipeline import run_scan
from .report import emit
from .yahoo import YahooClient, YahooError


def build_config(args: argparse.Namespace) -> AppConfig:
    cfg = AppConfig.load(args.config) if args.config else AppConfig()

    if args.screeners:
        cfg.universe.screeners = [s.strip() for s in args.screeners.split(",") if s.strip()]
    if args.no_trending:
        cfg.universe.use_trending = False
    if args.max_universe is not None:
        cfg.universe.max_universe = args.max_universe
    if args.min_mcap is not None:
        cfg.universe.min_market_cap = args.min_mcap
    if args.max_mcap is not None:
        cfg.universe.max_market_cap = args.max_mcap
    if args.min_price is not None:
        cfg.universe.min_price = args.min_price
    if args.min_volume is not None:
        cfg.universe.min_avg_volume = args.min_volume

    if args.deep_limit is not None:
        cfg.fetch.deep_limit = args.deep_limit
    if args.concurrency is not None:
        cfg.fetch.concurrency = args.concurrency
    if args.rate is not None:
        cfg.fetch.requests_per_second = args.rate

    if args.top_k is not None:
        cfg.output.top_k = args.top_k
    if args.min_score is not None:
        cfg.output.min_score = args.min_score
    if args.output:
        cfg.output.fmt = args.output
    if args.out:
        cfg.output.out_file = args.out

    if args.cache:
        cfg.cache.enabled = True
    if args.cache_ttl is not None:
        cfg.cache.ttl_seconds = args.cache_ttl

    cfg.verbose = args.verbose
    return cfg


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gemtrend",
        description="Gem & Trend Scanner über Yahoo Finance "
                    "(SIMULATION — keine Anlageberatung).")
    p.add_argument("--config", help="JSON-Konfigurationsdatei (überschreibt Defaults)")
    p.add_argument("--screeners", help="Komma-Liste der Yahoo-Predefined-Screener")
    p.add_argument("--no-trending", action="store_true", help="Trending-Quelle aus")
    p.add_argument("--max-universe", type=int, help="Obergrenze Roh-Symbole (Stage 1)")
    p.add_argument("--deep-limit", type=int, help="Anzahl Symbole für Tiefenanalyse (Stage 3)")
    p.add_argument("--concurrency", type=int, help="Parallele Worker (Stage 3)")
    p.add_argument("--rate", type=float, help="Globales Limit: Requests/Sekunde")
    p.add_argument("--min-mcap", type=float, help="Min. Marktkapitalisierung")
    p.add_argument("--max-mcap", type=float, help="Max. Marktkapitalisierung")
    p.add_argument("--min-price", type=float, help="Min. Kurs")
    p.add_argument("--min-volume", type=float, help="Min. Ø-Volumen")
    p.add_argument("--top-k", type=int, help="Zeilen je Tabelle")
    p.add_argument("--min-score", type=float, help="Mindest-Score für Anzeige")
    p.add_argument("--output", choices=["table", "json", "csv"], help="Ausgabeformat")
    p.add_argument("--out", help="Ausgabedatei (sonst stdout)")
    p.add_argument("--cache", action="store_true", help="TTL-Disk-Cache aktivieren")
    p.add_argument("--cache-ttl", type=int, help="Cache-TTL in Sekunden")
    p.add_argument("-v", "--verbose", action="store_true", help="Fortschritt zeigen")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = build_config(args)

    cache = TTLCache(cfg.cache.directory, cfg.cache.ttl_seconds, cfg.cache.enabled)
    cache.cleanup()                                # delete stale entries up front

    client = YahooClient(
        timeout=cfg.fetch.timeout,
        max_retries=cfg.fetch.max_retries,
        requests_per_second=cfg.fetch.requests_per_second,
        cache=cache if cfg.cache.enabled else None,
        verbose=cfg.verbose,
    )
    try:
        report = run_scan(client, cfg)
    except YahooError as exc:
        print(f"Fehler bei der Datenabfrage: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()
        cache.cleanup()                            # and clean up again afterwards

    emit(report, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
