"""
tray.py
-------
System tray icon for Smart Desktop Keyboard using pystray.
Provides: Enable/Disable toggle, hotkey reminder, Quit.

Runs on its own thread — pystray manages its own event loop.
"""

import threading
import pystray
from PIL import Image, ImageDraw
from version import __version__


def _make_icon_image(enabled: bool, loading: bool = False) -> Image.Image:
    """Draw a proportional keyboard icon (default 64×64)."""
    s    = 64
    img  = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if loading:
        color = (210, 150, 40)
    elif enabled:
        color = (124, 106, 247)
    else:
        color = (100, 100, 120)

    pad = round(s * 0.06)
    top = round(s * 0.22)
    bot = round(s * 0.78)
    draw.rounded_rectangle([pad, top, s - pad, bot], radius=round(s * 0.12), fill=color)
    key_color = (255, 255, 255, 200)
    key_sz    = max(1, round(s * 0.07))
    row_ys    = [round(s * f) for f in (0.34, 0.48, 0.62)]
    row_step  = round(s * 0.05)
    key_step  = round(s * 0.11)
    x0        = round(s * 0.16)
    for row, y in enumerate(row_ys):
        count = 7 - row
        for k in range(count):
            x = x0 + row * row_step + k * key_step
            draw.rectangle([x, y, x + key_sz, y + key_sz], fill=key_color)
    return img


class TrayManager:
    """
    Manages the system tray icon lifecycle.

    Usage:
        tray = TrayManager(on_toggle=my_fn, on_quit=my_fn)
        tray.start()          # Non-blocking — runs on a daemon thread
        tray.stop()           # Call to cleanly exit
    """

    def __init__(self, on_toggle=None, on_quit=None, on_change_hotkey=None, hotkey_str="Ctrl+Shift+T"):
        self._on_toggle        = on_toggle
        self._on_quit          = on_quit
        self._on_change_hotkey = on_change_hotkey
        self._hotkey_str       = hotkey_str
        self._enabled          = True
        self._loading          = True   # amber until all models report ready
        self._load_status      = "Loading models..."
        self._icon             = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Start the tray icon on a background daemon thread."""
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def stop(self):
        if self._icon:
            self._icon.stop()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self._refresh_icon()

    def set_hotkey(self, hotkey_label: str):
        self._hotkey_str = hotkey_label
        self._refresh_icon()

    def set_loading(self, is_loading: bool, status: str = ""):
        """Call from main thread as each model finishes loading.
        is_loading=False + empty status means all models are ready."""
        self._loading     = is_loading
        self._load_status = status
        self._refresh_icon()

    def _refresh_icon(self):
        if not self._icon:
            return
        self._icon.icon  = _make_icon_image(self._enabled, self._loading)
        self._icon.title = self._load_status if self._loading else f"Smart Keyboard v{__version__} — Ready"
        self._icon.menu  = self._build_menu()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self):
        self._icon = pystray.Icon(
            name  = "smart_keyboard",
            icon  = _make_icon_image(self._enabled, self._loading),
            title = self._load_status,
            menu  = self._build_menu(),
        )
        self._icon.run()

    def _build_menu(self) -> pystray.Menu:
        toggle_label = "✓  Enabled" if self._enabled else "   Disabled"
        items = [
            pystray.MenuItem("Smart Keyboard", None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
        if self._loading:
            items.append(
                pystray.MenuItem(self._load_status, None, enabled=False)
            )
            items.append(pystray.Menu.SEPARATOR)
        items += [
            pystray.MenuItem(toggle_label, self._handle_toggle),
            pystray.MenuItem(f"Hotkey: {self._hotkey_str}", None, enabled=False),
            pystray.MenuItem("Change hotkey...", self._handle_change_hotkey),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._handle_quit),
        ]
        return pystray.Menu(*items)

    def _handle_toggle(self, icon, item):
        self._enabled = not self._enabled
        self.set_enabled(self._enabled)
        if self._on_toggle:
            self._on_toggle(self._enabled)

    def _handle_change_hotkey(self, icon, item):
        if self._on_change_hotkey:
            self._on_change_hotkey()

    def _handle_quit(self, icon, item):
        if self._on_quit:
            self._on_quit()
        icon.stop()
