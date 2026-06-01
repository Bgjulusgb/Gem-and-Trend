# 💎 Gem & Trend Scanner

> **⚠ SIMULATION — Kein echter Handel, keine Anlageberatung. Nur zu Bildungszwecken.**
> Dieses Programm ist ein Lern- und Analyse-Werkzeug. Es gibt **keine** Kauf-/
> Verkaufs­empfehlungen. Alle Ausgaben sind informativ, können fehlerhaft oder
> verzögert sein und ersetzen keine eigene Recherche.

Ein effizienter Kommandozeilen-Scanner, der **Yahoo Finance** durchsucht und
zwei Arten von Kandidaten findet — bewusst **nicht nur die offensichtlichen
Top-Werte**, sondern auch kleine, übersehene Aktien:

- **📈 TREND (Momentum):** Was bewegt sich *jetzt*? Volumen-Anomalie, Kurs­dynamik,
  Ausbruch über gleitende Durchschnitte, Nähe zum 52-Wochen-Hoch.
- **💎 GEM (Pre-Discovery):** Noch *kein* Hype — aber gesunde Fundamental­daten,
  **geringe Analystenabdeckung**, niedrige institutionelle Quote, technische
  Bodenbildung/Akkumulation und relativ günstige Bewertung.

Beide werden in **getrennten Tabellen** ausgegeben, weil sie unterschiedliche
Strategien erfordern (Momentum vs. Geduld).

---

## Effizienz-Prinzip (3-Stufen-Trichter)

Das Programm ist darauf ausgelegt, **so viele Symbole wie möglich zu bewerten,
dabei aber so wenig wie möglich anzufragen und im Speicher zu halten**:

| Stufe | Quelle | Kosten | Was passiert |
|------:|--------|--------|--------------|
| **1** breit | Predefined-Screener + Trending | ~8 Requests | Hunderte Symbole + reiche Basisfelder → Hard-Filter, Roh-JSON sofort verworfen |
| **2** gebündelt | `v7/quote` Batch (≤50/Req) | wenige Requests | fehlende Kerndaten auffüllen, Vor-Ranking, Top-N behalten, Rest freigeben |
| **3** eng | `chart` + `quoteSummary` | nur Top-N × 2 | Tiefenanalyse **nur** für Überlebende, parallel & gedrosselt; Roh-OHLCV sofort verworfen |

Ergebnisse leben ausschließlich in **beschränkten Top-K-Heaps** — die „ganze
Welt" liegt nie gleichzeitig im RAM. Ein optionaler **TTL-Cache** spart bei
wiederholten Läufen Requests und **löscht** abgelaufene Einträge automatisch.

Details: siehe [`PLAN.md`](PLAN.md).

---

## Installation

```bash
pip install -r requirements.txt
```

Nur `requests` ist Pflicht. `rich` ist optional (schönere Tabellen); ohne
`rich` fällt das Programm automatisch auf Plaintext-Tabellen zurück.
Kein API-Schlüssel nötig — es werden nur öffentliche Yahoo-Endpunkte genutzt.

## Nutzung

```bash
# Standard-Scan (Tabelle im Terminal)
python -m gemtrend

# Mit Fortschrittsanzeige, mehr Tiefenanalyse, Cache
python -m gemtrend -v --deep-limit 40 --cache

# Nur kleine Werte als Gems, höflicheres Rate-Limit
python -m gemtrend --max-mcap 2e9 --rate 3 --top-k 15

# Maschinenlesbar exportieren
python -m gemtrend --output json --out scan.json
python -m gemtrend --output csv  --out scan.csv
```

### Wichtige Optionen

