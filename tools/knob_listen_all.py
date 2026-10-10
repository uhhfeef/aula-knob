"""Read-only listener: logs consumer-control raw HID reports (with source device) and
low-level keyboard VK events. Nothing is blocked or changed."""
import ctypes, sys, time
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR)]

class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", w.USHORT), ("usUsage", w.USHORT), ("dwFlags", w.DWORD), ("hwndTarget", w.HWND)]

class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", w.DWORD), ("dwSize", w.DWORD), ("hDevice", w.HANDLE), ("wParam", w.WPARAM)]

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]

user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
user32.CreateWindowExW.restype = w.HWND
user32.GetRawInputData.argtypes = [w.HANDLE, w.UINT, w.LPVOID, ctypes.POINTER(w.UINT), w.UINT]
user32.GetRawInputDeviceInfoW.argtypes = [w.HANDLE, w.UINT, w.LPVOID, ctypes.POINTER(w.UINT)]
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD]
user32.SetWindowsHookExW.restype = w.HHOOK
user32.CallNextHookEx.argtypes = [w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID]
kernel32.GetModuleHandleW.restype = w.HMODULE

WM_INPUT, WM_TIMER = 0x00FF, 0x0113
RID_INPUT, RIDI_DEVICENAME, RIDEV_INPUTSINK = 0x10000003, 0x20000007, 0x00000100
T0 = time.perf_counter()
names = {}

def ts():
    return f"{time.perf_counter() - T0:8.3f}"

def devname(h):
    if h not in names:
        n = w.UINT(0)
        user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, None, ctypes.byref(n))
        buf = ctypes.create_unicode_buffer(n.value + 1)
        user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf, ctypes.byref(n))
        names[h] = buf.value
    return names[h]

@WNDPROC
def wndproc(hwnd, msg, wp, lp):
    if msg == WM_INPUT:
        size = w.UINT(0)
        hdr = ctypes.sizeof(RAWINPUTHEADER)
        user32.GetRawInputData(lp, RID_INPUT, None, ctypes.byref(size), hdr)
        buf = ctypes.create_string_buffer(size.value)
        user32.GetRawInputData(lp, RID_INPUT, buf, ctypes.byref(size), hdr)
        h = RAWINPUTHEADER.from_buffer_copy(buf)
        sz, cnt = ctypes.c_uint32.from_buffer_copy(buf, hdr).value, ctypes.c_uint32.from_buffer_copy(buf, hdr + 4).value
        data = buf.raw[hdr + 8: hdr + 8 + sz * cnt]
        print(f"{ts()} RAW  dev={devname(h.hDevice)}  report={data.hex(' ')}", flush=True)
    elif msg == WM_TIMER:
        user32.PostQuitMessage(0)
    return user32.DefWindowProcW(hwnd, msg, wp, lp)

@HOOKPROC
def kbproc(code, wp, lp):
    k = KBDLLHOOKSTRUCT.from_address(lp)
    if code == 0 and k.vkCode in (0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB3):  # only media/volume keys are logged
        ctrl = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
        print(f"{ts()} HOOK vk=0x{k.vkCode:02X} scan=0x{k.scanCode:02X} flags=0x{k.flags:02X} "
              f"{'down' if wp in (0x100, 0x104) else 'up'} ctrl={ctrl}", flush=True)
    return user32.CallNextHookEx(None, code, wp, lp)

def main(seconds):
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW(lpfnWndProc=wndproc, hInstance=hinst, lpszClassName="KnobListen")
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, "KnobListen", "", 0, 0, 0, 0, 0, w.HWND(-3), None, hinst, None)
    # Register every non-keyboard/non-mouse HID collection exposed by the AULA (VID_05AC&PID_024F)
    class RIDL(ctypes.Structure): _fields_ = [("h", w.HANDLE), ("t", w.DWORD)]
    class HIDINFO(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("dwType", w.DWORD), ("vid", w.DWORD), ("pid", w.DWORD),
                    ("ver", w.DWORD), ("page", w.USHORT), ("usage", w.USHORT), ("pad", ctypes.c_byte * 8)]
    n = w.UINT(0)
    user32.GetRawInputDeviceList(None, ctypes.byref(n), ctypes.sizeof(RIDL))
    lst = (RIDL * n.value)()
    user32.GetRawInputDeviceList(lst, ctypes.byref(n), ctypes.sizeof(RIDL))
    pairs = set()
    for d in lst:
        if d.t != 2 or "VID_05AC&PID_024F" not in devname(d.h).upper():
            continue
        info = HIDINFO(cbSize=ctypes.sizeof(HIDINFO)); sz = w.UINT(ctypes.sizeof(HIDINFO))
        user32.GetRawInputDeviceInfoW(d.h, 0x2000000b, ctypes.byref(info), ctypes.byref(sz))
        print(f"collection page=0x{info.page:04X} usage=0x{info.usage:02X}  {devname(d.h)}", flush=True)
        if (info.page, info.usage) not in ((0x01, 0x06), (0x01, 0x02)):
            pairs.add((info.page, info.usage))
    rids = (RAWINPUTDEVICE * len(pairs))(*[RAWINPUTDEVICE(p, u, RIDEV_INPUTSINK, hwnd) for p, u in pairs])
    if not user32.RegisterRawInputDevices(rids, len(pairs), ctypes.sizeof(RAWINPUTDEVICE)):
        sys.exit(f"RegisterRawInputDevices failed: {ctypes.get_last_error()}")
    print("registered:", sorted(pairs), flush=True)
    hook = user32.SetWindowsHookExW(13, kbproc, hinst, 0)
    user32.SetTimer(hwnd, 1, seconds * 1000, None)
    print(f"listening {seconds}s...", flush=True)
    msg = w.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg)); user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnhookWindowsHookEx(hook)
    print("done", flush=True)

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
