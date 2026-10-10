"""Windows 11-style brightness flyout (like the volume pop-up), drawn with tkinter.

Runs Tk on its own thread; call `BrightnessOSD.show(value)` from any thread.
The window never takes focus and is click-through.
"""
import ctypes
import queue
import threading
import tkinter as tk
import winreg
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.GetForegroundWindow.restype = w.HWND
user32.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, w.LPVOID, w.UINT]

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
HWND_TOPMOST = w.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
SPI_GETWORKAREA = 0x30
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2

HOLD_MS = 1200      # visible time after the last change
FADE_STEPS, FADE_MS = 8, 20
GLYPH_BRIGHTNESS = ""


def _reg(path, name, default=None):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return default


def _theme():
    """Colours matching the Windows 11 flyouts, following light/dark mode and the accent colour."""
    light = _reg(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "SystemUsesLightTheme", 0) == 1
    palette = _reg(r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent", "AccentPalette")
    if palette and len(palette) >= 32:
        # AccentPalette: 8 RGBA entries, light3..dark3. Win11 uses Light2 in dark mode, Dark1 in light mode.
        i = 4 if light else 1
        accent = "#%02x%02x%02x" % tuple(palette[i * 4:i * 4 + 3])
    else:
        accent = "#005fb8" if light else "#4cc2ff"
    if light:
        return dict(bg="#f9f9f9", fg="#1a1a1a", track="#868686", thumb="#ffffff", ring="#d6d6d6", accent=accent)
    return dict(bg="#2c2c2c", fg="#ffffff", track="#9a9a9a", thumb="#454545", ring="#3a3a3a", accent=accent)


class BrightnessOSD:
    def __init__(self, log=lambda *a: None):
        self._log = log
        self._q = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def show(self, value):
        self._q.put(value)

    # --- everything below runs on the Tk thread ---
    def _run(self):
        self.scale = user32.GetDpiForSystem() / 96
        s = lambda v: round(v * self.scale)
        self.W, self.H = s(196), s(48)
        self.track_x0, self.track_x1, self.cy = s(48), s(140), self.H // 2

        root = self.root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True, "-alpha", 0.0)
        self.colors = _theme()
        self.canvas = tk.Canvas(root, width=self.W, height=self.H, highlightthickness=0, bd=0)
        self.canvas.pack()
        root.geometry(f"{self.W}x{self.H}+0+0")
        root.update_idletasks()

        self.hwnd = int(root.wm_frame(), 16)
        ex = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex | WS_EX_TOPMOST | WS_EX_TRANSPARENT |
                                 WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE)
        pref = ctypes.c_int(DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(w.HWND(self.hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), 4)

        fg = user32.GetForegroundWindow()
        root.deiconify()  # mapped once, fully transparent; shown/hidden via alpha from here on
        root.update()
        if fg:
            user32.SetForegroundWindow(fg)

        self._hide_job = None
        self._alpha = 0.0
        root.after(16, self._poll)
        root.mainloop()

    def _poll(self):
        value = None
        try:
            while True:
                value = self._q.get_nowait()
        except queue.Empty:
            pass
        if value is not None:
            self._present(value)
        self.root.after(16, self._poll)

    def _present(self, value):
        if self._alpha == 0.0:
            self.colors = _theme()  # pick up theme/accent changes each time it appears
            self._place()
        self._draw(value)
        self._set_alpha(1.0)
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
        self._hide_job = self.root.after(HOLD_MS, self._fade, FADE_STEPS)

    def _place(self):
        rc = w.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0)
        x = rc.left + (rc.right - rc.left - self.W) // 2
        y = rc.bottom - self.H - round(24 * self.scale)
        self.root.geometry(f"+{x}+{y}")
        # Move via Win32 too: Tk applies geometry lazily and can leave the window at its old spot.
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
        r = w.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        self._log(f"osd at {x},{y} (work area {rc.left},{rc.top},{rc.right},{rc.bottom}); actual {r.left},{r.top}")

    def _fade(self, steps):
        if steps <= 0:
            self._set_alpha(0.0)
            self._hide_job = None
            return
        self._set_alpha(steps / (FADE_STEPS + 1))
        self._hide_job = self.root.after(FADE_MS, self._fade, steps - 1)

    def _set_alpha(self, a):
        self._alpha = a
        self.root.attributes("-alpha", a)

    def _draw(self, value):
        c, k, s = self.canvas, self.colors, lambda v: round(v * self.scale)
        c.delete("all")
        c.configure(bg=k["bg"])
        c.create_rectangle(0, 0, self.W - 1, self.H - 1, outline=k["ring"])
        c.create_text(s(24), self.cy, text=GLYPH_BRIGHTNESS, fill=k["fg"], font=("Segoe Fluent Icons", -s(16)))

        x0, x1, cy = self.track_x0, self.track_x1, self.cy
        xv = x0 + (x1 - x0) * max(0, min(100, value)) / 100
        width = max(2, s(4))
        c.create_line(x0, cy, x1, cy, fill=k["track"], width=width, capstyle="round")
        c.create_line(x0, cy, xv, cy, fill=k["accent"], width=width, capstyle="round")
        r_out, r_in = s(10), s(6)
        c.create_oval(xv - r_out, cy - r_out, xv + r_out, cy + r_out, fill=k["thumb"], outline=k["ring"])
        c.create_oval(xv - r_in, cy - r_in, xv + r_in, cy + r_in, fill=k["accent"], outline="")

        c.create_text(s(170), cy, text=str(value), fill=k["fg"], font=("Segoe UI Variable Text", -s(14)))
