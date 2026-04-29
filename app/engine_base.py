"""
engine_base.py
--------------
BaseEngine — shared lifecycle boilerplate for all ONNX inference engines.

Eliminates the ~150 lines of identical __init__ / load / is_ready / is_failed /
_wait_ready code that was duplicated across GrammarEngine, TranslationEngine,
and ToneEngine.

Subclass contract:
    - Call super().__init__(cache_namespace, cache_size, thread_prefix, load_timeout)
    - Implement _load_model() — runs on a daemon thread; raises on failure
    - Call self._wait_ready() at the start of any sync inference method
"""

import threading
import concurrent.futures
import time
from typing import Callable, Optional

from logger import log
from cache  import LRUCache


class BaseEngine:

    def __init__(
        self,
        cache_namespace: str,
        cache_size:       int = 200,
        thread_prefix:    str = "Engine",
        load_timeout:     int = 60,
    ):
        self._cache        = LRUCache(cache_namespace, cache_size)
        self._ready        = threading.Event()
        self._failed       = False
        self._load_called  = False
        self._lock         = threading.Lock()
        self._load_timeout = load_timeout
        self._executor     = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix=thread_prefix
        )
        self._thread_prefix = thread_prefix

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(
        self,
        on_ready: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Start loading the model on a background daemon thread."""
        self._load_called = True
        name = self.__class__.__name__

        def _load():
            t_start = time.monotonic()
            try:
                self._load_model()
                elapsed = (time.monotonic() - t_start) * 1000
                self._ready.set()
                log.info(f"{name} ready in {elapsed:.0f}ms")
                if on_ready:
                    on_ready()
            except Exception as e:
                elapsed = (time.monotonic() - t_start) * 1000
                log.error(f"{name} load failed after {elapsed:.0f}ms: {e}")
                self._failed = True
                if on_error:
                    on_error(str(e))

        threading.Thread(
            target=_load, daemon=True, name=f"{self._thread_prefix}-Load"
        ).start()

    def _load_model(self) -> None:
        """Subclasses must implement: load ONNX sessions and tokenizers here."""
        raise NotImplementedError

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def is_failed(self) -> bool:
        return self._failed

    # ── Inference guard ───────────────────────────────────────────────────────

    def _wait_ready(self) -> None:
        """
        Fail-fast pre-check for sync inference methods.

        Raises RuntimeError immediately if:
          - load() was never called
          - the model failed to load at startup
          - the model has not finished loading within load_timeout seconds
        """
        name = self.__class__.__name__
        if not self._load_called:
            raise RuntimeError(
                f"{name}.load() was never called. "
                "Call engine.load() at app startup before using the engine."
            )
        if self._failed:
            raise RuntimeError(
                f"{name} failed to load at startup — check logs and restart."
            )
        if not self._ready.wait(timeout=self._load_timeout):
            raise RuntimeError(
                f"{name} did not finish loading within {self._load_timeout}s. "
                "Check that model files are complete."
            )
