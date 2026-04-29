"""
cache.py
--------
Simple in-memory LRU cache for grammar and translation results.

200 entries per namespace; evicts the least-recently-used entry when full.
Shared across correction/translation calls within a session so repeated
identical inputs return instantly without hitting the model again.
"""

import os
import threading
from collections import OrderedDict
from typing import Optional


def appdata_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "SmartKeyboard")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        # %APPDATA% is on a broken network share or a read-only volume.
        # Fall back to the system temp directory so logging and settings
        # still work — the app shows a warning after Qt initialises.
        import tempfile
        fallback = os.path.join(tempfile.gettempdir(), "SmartKeyboard")
        os.makedirs(fallback, exist_ok=True)
        return fallback


class LRUCache:
    """In-memory LRU cache backed by OrderedDict. Thread-safe."""

    def __init__(self, namespace: str, maxsize: int = 200, **_):
        self._namespace = namespace
        self._maxsize   = maxsize
        self._mem: OrderedDict = OrderedDict()
        self._lock  = threading.Lock()
        self.hits   = 0
        self.misses = 0

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key in self._mem:
                self._mem.move_to_end(key)
                self.hits += 1
                return self._mem[key]
            self.misses += 1
            return None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._mem:
                self._mem.move_to_end(key)
            self._mem[key] = value
            if len(self._mem) > self._maxsize:
                self._mem.popitem(last=False)

    def __len__(self) -> int:
        return len(self._mem)
