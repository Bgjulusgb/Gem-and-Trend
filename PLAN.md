# PLAN — Gem & Trend Scanner

> **⚠ SIMULATION / NUR BILDUNG — Keine Anlageberatung.**
> Dieses Programm ist ein Lern- und Analyse-Werkzeug. Es liefert **keine**
> Kauf-/Verkaufsempfehlungen und keine Finanzberatung. Alle Ausgaben sind
> rein informativ und können fehlerhaft sein.

## 1. Ziel

Ein Scanner, der **Yahoo Finance** durchsucht und zwei Arten von Kandidaten
findet — bewusst **nicht nur die offensichtlichen Top-Werte**, sondern auch
kleine, übersehene Aktien:

- **TREND (Pfad A — Momentum):** Was bewegt sich *jetzt*? Volumen-Anomalie,
  Kursdynamik, Ausbruch, hohe Aufmerksamkeit.
- **GEM (Pfad B — Pre-Discovery):** Noch *kein* Hype, aber gesunde Fundamentaldaten,
  geringe Analystenabdeckung, niedrige institutionelle Quote, technische
  Bodenbildung/Akkumulation, relativ günstige Bewertung.

## 2. Leitprinzip: Effizienz & Daten-Lebenszyklus

Der Nutzer fordert: *so viele Daten wie möglich anfragen, analysieren, managen
und wieder löschen.* Das wird über eine **3-Stufen-Trichter-Pipeline** und
striktes Speicher-Management umgesetzt:

```
   STUFE 1  (breit & billig)        STUFE 2 (billig, gebündelt)     STUFE 3 (teuer, eng)
 ┌────────────────────────┐       ┌────────────────────────┐     ┌────────────────────────┐
 │ Screener + Trending    │       │ v7 Batch-Quote          │     │ Chart (OHLCV) +        │
 │ ~8 Requests →          │  ───► │ 50 Symbole / Request →  │ ──► │ quoteSummary           │
 │ hunderte Symbole +     │       │ Kern-Metriken auffüllen │     │ NUR für Überlebende    │
 │ reiche Basisfelder     │       │ (wenige Requests)       │     │ (parallel, gedrosselt) │
 └────────────────────────┘       └────────────────────────┘     └────────────────────────┘
        │  Hard-Filter                     │  Vor-Score Ranking          │  Voll-Score beide Pfade
        ▼  (MarketCap/Preis/Vol)           ▼  Top-N behalten             ▼  Bounded Top-K Heap
   Roh-JSON sofort verworfen          Rest verworfen (del)         Roh-OHLCV sofort verworfen
```

**Speicher-Management-Regeln (im Code erzwungen):**
1. Roh-JSON/-OHLCV wird **sofort nach Metrik-Extraktion** verworfen (`del`).
2. Zwischen den Stufen werden nicht-überlebende Records freigegeben.
3. Endergebnis lebt in **beschränkten Top-K-Heaps** (feste Obergrenze, nie die
   ganze Welt im RAM).
4. **TTL-Disk-Cache** (optional): wiederholte Läufe sparen Requests; abgelaufene
   Einträge werden automatisch **gelöscht** (`cleanup`).
5. Eine **einzige** HTTP-Session, Rate-Limiter + Backoff → minimale, höfliche
   Last auf Yahoo.

So werden mit ~8 + wenige + (Top-N × 2) Requests **hunderte** Symbole bewertet,
ohne je mehr als ein paar hundert kompakte Records gleichzeitig zu halten.

## 3. Datenquellen (verifiziert aus dieser Umgebung)

| Endpunkt | Liefert | Crumb? | Stufe |
|---|---|---|---|
| `v1/finance/screener/predefined/saved` | Universe + reiche Quote-Felder | optional | 1 |
| `v1/finance/trending/US` | Trending-Symbole | nein | 1 |
| `v7/finance/quote` | Batch-Kerndaten (≤50/Req) | **ja** | 2 |
| `v8/finance/chart` | OHLCV-Historie (TA) | nein | 3 |
| `v10/finance/quoteSummary` | Fundamentaldaten, Analysten, Insider, Institut. | **ja** | 3 |

Alle Requests benötigen einen echten `User-Agent` (sonst HTTP 429).
Crumb-Bootstrap: `GET fc.yahoo.com` (setzt Cookie) → `GET v1/test/getcrumb`.

## 4. Analyse-Module (neu kalibriert)

- **Marktkontext:** S&P 500 vs. 200-Tage-MA, VIX-Niveau → Marktregime →
  beeinflusst empfohlene Positionsgröße.
