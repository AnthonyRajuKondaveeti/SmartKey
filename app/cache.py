"""
cache.py
--------
Simple in-memory LRU cache for grammar and translation results.

200 entries per namespace; evicts the least-recently-used entry when full.
Shared across correction/translation calls within a session so repeated
identical inputs return instantly without hitting the model again.
"""

import os
from collections import OrderedDict
from typing import Optional


def appdata_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "SmartKeyboard")
    os.makedirs(d, exist_ok=True)
    return d


class LRUCache:
    """In-memory LRU cache backed by OrderedDict."""

    def __init__(self, namespace: str, maxsize: int = 200, **_):
        self._namespace = namespace
        self._maxsize   = maxsize
        self._mem: OrderedDict = OrderedDict()
        self.hits   = 0
        self.misses = 0

    def get(self, key: str) -> Optional[str]:
        if key in self._mem:
            self._mem.move_to_end(key)
            self.hits += 1
            return self._mem[key]
        self.misses += 1
        return None

    def put(self, key: str, value: str) -> None:
        if key in self._mem:
            self._mem.move_to_end(key)
        self._mem[key] = value
        if len(self._mem) > self._maxsize:
            self._mem.popitem(last=False)

    def __len__(self) -> int:
        return len(self._mem)
