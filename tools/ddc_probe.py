import ctypes
from ctypes import wintypes as w
dx = ctypes.WinDLL("dxva2"); u = ctypes.WinDLL("user32")
class PM(ctypes.Structure): _fields_=[("h",w.HANDLE),("d",ctypes.c_wchar*128)]
MEP = ctypes.WINFUNCTYPE(w.BOOL, w.HMONITOR, w.HDC, ctypes.POINTER(w.RECT), w.LPARAM)
mons=[]
u.EnumDisplayMonitors(None,None,MEP(lambda h,dc,r,l: mons.append(h) or True),0)
for hm in mons:
    n=w.DWORD(); dx.GetNumberOfPhysicalMonitorsFromHMONITOR(w.HMONITOR(hm),ctypes.byref(n))
    arr=(PM*n.value)(); dx.GetPhysicalMonitorsFromHMONITOR(w.HMONITOR(hm),n,arr)
    for p in arr:
        mn,cur,mx=w.DWORD(),w.DWORD(),w.DWORD()
        ok=dx.GetMonitorBrightness(p.h,ctypes.byref(mn),ctypes.byref(cur),ctypes.byref(mx))
        vt=ctypes.c_int(); c=w.DWORD(); m=w.DWORD()
        ok2=dx.GetVCPFeatureAndVCPFeatureReply(p.h,ctypes.c_ubyte(0x10),ctypes.byref(vt),ctypes.byref(c),ctypes.byref(m))
        print(p.d, "GetMonitorBrightness:", bool(ok), mn.value,cur.value,mx.value, "| VCP10:", bool(ok2), c.value, m.value)
    dx.DestroyPhysicalMonitors(n,arr)
