"""Optional TTL disk cache.

Purpose in this project: repeated runs within the TTL avoid re-hitting Yahoo
(fewer requests), and — importantly for the "manage and delete data" goal —
expired entries are actively removed via :meth:`cleanup`. Each entry is a small
JSON file keyed by a hash of (endpoint, params).

Thread-safe for concurrent Stage-3 reads/writes via a single lock; the work
under the lock is tiny (file I/O on small blobs).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any


class TTLCache:
    def __init__(self, directory: str, ttl_seconds: int, enabled: bool = True):
        self.directory = directory
        self.ttl = ttl_seconds
        self.enabled = enabled
        self._lock = threading.Lock()
        if self.enabled:
            os.makedirs(self.directory, exist_ok=True)

    def _path(self, key: str) -> str:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, f"{digest}.json")

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(key)
        with self._lock:
            try:
                if time.time() - os.path.getmtime(path) > self.ttl:
                    os.remove(path)            # expired -> delete eagerly
                    return None
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (FileNotFoundError, ValueError, OSError):
                return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        with self._lock:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(value, fh)
            except (OSError, TypeError):
                pass                            # cache failures must never break a run

    def cleanup(self) -> int:
        """Delete every expired entry. Returns the number removed."""
        if not self.enabled or not os.path.isdir(self.directory):
            return 0
        removed = 0
        now = time.time()
        with self._lock:
            for name in os.listdir(self.directory):
                path = os.path.join(self.directory, name)
                try:
                    if now - os.path.getmtime(path) > self.ttl:
                        os.remove(path)
                        removed += 1
                except OSError:
                    continue
        return removed
