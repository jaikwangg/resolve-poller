#!/usr/bin/env python3
"""A2 — idle + "Resolve อยู่หน้าสุดไหม" (metric #2). cross-platform: macOS / Linux(X11) / Windows.
ไม่ต้องขอ permission (idle เป็นตัวเลขล้วน). import แบบ defensive → ไม่ crash ถ้า OS ไม่รองรับ.

ส่งออกแค่ boolean — **ไม่ส่งชื่อแอป/ชื่อหน้าต่าง** (ชื่อหน้าต่างบน Windows/Linux = แท็บ browser, หัวข้ออีเมล, ชื่อไฟล์)
ตัดสินจาก identity ของ process ตรง ๆ ไม่ใช่ substring ของชื่อ ("how to resolve…" ในแท็บ ≠ Resolve):
  macOS   = bundle id ของแอปหน้าสุด ∈ EDITORTRACK_RESOLVE_BUNDLES
  Windows = ชื่อไฟล์ exe ของ process เจ้าของหน้าต่างหน้าสุด ∈ EDITORTRACK_RESOLVE_PROCS (GetWindowThreadProcessId)
  Linux   = /proc/<pid>/exe (หรือ comm) ของ _NET_WM_PID หน้าต่างหน้าสุด ∈ EDITORTRACK_RESOLVE_PROCS · ไม่มี pid → WM_CLASS ตรงตัว

เทสด้วยตา:  python activity_sampler.py --watch   (พิมพ์ทุก 2 วิ)
  - ขยับเมาส์/พิมพ์ → idle_sec เล็ก · หยุด → โตขึ้นเรื่อย ๆ
  - โฟกัส Resolve → resolve_frontmost=true · สลับแอปอื่น (รวม browser ที่มีคำว่า resolve) → false

หมายเหตุห้อง grade (Linux): Resolve มักเต็มจอตลอด → สัญญาณหลักคือ idle
  frontmost บน Linux ต้องมี xdotool หรือ xprop (ไม่มี → resolve_frontmost=false)
"""
import os, sys, time, json


def _set(env, default):
    return {x.strip().lower() for x in os.environ.get(env, default).split(",") if x.strip()}


RESOLVE_BUNDLE_IDS = _set("EDITORTRACK_RESOLVE_BUNDLES",
                          "com.blackmagic-design.DaVinciResolve,com.blackmagic-design.DaVinciResolveLite")   # macOS
RESOLVE_PROCS = _set("EDITORTRACK_RESOLVE_PROCS", "resolve,resolve.exe")      # ชื่อไฟล์ executable (Linux/Windows)
LOCK_BUNDLES = {"com.apple.loginwindow"}                                     # macOS: จอล็อก/หน้า login
LOCK_PROCS = {"lockapp.exe", "logonui.exe"}                                  # Windows: จอล็อก


def is_resolve_bundle(bid):
    return (bid or "").strip().lower() in RESOLVE_BUNDLE_IDS


def is_resolve_proc(exe):
    """exe = path หรือชื่อไฟล์ของ executable → ตรงตัว (ไม่ใช่ substring)"""
    base = os.path.basename((exe or "").strip().replace("\\", "/")).lower()
    return bool(base) and base in RESOLVE_PROCS


# ---------- macOS ----------
def _macos():
    from Quartz import (CGEventSourceSecondsSinceLastEventType,
                        kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)
    from AppKit import NSWorkspace
    idle = CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    bid = ((app.bundleIdentifier() or "") if app else "").lower()
    return float(idle), is_resolve_bundle(bid), bid in LOCK_BUNDLES


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
    exe = ""
    hwnd = user32.GetForegroundWindow()
    if hwnd:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    exe = buf.value
            finally:
                kernel32.CloseHandle(h)
    base = os.path.basename(exe.replace("\\", "/")).lower()
    return idle, is_resolve_proc(exe), base in LOCK_PROCS


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
    return idle_ms / 1000.0, _linux_resolve_frontmost(), None


def _run(cmd):
    import subprocess
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def parse_xprop_pid(s):
    """'_NET_WM_PID(CARDINAL) = 1234' → 1234"""
    try:
        return int(s.rsplit("=", 1)[1].strip()) if "=" in s else int(s.strip())
    except (ValueError, IndexError):
        return None


def parse_wm_class(s):
    """'WM_CLASS(STRING) = "resolve", "Resolve"' → ["resolve", "resolve"]"""
    return [x.strip().strip('"').lower() for x in s.split("=", 1)[1].split(",")] if "=" in s else []


def proc_exe(pid, proc="/proc"):
    """ชื่อ executable ของ pid (Linux): /proc/pid/exe → ไม่ได้ (สิทธิ์) ใช้ /proc/pid/comm"""
    try:
        return os.readlink(f"{proc}/{pid}/exe")
    except OSError:
        pass
    try:
        with open(f"{proc}/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _linux_resolve_frontmost():
    """pid ของหน้าต่างหน้าสุด → executable ตรงตัว · ไม่มี pid → WM_CLASS ตรงตัว · อ่านไม่ได้ = False
    ไม่อ่านชื่อหน้าต่าง (_NET_WM_NAME) เลย"""
    win = _run(["xdotool", "getactivewindow"])
    if not win:
        root = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        win = root.split()[-1] if root and "0x" in root else ""
    if not win:
        return False
    pid = parse_xprop_pid(_run(["xdotool", "getwindowpid", win])) if win.isdigit() else None
    if pid is None:
        pid = parse_xprop_pid(_run(["xprop", "-id", win, "_NET_WM_PID"]))
    if pid:
        exe = proc_exe(pid)
        if exe:
            return is_resolve_proc(exe)
    return any(c in RESOLVE_PROCS for c in parse_wm_class(_run(["xprop", "-id", win, "WM_CLASS"])))


def sample():
    """→ {idle_sec, resolve_frontmost, locked, platform} · ไม่มีชื่อแอป/หน้าต่าง
    locked = จอล็อก/หน้า login (macOS, Windows) · None = OS นี้ไม่รู้"""
    plat = sys.platform
    try:
        if plat == "darwin":
            idle, is_resolve, locked = _macos()
        elif plat.startswith("linux"):
            idle, is_resolve, locked = _linux()
        elif plat.startswith("win"):
            idle, is_resolve, locked = _windows()
        else:
            return {"idle_sec": None, "resolve_frontmost": False, "unavailable": True, "platform": plat}
    except Exception as e:
        return {"idle_sec": None, "resolve_frontmost": False,
                "unavailable": True, "error": type(e).__name__, "platform": plat}
    return {"idle_sec": round(float(idle), 1), "resolve_frontmost": bool(is_resolve),
            "locked": locked, "platform": plat}


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