| Option | Bedeutung |
|--------|-----------|
| `--screeners a,b,c` | Yahoo-Predefined-Screener (Universe-Quellen) |
| `--no-trending` | Trending-Quelle abschalten |
| `--max-universe N` | Obergrenze Roh-Symbole (Stufe 1) |
| `--deep-limit N` | Anzahl Symbole für die teure Tiefenanalyse (Stufe 3) |
| `--concurrency N` | Parallele Worker in Stufe 3 |
| `--rate R` | **Globales** Limit in Requests/Sekunde (Yahoo-schonend) |
| `--min-mcap / --max-mcap` | Marktkapitalisierungs-Filter (Gem-Floor: 50 M) |
| `--min-price / --min-volume` | Liquiditäts-/Penny-Filter |
| `--top-k N` | Zeilen je Tabelle |
| `--min-score S` | Mindest-Score für die Anzeige |
| `--output table\|json\|csv`, `--out FILE` | Ausgabeformat/-datei |
| `--cache`, `--cache-ttl S` | TTL-Disk-Cache (Auto-Cleanup) |
| `--config FILE` | JSON-Datei, die beliebige Defaults überschreibt |
| `-v/--verbose` | Stufen-Fortschritt anzeigen |

---

## Scoring & Ehrlichkeit (Anti-Halluzination)

Jede Teil-Kennzahl wird auf **0–100 normalisiert**, dann gewichtet. Entscheidend
ist die **Daten-Ampel** je Kennzahl:

- 🟢 **real** — direkt aus Yahoo abrufbar
- 🟡 **verzögert/genähert** — z. B. evtl. veraltete Insider-/Eigentümerdaten
- 🔴 **nicht frei verfügbar** — z. B. Social-Buzz, Options-Flow

🔴-Kennzahlen werden **nie erfunden**: Sie fallen aus dem Score und die Gewichte
werden renormalisiert. Beruht ein Score zu **>40 %** des Gewichts auf 🔴-Daten,
wird die Empfehlung automatisch auf **„Watchlist"** gedeckelt.

**Entscheidungsleiter (0–100):** <50 nicht handeln · 50–64 Watchlist ·
65–79 kleine Position · 80–94 normale Position · ≥95 volle Position. Die
vorgeschlagene Positionsgröße wird zusätzlich durch das **Marktregime**
(S&P vs. 200-Tage-MA, VIX) gedämpft. Ein **ATR-basierter Stop-Hinweis** wird
mitgeliefert. Alles rein informativ.

---

## Projektstruktur

```
gemtrend/
  __main__.py   CLI
  config.py     Konfiguration (Dataclasses + JSON-Override)
  yahoo.py      HTTP-Client (Session, Crumb, Rate-Limiter, Backoff, Cache)
  indicators.py Reine TA-Funktionen (None-sicher, kein pandas/numpy)
  models.py     Dataclasses (Ampel, Component, Candidate, MarketContext)
  scoring.py    Normalisierung, Ampel-Renorm, Entscheidungsleiter
  analyze.py    Trend- & Gem-Bewertung, Vor-Ranking
  pipeline.py   3-Stufen-Orchestrierung (Streaming, Bounded-Top-K)
  report.py     Tabellen (rich/plain), JSON/CSV
  cache.py      TTL-Disk-Cache mit Auto-Cleanup
tests/          Unit-Tests (Indikatoren + Scoring, ohne Netzwerk)
```

## Tests

```bash
python -m pytest -q
```

Die deterministische Kernlogik (Indikatoren, Normalisierung, Ampel-Renorm,
Entscheidungsleiter) ist unit-getestet und benötigt **kein** Netzwerk.

---

## Grenzen / Ehrlichkeit

- Social-Sentiment, Options-Flow und Echtzeit-Insider sind **nicht frei
  verfügbar** → ehrlich als 🔴 markiert, nicht geschätzt.
- Yahoo-Daten können verzögert/fehlerhaft sein; Rate-Limits sind möglich
  (das Programm drosselt, wiederholt mit Backoff und holt den Crumb bei Bedarf neu).
- Die Heuristiken/Gewichte sind bewusst transparent und in `config.py`
  einstellbar — sie sind **keine** geprüfte Anlagestrategie.

**Nochmal: SIMULATION — keine Anlageberatung. Nur zu Bildungszwecken.**
