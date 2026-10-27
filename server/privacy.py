#!/usr/bin/env python3
"""Server-side data minimisation (docs/DATA_SHEET.md).

Agents from the data-minimisation change on no longer send these fields; older agents still do,
so /ingest drops them before anything is written to disk:
  user                        login name on the workstation
  activity.frontmost_name     name of the app in front
  activity.frontmost_bundle   bundle id of the app in front
"Is the screen locked" (frontmost was loginwindow) is kept as activity.locked = true.
"""
PERSONAL_TOP = ("user",)
PERSONAL_ACTIVITY = ("frontmost_name", "frontmost_bundle")
LOCK_SCREEN_APPS = ("loginwindow",)


def scrub(rec):
    """remove personal fields in place; True if anything was removed."""
    changed = False
    for k in PERSONAL_TOP:
        if k in rec:
            del rec[k]
            changed = True
    act = rec.get("activity")
    if isinstance(act, dict):
        if (act.get("frontmost_name") or "") in LOCK_SCREEN_APPS:
            act["locked"] = True
        for k in PERSONAL_ACTIVITY:
            if k in act:
                del act[k]
                changed = True
    return changed
