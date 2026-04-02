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


def _make_icon_image(enabled: bool) -> Image.Image:
    """
    Draw a simple 64x64 keyboard icon programmatically.
    Green when enabled, grey when disabled — no external image file needed.
    """
    size   = 64
    img    = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(img)
    color  = (124, 106, 247) if enabled else (100, 100, 120)   # purple / grey

    # Outer rounded rectangle (keyboard body)
    draw.rounded_rectangle([4, 14, 60, 50], radius=8, fill=color)

    # Three rows of white key dots
    key_color = (255, 255, 255, 200)
    for row, y in enumerate([22, 31, 40]):
        keys_in_row = 7 - row
        start_x     = 10 + row * 3
        for k in range(keys_in_row):
            x = start_x + k * 7
            draw.rectangle([x, y, x + 4, y + 4], fill=key_color)

    return img


class TrayManager:
    """
    Manages the system tray icon lifecycle.

    Usage:
        tray = TrayManager(on_toggle=my_fn, on_quit=my_fn)
        tray.start()          # Non-blocking — runs on a daemon thread
        tray.stop()           # Call to cleanly exit
    """

    def __init__(self, on_toggle=None, on_quit=None, hotkey_str="Ctrl+Shift+T"):
        self._on_toggle  = on_toggle   # Callable[[bool], None]  — receives new enabled state
        self._on_quit    = on_quit     # Callable[]
        self._hotkey_str = hotkey_str
        self._enabled    = True
        self._icon       = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Start the tray icon on a background daemon thread."""
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def stop(self):
        if self._icon:
            self._icon.stop()

    def set_enabled(self, enabled: bool):
        """Update the tray icon to reflect the current enabled state."""
        self._enabled = enabled
        if self._icon:
            self._icon.icon = _make_icon_image(enabled)
            self._icon.menu = self._build_menu()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self):
        self._icon = pystray.Icon(
            name    = "smart_keyboard",
            icon    = _make_icon_image(self._enabled),
            title   = "Smart Desktop Keyboard",
            menu    = self._build_menu(),
        )
        self._icon.run()

    def _build_menu(self) -> pystray.Menu:
        toggle_label = "✓  Enabled" if self._enabled else "   Disabled"
        return pystray.Menu(
            pystray.MenuItem(
                "Smart Desktop Keyboard",
                None,
                enabled=False,   # Non-clickable header
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(toggle_label, self._handle_toggle),
            pystray.MenuItem(
                f"Hotkey: {self._hotkey_str}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._handle_quit),
        )

    def _handle_toggle(self, icon, item):
        self._enabled = not self._enabled
        self.set_enabled(self._enabled)
        if self._on_toggle:
            self._on_toggle(self._enabled)

    def _handle_quit(self, icon, item):
        if self._on_quit:
            self._on_quit()
        icon.stop()
