"""AULA knob remapper: Ctrl + knob -> monitor brightness (DDC/CI), with a Windows-style flyout.

The knob sends Volume Up/Down. While Ctrl is held, those keys are swallowed and the
brightness of every DDC/CI-capable monitor is stepped instead. Without Ctrl, the knob
keeps controlling volume.

Run:   pythonw aula_knob.py          (background, no console)
       python  aula_knob.py --debug  (console with log, Ctrl+C to quit)
"""
import ctypes
import sys
import threading
from ctypes import wintypes as w

from osd import BrightnessOSD

STEP = 5  # brightness % per knob click

VK_CONTROL, VK_VOLUME_DOWN, VK_VOLUME_UP = 0x11, 0xAE, 0xAF
WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN = 13, 0x0100, 0x0104
DEBUG = "--debug" in sys.argv

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HMONITOR, w.HDC, ctypes.POINTER(w.RECT), w.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("hPhysicalMonitor", w.HANDLE), ("szPhysicalMonitorDescription", ctypes.c_wchar * 128)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD]
user32.SetWindowsHookExW.restype = w.HHOOK
user32.CallNextHookEx.argtypes = [w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [w.HHOOK]
user32.EnumDisplayMonitors.argtypes = [w.HDC, ctypes.POINTER(w.RECT), MONITORENUMPROC, w.LPARAM]
user32.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID]
kernel32.GetModuleHandleW.restype = w.HMODULE
kernel32.CreateMutexW.restype = w.HANDLE
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [w.HMONITOR, ctypes.POINTER(w.DWORD)]
dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [w.HMONITOR, w.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
dxva2.DestroyPhysicalMonitors.argtypes = [w.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
dxva2.GetMonitorBrightness.argtypes = [w.HANDLE] + [ctypes.POINTER(w.DWORD)] * 3
dxva2.SetMonitorBrightness.argtypes = [w.HANDLE, w.DWORD]


def log(*args):
    if DEBUG:
        print(*args, flush=True)


class Brightness:
    """Owns the DDC/CI monitor handles. All DDC calls happen on one worker thread, and
    knob clicks are coalesced so fast spins don't queue up slow (~50ms) DDC writes."""

    def __init__(self):
        self._pending = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._monitors = []  # [PHYSICAL_MONITOR array, [(handle, min, max, current), ...]]
        threading.Thread(target=self._run, daemon=True).start()

    def nudge(self, clicks):
        with self._lock:
            self._pending += clicks
        self._wake.set()

    def _open_monitors(self):
        self._close_monitors()
        hmons = []
        cb = MONITORENUMPROC(lambda h, dc, rc, lp: hmons.append(h) or True)
        user32.EnumDisplayMonitors(None, None, cb, 0)
        for hmon in hmons:
            n = w.DWORD()
            if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) or not n.value:
                continue
            arr = (PHYSICAL_MONITOR * n.value)()
            if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n, arr):
                continue
            usable = []
            for pm in arr:
                lo, cur, hi = w.DWORD(), w.DWORD(), w.DWORD()
                if dxva2.GetMonitorBrightness(pm.hPhysicalMonitor, ctypes.byref(lo), ctypes.byref(cur), ctypes.byref(hi)):
                    usable.append([pm.hPhysicalMonitor, lo.value, hi.value, cur.value])
                    log(f"monitor '{pm.szPhysicalMonitorDescription}' brightness {cur.value} ({lo.value}-{hi.value})")
            self._monitors.append([arr, usable])
        if not any(u for _, u in self._monitors):
            log("no DDC/CI monitor found (is DDC/CI enabled in the monitor's menu?)")

    def _close_monitors(self):
        for arr, _ in self._monitors:
            dxva2.DestroyPhysicalMonitors(len(arr), arr)
        self._monitors = []

    def _run(self):
        self._open_monitors()
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                clicks, self._pending = self._pending, 0
            if clicks and not self._apply(clicks):
                # Monitor was likely replugged / woke from sleep: reopen and retry once.
                log("DDC write failed, reopening monitors")
                self._open_monitors()
                self._apply(clicks)

    def _apply(self, clicks):
        ok = any(u for _, u in self._monitors)
        shown = False
        for _, usable in self._monitors:
            for mon in usable:
                handle, lo, hi, cur = mon
                target = max(lo, min(hi, cur + clicks * STEP * (hi - lo) // 100))
                if not shown:  # show before the slow DDC write so the flyout feels instant
                    osd.show(round(100 * (target - lo) / ((hi - lo) or 1)))
                    shown = True
                if target == cur:
                    continue
                if dxva2.SetMonitorBrightness(handle, target):
                    mon[3] = target
                    log(f"brightness -> {target}")
                else:
                    ok = False
        return ok


ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # per-monitor DPI aware, so the flyout is crisp
osd = BrightnessOSD(log)
brightness = Brightness()


@HOOKPROC
def keyboard_hook(code, wparam, lparam):
    if code == 0:
        vk = KBDLLHOOKSTRUCT.from_address(lparam).vkCode
        if vk in (VK_VOLUME_UP, VK_VOLUME_DOWN) and user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
            if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                brightness.nudge(1 if vk == VK_VOLUME_UP else -1)
            return 1  # swallow both key down and key up so volume never changes
    return user32.CallNextHookEx(None, code, wparam, lparam)


def main():
    kernel32.CreateMutexW(None, False, "Local\\aula_knob_remapper")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        log("already running")
        return
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_hook, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        sys.exit(f"SetWindowsHookEx failed: {ctypes.get_last_error()}")
    user32.SetTimer(None, 0, 500, None)  # wake the loop periodically so Ctrl+C is handled
    log(f"running: Ctrl + knob = brightness ({STEP}% per click). Ctrl+C to quit.")
    msg = w.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnhookWindowsHookEx(hook)


if __name__ == "__main__":
    main()