- **Technische Indikatoren (reines Python, kein pandas/numpy):** RSI (Wilder),
  SMA/EMA, ATR (für Stop-Loss), relatives Volumen / Volumen-Z-Score, Momentum,
  Abstand zu 52-Wochen-Hoch/-Tief, realisierte Volatilität (Konsolidierung),
  Regressions-Steigung (Akkumulation).
- **Pfad-Weiche:** Jeder Überlebende wird auf **beiden** Pfaden bewertet und in
  die Tabelle einsortiert, in der er besser passt/qualifiziert.

### Trend-Score (Pfad A) — Komponenten
Volumen-Anomalie · Kurs-Momentum · Ausbruch über MAs/52W-Hoch · RSI-Momentumzone
· Nähe zum Hoch. (Social/Options-Flow = **nicht frei verfügbar** → Daten-Ampel 🔴.)

### Gem-Score (Pfad B) — Komponenten
Fundamental-Gesundheit (Profitabilität/Margen) · relative Bewertung (P/E, P/S
vs. Schwelle) · geringe Analystenabdeckung (≤3) · institutionelle Quote (<40 %,
steigend) · technische Bodenbildung/Akkumulation · Insider-Käufe (sofern via
Yahoo verfügbar). (Früh-Social = 🔴.)

## 5. Scoring (saubere Mathematik + Anti-Halluzination)

1. Jede Komponente wird zuerst auf **0–100 normalisiert** (`erreicht/maximal`).
2. **Daten-Ampel je Metrik:** 🟢 real · 🟡 verzögert/genähert · 🔴 nicht frei
   verfügbar. 🔴-Komponenten werden **nicht erfunden**, sondern aus dem Score
   entfernt und die Gewichte renormalisiert.
3. Gewichtete Summe → 0–100.
4. **Sicherung:** Beruht ein Score zu >40 % (Gewicht) auf 🔴-Daten, wird die
   Empfehlung automatisch auf **„Watchlist"** gedeckelt.
5. **Entscheidungsregel:** <50 nicht handeln · 50–64 Watchlist · 65–79 kleine
   Position · 80–94 normale Position · ≥95 volle Position. Positionsgröße wird
   zusätzlich durch das Marktregime gedämpft.
6. **ATR-basierter Stop-Loss** + gestaffelte Take-Profit-Hinweise (informativ).

## 6. Ausgabe

Getrennte Tabellen für **TREND** und **GEM** (unterschiedliche Strategien:
Momentum vs. Geduld), je mit Score, Entscheidung, Daten-Vertrauen (% 🟢),
vorgeschlagener Positionsgröße, Stop-Hinweis. Plus Marktkontext-Kopf,
Daten-Ampel-Legende und prominenter Simulations-Disclaimer.
Formate: `table` (rich, mit Plaintext-Fallback) · `json` · `csv`.

## 7. Architektur / Dateien

```
gemtrend/
  __main__.py     CLI-Einstieg (python -m gemtrend)
  config.py       Dataclass-Konfiguration + JSON-Override + Defaults
  yahoo.py        HTTP-Client: Session, UA, Crumb, Rate-Limiter, Retry/Backoff,
                  Endpunkt-Methoden (trending/screener/batch_quote/chart/summary)
  indicators.py   Reine TA-Funktionen (None-sicher, kein pandas)
  models.py       Dataclasses: Source-Ampel, Metric, Candidate, MarketContext
  scoring.py      Normalisierung, Gewichtung, Ampel-Renorm, Entscheidungsregel
  analyze.py      analyze_trending / analyze_gem / Pfad-Routing
  pipeline.py     Orchestrierung (3 Stufen, Streaming, Bounded-Top-K, del)
  report.py       Tabellen (rich/plain), JSON/CSV, Disclaimer
  cache.py        TTL-Disk-Cache mit Auto-Cleanup (optional)
tests/
  test_indicators.py  Unit-Tests der TA-Mathematik
  test_scoring.py     Unit-Tests Normalisierung/Ampel/Entscheidung
requirements.txt   requests (hart) · rich (optional)
```

## 8. Robustheit

- Funktioniert ohne API-Key (nur öffentliche Yahoo-Endpunkte).
- Graceful Degradation: fehlt ein Endpunkt/Feld → Daten-Ampel 🔴, kein Absturz.
- Rate-Limit-Schutz: 429/5xx → exponentielles Backoff; 401 → Crumb neu holen.
- Deterministische Kernlogik (Indikatoren/Scoring) ist unit-getestet.
- Keine Anlageberatung — Disclaimer in jeder Ausgabe.
