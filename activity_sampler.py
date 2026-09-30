#!/usr/bin/env python3
"""A2 — idle + frontmost (metric #2). cross-platform: macOS / Linux(X11) / Windows.
ไม่ต้องขอ permission (idle เป็นตัวเลขล้วน). import แบบ defensive → ไม่ crash ถ้า OS ไม่รองรับ.

เทสด้วยตา:  python activity_sampler.py --watch   (พิมพ์ทุก 2 วิ)
  - ขยับเมาส์/พิมพ์ → idle_sec เล็ก · หยุด → โตขึ้นเรื่อย ๆ
  - โฟกัส Resolve → resolve_frontmost=true · สลับแอปอื่น → false

หมายเหตุห้อง grade (Linux): Resolve มักเต็มจอตลอด → สัญญาณหลักคือ idle
  frontmost บน Linux เป็น best-effort (ต้องมี xdotool หรือ xprop; ไม่มีก็คืน "")
"""
import sys, time, json

RESOLVE_HINTS = ("davinci", "resolve")
RESOLVE_BUNDLE_IDS = {"com.blackmagic-design.DaVinciResolve"}   # macOS


# ---------- macOS ----------
def _macos():
    from Quartz import (CGEventSourceSecondsSinceLastEventType,
                        kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)
    from AppKit import NSWorkspace
    idle = CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    name = (app.localizedName() or "") if app else ""
    bid = (app.bundleIdentifier() or "") if app else ""
    return float(idle), name, bid


# ---------- Windows ----------
def _windows():
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    kernel32.GetTickCount.restype = wintypes.DWORD      # unsigned 32-bit (ไม่งั้น ctypes อ่านเป็น signed → ติดลบ)
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    user32.GetLastInputInfo(ctypes.byref(li))
    idle = ((kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0   # modular → กัน 32-bit wrap
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return idle, (buf.value or ""), ""


# ---------- Linux (X11) ----------
def _linux():
    import ctypes

    class XSS(ctypes.Structure):
        _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                    ("since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("event_mask", ctypes.c_ulong)]

    x11 = ctypes.CDLL("libX11.so.6")
    xss = ctypes.CDLL("libXss.so.1")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xss.XScreenSaverAllocInfo.restype = ctypes.c_void_p
    xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]

    dpy = x11.XOpenDisplay(None)
    if not dpy:
        raise RuntimeError("no X11 display (headless/Wayland?)")
    try:
        root = x11.XDefaultRootWindow(dpy)
        info = xss.XScreenSaverAllocInfo()
        xss.XScreenSaverQueryInfo(dpy, root, info)
        idle_ms = ctypes.cast(info, ctypes.POINTER(XSS)).contents.idle
    finally:
        x11.XCloseDisplay(dpy)
    return idle_ms / 1000.0, _linux_active_window(), ""


def _linux_active_window():
    """best-effort: xdotool → xprop → "" (frontmost ไม่ critical กับห้อง grade)"""
    import subprocess
    for cmd in (["xdotool", "getactivewindow", "getwindowname"],
                ["sh", "-c", "xprop -id $(xprop -root _NET_ACTIVE_WINDOW | grep -o '0x[0-9a-f]*') _NET_WM_NAME"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if out.returncode == 0 and out.stdout.strip():
                s = out.stdout.strip()
                if "_NET_WM_NAME" in s and '"' in s:
                    s = s.split('"', 2)[1]
                return s
        except Exception:
            continue
    return ""


def sample():
    plat = sys.platform
    try:
        if plat == "darwin":
            idle, name, bid = _macos()
        elif plat.startswith("linux"):
            idle, name, bid = _linux()
        elif plat.startswith("win"):
            idle, name, bid = _windows()
        else:
            return {"idle_sec": None, "frontmost_name": "", "resolve_frontmost": False,
                    "unavailable": True, "platform": plat}
    except Exception as e:
        return {"idle_sec": None, "frontmost_name": "", "resolve_frontmost": False,
                "unavailable": True, "error": str(e)[:70], "platform": plat}
    is_resolve = bid in RESOLVE_BUNDLE_IDS or any(h in (name or "").lower() for h in RESOLVE_HINTS)
    return {"idle_sec": round(float(idle), 1), "frontmost_name": name, "frontmost_bundle": bid,
            "resolve_frontmost": bool(is_resolve), "platform": plat}


if __name__ == "__main__":
    if "--watch" in sys.argv:
        try:
            while True:
                print(json.dumps(sample(), ensure_ascii=False))
                time.sleep(2)
        except KeyboardInterrupt:
            pass
    else:
        print(json.dumps(sample(), ensure_ascii=False, indent=2))
