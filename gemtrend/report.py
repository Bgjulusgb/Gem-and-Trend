"""Rendering: separate TREND and GEM tables plus market context and the honest
data-availability legend. Formats: table (rich, with a plaintext fallback),
json, csv.
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict
from typing import Any

from .config import AppConfig
from .models import Candidate
from .pipeline import Report

DISCLAIMER = (
    "SIMULATION — Kein echter Handel, keine Anlageberatung. "
    "Nur zu Bildungszwecken. Daten können fehlerhaft/verzögert sein."
)


def _filter(cands: list[Candidate], cfg: AppConfig) -> list[Candidate]:
    return [c for c in cands if c.score >= cfg.output.min_score][:cfg.output.top_k]


def emit(report: Report, cfg: AppConfig) -> None:
    fmt = cfg.output.fmt
    if fmt == "json":
        _emit_json(report, cfg)
    elif fmt == "csv":
        _emit_csv(report, cfg)
    else:
        _emit_table(report, cfg)


# --- serialisation helpers ----------------------------------------------------
def _candidate_dict(c: Candidate) -> dict[str, Any]:
    d = asdict(c)
    d["confidence"] = round(c.confidence, 3)
    d["components"] = [
        {"name": comp.name, "score": round(comp.score, 1), "weight": comp.weight,
         "source": comp.source.value, "detail": comp.detail}
        for comp in c.components
    ]
    return d


def _emit_json(report: Report, cfg: AppConfig) -> None:
    payload = {
        "disclaimer": DISCLAIMER,
        "market": asdict(report.market),
        "universe_size": report.universe_size,
        "scanned": report.scanned,
        "trending": [_candidate_dict(c) for c in _filter(report.trending, cfg)],
        "gems": [_candidate_dict(c) for c in _filter(report.gems, cfg)],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    _write(text, cfg)


def _emit_csv(report: Report, cfg: AppConfig) -> None:
    rows = []
    for c in _filter(report.trending, cfg) + _filter(report.gems, cfg):
        rows.append({
            "path": c.path, "symbol": c.symbol, "name": c.name,
            "score": c.score, "decision": c.decision,
            "position_pct": c.position_pct, "confidence": round(c.confidence, 3),
            "stop_hint": c.stop_hint, **c.metrics, "notes": c.notes,
        })
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    out = sys.stdout if not cfg.output.out_file else open(cfg.output.out_file, "w", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if cfg.output.out_file:
            out.close()
            print(f"CSV geschrieben: {cfg.output.out_file}")


def _write(text: str, cfg: AppConfig) -> None:
    if cfg.output.out_file:
        with open(cfg.output.out_file, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Datei geschrieben: {cfg.output.out_file}")
    else:
        print(text)


# --- table rendering ----------------------------------------------------------
def _market_line(report: Report) -> str:
    m = report.market
    return (f"Marktregime: {m.regime}  |  {m.note}  |  "
            f"Universe(gefiltert): {report.universe_size}  "
            f"Tiefenanalyse: {report.scanned}")


def _emit_table(report: Report, cfg: AppConfig) -> None:
    trending = _filter(report.trending, cfg)
    gems = _filter(report.gems, cfg)
    try:
        from rich.console import Console
        from rich.panel import Panel
    except ImportError:
        _emit_plain(report, trending, gems)
        return

    console = Console()
    # Headless/piped output reports 80 cols; give rich room so columns don't
    # collapse. On a real but narrow terminal, fall back to the plaintext table.
    if not console.is_terminal:
        console = Console(width=150)
    if console.width < 100:
        _emit_plain(report, trending, gems)
        return
    console.print(Panel(f"[bold]GEM & TREND SCANNER[/bold]\n[yellow]{DISCLAIMER}[/yellow]",
                        border_style="yellow"))
    console.print(f"[cyan]{_market_line(report)}[/cyan]\n")

    console.print(_rich_table("📈 TREND / MOMENTUM (Pfad A)", trending, gem=False))
    console.print()
    console.print(_rich_table("💎 GEMS / PRE-DISCOVERY (Pfad B)", gems, gem=True))
    console.print(
        "\n[dim]Daten-Ampel: 🟢 real · 🟡 verzögert/genähert · "
        "🔴 nicht frei verfügbar (nicht erfunden). "
        "Score >40% auf 🔴 → max. Watchlist.[/dim]")
    console.print(f"[yellow]{DISCLAIMER}[/yellow]")


def _rich_table(title, cands, gem: bool):
    from rich.table import Table
    t = Table(title=title, title_justify="left", header_style="bold", expand=False)
    t.add_column("#", justify="right")
    t.add_column("Sym", style="bold")
    t.add_column("Name", max_width=20, no_wrap=True)
    t.add_column("Score", justify="right")
    t.add_column("Entscheidung")
    t.add_column("Pos%", justify="right")
    t.add_column("Daten", justify="center")
    extra = ["MCap", "P/E", "Analysten", "Inst%"] if gem else ["Preis", "RelVol", "Mom%", "RSI"]
    for col in extra:
        t.add_column(col, justify="right")

    if not cands:
        t.add_row("–", "–", "keine Kandidaten über Schwelle", "", "", "", "",
                  *["" for _ in extra])
        return t

    for i, c in enumerate(cands, 1):
        conf = f"{c.confidence*100:.0f}%🟢"
        pos = f"{c.position_pct*100:.0f}%" if c.position_pct > 0 else "–"
        t.add_row(str(i), c.symbol, c.name[:20], f"{c.score:.0f}",
                  _decor(c.decision), pos, conf,
                  *[c.metrics.get(col, "–") for col in extra])
    return t


def _decor(decision: str) -> str:
    if decision.startswith("Volle") or decision.startswith("Normale"):
        return f"[green]{decision}[/green]"
    if decision.startswith("Kleine"):
        return f"[cyan]{decision}[/cyan]"
    if decision.startswith("Watchlist"):
        return f"[yellow]{decision}[/yellow]"
    return f"[dim]{decision}[/dim]"


# --- plaintext fallback (no rich) --------------------------------------------
def _emit_plain(report: Report, trending, gems) -> None:
    print("=" * 78)
    print("GEM & TREND SCANNER")
    print(DISCLAIMER)
    print("=" * 78)
    print(_market_line(report))
    _plain_table("TREND / MOMENTUM (Pfad A)", trending, gem=False)
    _plain_table("GEMS / PRE-DISCOVERY (Pfad B)", gems, gem=True)
    print("\nDaten-Ampel: 🟢 real · 🟡 verzögert · 🔴 n/a (nicht erfunden).")
    print(DISCLAIMER)


def _plain_table(title, cands, gem: bool) -> None:
    print(f"\n--- {title} ---")
    if not cands:
        print("  (keine Kandidaten über Schwelle)")
        return
    extra = ["MCap", "P/E", "Analysten"] if gem else ["Preis", "RelVol", "Mom%"]
    header = f"{'#':>2} {'SYM':<7} {'SCORE':>5} {'ENTSCHEIDUNG':<26} {'POS':>4} {'DATEN':>6}"
    for col in extra:
        header += f" {col:>9}"
    print(header)
    for i, c in enumerate(cands, 1):
        pos = f"{c.position_pct*100:.0f}%" if c.position_pct > 0 else "-"
        line = (f"{i:>2} {c.symbol:<7} {c.score:>5.0f} {c.decision[:26]:<26} "
                f"{pos:>4} {c.confidence*100:>5.0f}%")
        for col in extra:
            line += f" {str(c.metrics.get(col, '-')):>9}"
        print(line)
        if c.notes:
            print(f"     {c.notes}")
