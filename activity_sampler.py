#!/usr/bin/env python3
"""A2 — idle + frontmost บน macOS (ไม่ต้องขอ permission).
import แบบ defensive → import ได้บน Linux/เครื่องไม่มี pyobjc (คืน unavailable)."""

# PROBE: ยืนยัน bundle id จริงด้วย probe.py แล้วแก้ตรงนี้
RESOLVE_BUNDLE_IDS = {"com.blackmagic-design.DaVinciResolve"}
RESOLVE_NAME_HINTS = ("davinci", "resolve")

try:
    from Quartz import (
        CGEventSourceSecondsSinceLastEventType,
        kCGEventSourceStateHIDSystemState,   # hardware-only → ทน software mouse-jiggler
        kCGAnyInputEventType,
    )
    from AppKit import NSWorkspace
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False


def sample():
    if not _AVAILABLE:
        return {"idle_sec": None, "frontmost_bundle": "", "frontmost_name": "",
                "resolve_frontmost": False, "unavailable": True}
    idle = CGEventSourceSecondsSinceLastEventType(
        kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    bid = (app.bundleIdentifier() or "") if app else ""
    name = (app.localizedName() or "") if app else ""
    is_resolve = bid in RESOLVE_BUNDLE_IDS or any(h in name.lower() for h in RESOLVE_NAME_HINTS)
    return {"idle_sec": round(float(idle), 1), "frontmost_bundle": bid,
            "frontmost_name": name, "resolve_frontmost": bool(is_resolve)}


if __name__ == "__main__":
    import json
    print(json.dumps(sample(), ensure_ascii=False, indent=2))
