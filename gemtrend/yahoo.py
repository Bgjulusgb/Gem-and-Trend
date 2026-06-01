"""Yahoo Finance HTTP client.

Design goals (efficiency / politeness):
  * one shared :class:`requests.Session` (connection reuse);
  * a real browser ``User-Agent`` (without it Yahoo returns HTTP 429);
  * lazy, thread-safe crumb+cookie bootstrap (required by v7 quote &
    quoteSummary);
  * a global token-bucket-ish rate limiter shared across worker threads;
  * exponential backoff on 429/5xx and an automatic crumb refresh on 401;
  * an optional TTL cache so repeated runs cost almost no requests.

Every method returns compact Python structures; callers extract metrics and
drop the payloads immediately, so raw JSON never accumulates.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Iterable

import requests

from .cache import TTLCache

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_HOST = "https://query1.finance.yahoo.com"


class YahooError(RuntimeError):
    pass


class YahooClient:
    def __init__(self, timeout: float = 12.0, max_retries: int = 4,
                 requests_per_second: float = 4.0,
                 cache: TTLCache | None = None, verbose: bool = False):
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache = cache
        self.verbose = verbose

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": _UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })

        self._crumb: str | None = None
        self._crumb_lock = threading.Lock()

        # Global rate limiter shared by all threads.
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._next_allowed = 0.0
        self._rate_lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------------
    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "YahooClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ------------------------------------------------------------
    def _throttle(self) -> None:
        if self._interval <= 0:
            return
        with self._rate_lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            start = max(now, self._next_allowed)
            self._next_allowed = start + self._interval
        if wait > 0:
            time.sleep(wait)

    def _ensure_crumb(self, force: bool = False) -> str:
        with self._crumb_lock:
            if self._crumb and not force:
                return self._crumb
            # 1) hit a Yahoo host to receive the session cookie (404 is fine).
            try:
                self.session.get("https://fc.yahoo.com", timeout=self.timeout)
            except requests.RequestException:
                pass
            # 2) exchange the cookie for a crumb token.
            try:
                resp = self.session.get(f"{_HOST}/v1/test/getcrumb",
                                        timeout=self.timeout)
                crumb = resp.text.strip()
            except requests.RequestException as exc:
                raise YahooError(f"crumb bootstrap failed: {exc}") from exc
            if not crumb or len(crumb) > 32 or "<" in crumb:
                raise YahooError("could not obtain a valid crumb")
            self._crumb = crumb
            return crumb

    def _get(self, path: str, params: dict[str, Any] | None = None,
             need_crumb: bool = False, cache_key: str | None = None) -> Any:
        """GET with rate-limiting, retry/backoff, crumb refresh and caching."""
        params = dict(params or {})
        if cache_key and self.cache:
            hit = self.cache.get(cache_key)
            if hit is not None:
                return hit

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if need_crumb:
                params["crumb"] = self._ensure_crumb()
            self._throttle()
            try:
                resp = self.session.get(f"{_HOST}{path}", params=params,
                                        timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                self._sleep_backoff(attempt)
                continue

            if resp.status_code == 200:
                data = resp.json()
                if cache_key and self.cache:
                    self.cache.set(cache_key, data)
                return data
            if resp.status_code == 401 and need_crumb:
                self._ensure_crumb(force=True)          # stale crumb -> refresh
                last_exc = YahooError("401 unauthorized")
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = YahooError(f"HTTP {resp.status_code}")
                self._sleep_backoff(attempt)
                continue
            # other 4xx -> not retryable
            raise YahooError(f"HTTP {resp.status_code} for {path}")

        raise YahooError(f"exhausted retries for {path}: {last_exc}")

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(16.0, 0.8 * (2 ** attempt)) + random.uniform(0, 0.4)
        if self.verbose:
            print(f"  [retry] backoff {delay:.1f}s")
        time.sleep(delay)

    # -- endpoints ------------------------------------------------------------
    def trending(self, count: int = 30) -> list[str]:
        """Trending US tickers, filtered to plain equities."""
        try:
            data = self._get("/v1/finance/trending/US", {"count": count},
                             cache_key=f"trending:{count}")
            quotes = data["finance"]["result"][0]["quotes"]
        except (YahooError, KeyError, IndexError, TypeError):
            return []
        out = []
        for q in quotes:
            sym = q.get("symbol", "")
            if sym and _is_equity_symbol(sym):
                out.append(sym)
        return out

    def screener(self, scr_id: str, count: int = 60) -> list[dict[str, Any]]:
        """Rows from a predefined Yahoo screener (already rich quote dicts)."""
        params = {"scrIds": scr_id, "count": count, "start": 0}
        try:
            data = self._get("/v1/finance/screener/predefined/saved", params,
                             need_crumb=True, cache_key=f"scr:{scr_id}:{count}")
            return data["finance"]["result"][0].get("quotes", [])
        except (YahooError, KeyError, IndexError, TypeError):
            return []

    def batch_quote(self, symbols: Iterable[str], batch_size: int = 50
                    ) -> list[dict[str, Any]]:
        """Core fields for many symbols using few requests (Stage 2)."""
        syms = [s for s in symbols if s]
        out: list[dict[str, Any]] = []
        for i in range(0, len(syms), batch_size):
            chunk = syms[i:i + batch_size]
            params = {"symbols": ",".join(chunk)}
            try:
                data = self._get("/v7/finance/quote", params, need_crumb=True)
                out.extend(data["quoteResponse"]["result"])
            except (YahooError, KeyError, TypeError):
                continue
        return out

    def chart(self, symbol: str, rng: str = "6mo", interval: str = "1d"
              ) -> dict[str, list] | None:
        """OHLCV history for one symbol (Stage 3). Returns aligned lists."""
        params = {"range": rng, "interval": interval}
        try:
            data = self._get(f"/v8/finance/chart/{symbol}", params,
                             cache_key=f"chart:{symbol}:{rng}:{interval}")
            res = data["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            return {
                "timestamp": res.get("timestamp", []),
                "open": q.get("open", []),
                "high": q.get("high", []),
                "low": q.get("low", []),
                "close": q.get("close", []),
                "volume": q.get("volume", []),
            }
        except (YahooError, KeyError, IndexError, TypeError):
            return None

    def quote_summary(self, symbol: str, modules: Iterable[str]
                      ) -> dict[str, Any] | None:
        """Fundamentals/ownership/insider modules for one symbol (Stage 3)."""
        mods = ",".join(modules)
        params = {"modules": mods}
        try:
            data = self._get(f"/v10/finance/quoteSummary/{symbol}", params,
                             need_crumb=True,
                             cache_key=f"qs:{symbol}:{mods}")
            return data["quoteSummary"]["result"][0]
        except (YahooError, KeyError, IndexError, TypeError):
            return None


def _is_equity_symbol(sym: str) -> bool:
    """Filter out futures (=F), crypto (-USD), indices (^...), FX pairs."""
    if any(tok in sym for tok in ("=", "^")):
        return False
    if sym.endswith("-USD") or sym.endswith("-EUR"):
        return False
    return True
