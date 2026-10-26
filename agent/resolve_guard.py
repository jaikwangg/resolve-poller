#!/usr/bin/env python3
"""resolve_guard — the only way agent code reaches DaVinci Resolve.

Zero-incident rule: the agent may call a Resolve scripting method only if its name is on
an explicit allowlist below. Everything else is refused *before* the remote object is
touched (default deny). A "Get" prefix is NOT evidence of safety: Project.GetRenderJobList()
switched a real workstation to the Deliver page (incident, Oct 2026).

How it is enforced
  * `guard(raw_resolve, ctx)` wraps the object returned by DaVinciResolveScript.scriptapp().
    Every attribute access on the wrapper goes through `Guarded.__getattr__`; every object a
    call returns (Project, Timeline, TimelineItem, Graph, ...) is wrapped again, so there is no
    path from agent code to an unwrapped Resolve object.
  * A refused name raises ForbiddenCall and is recorded in ctx.violations; the runner trips
    the circuit breaker on any violation (it means our code is wrong -> stop touching Resolve).
  * Each call checks the scan budget (wall time + call count). Over budget -> ScanAborted,
    the poll is dropped, nothing partial is sent.
  * tests/test_api_allowlist.py parses every agent module and fails if a method name used
    against Resolve is not on ALLOWED_CORE/ALLOWED_EXTENDED/GATED, so a new call cannot be
    added without editing this file (and the review that goes with it).

Tiers
  ALLOWED_CORE      needed by the default poll. Pure getters per the Resolve README.
  ALLOWED_EXTENDED  only for EDITORTRACK_EXTRACT_CLIP_META=1 (off by default).
  GATED             not a pure getter; allowed only when its env gate is "1" AND it has been
                    verified on a real dummy project (WORKSTATION_SAFETY.md phase 3).
  DENIED            documented dangerous names; listed so reviews/tests can name the reason.
                    Being absent from the allowlist is already enough to refuse a call.
"""
import os
import time

# Status column: "observed" = called on conform-01 (Mac, Oct 7-14 test) without a page/selection
# change being reported; "unverified" = expected to be a pure getter, must pass the phase-3 check.
ALLOWED_CORE = {
    # Resolve
    "GetProjectManager": "observed",
    "GetCurrentPage": "observed",
    "GetProductName": "unverified",
    "GetVersionString": "unverified",
    # ProjectManager
    "GetCurrentProject": "observed",
    # Project
    "GetName": "observed",               # Project / Timeline / TimelineItem / ColorGroup
    "GetUniqueId": "observed",           # Timeline / TimelineItem
    "GetTimelineCount": "observed",
    "GetTimelineByIndex": "observed",    # returns a Timeline object; does NOT change current timeline
    "GetCurrentTimeline": "observed",
    "IsRenderingInProgress": "observed",
    "GetSetting": "observed",            # read with a key only ("timelineFrameRate")
    # Timeline
    "GetStartFrame": "observed",
    "GetEndFrame": "observed",
    "GetTrackCount": "observed",
    "GetItemListInTrack": "observed",
    "GetMarkers": "observed",
    "GetCurrentVideoItem": "unverified",
    "GetCurrentTimecode": "unverified",  # playback detection (two reads ~250 ms apart)
    # TimelineItem
    "GetStart": "observed",
    "GetEnd": "observed",
    "GetNodeGraph": "observed",
    "GetCurrentVersion": "unverified",
    # Graph
    "GetNumNodes": "observed",
    "GetNodeLabel": "unverified",
    "GetLUT": "unverified",
    "GetToolsInNode": "unverified",
}

ALLOWED_EXTENDED = {   # EXTRACT_CLIP_META only
    "GetDuration": "unverified",
    "GetLeftOffset": "unverified",
    "GetRightOffset": "unverified",
    "GetClipColor": "unverified",
    "GetFlagList": "unverified",
    "GetMediaPoolItem": "unverified",
    "GetClipProperty": "unverified",
    "GetMetadata": "unverified",
    "GetVersionNameList": "unverified",
    "GetColorGroup": "unverified",
    "GetFusionCompCount": "unverified",
}

# name -> (env gate, why it is not a plain getter)
GATED = {
    "ExportLUT": ("EDITORTRACK_ALLOW_EXPORT_LUT",
                  "renders the clip's grade into a .cube file: Resolve-side GPU/CPU work on its "
                  "main thread and a file write; effect on UI/undo/cache not yet verified"),
}

# Plain attributes (constants), never called.
CONSTANTS = {"EXPORT_LUT_17PTCUBE"}

# Known dangerous / state-changing names. Refused anyway (default deny); kept for reasons + tests.
DENIED = {
    "GetRenderJobList": "switched a real workstation to the Deliver page (incident Oct 2026)",
    "GetRenderJobStatus": "same render-queue path as GetRenderJobList; page jump",
    "GetCurrentClipThumbnailImage": "renders a frame of client footage (privacy + GPU work); Track B only after consent",
    "OpenPage": "changes the page the editor is on (was used by RESTORE_PAGE; removed)",
    "SetCurrentTimeline": "changes the editor's current timeline",
    "SetCurrentTimecode": "moves the playhead",
    "LoadProject": "switches/loads a project", "CloseProject": "closes the project",
    "SaveProject": "writes the project", "CreateProject": "creates a project",
    "DeleteProject": "deletes a project", "ImportProject": "imports a project",
    "ExportProject": "exports the project (disk I/O, may lock)", "ArchiveProject": "archive job",
    "RestoreProject": "restores a project", "SetCurrentDatabase": "switches database",
    "GotoRootFolder": "changes project manager folder", "GotoParentFolder": "changes folder",
    "OpenFolder": "changes folder", "SetCurrentFolder": "changes media pool folder",
    "AddRenderJob": "render queue", "DeleteRenderJob": "render queue",
    "DeleteAllRenderJobs": "render queue", "StartRendering": "render", "StopRendering": "render",
    "LoadRenderPreset": "render settings", "SetRenderSettings": "render settings",
    "SetCurrentRenderFormatAndCodec": "render settings", "SetCurrentRenderMode": "render settings",
    "SetSetting": "project/timeline settings", "SetName": "renames",
    "AddMarker": "edits markers", "DeleteMarkerAtFrame": "edits markers",
    "DeleteMarkersByColor": "edits markers", "UpdateMarkerCustomData": "edits markers",
    "SetClipColor": "edits clip", "AddFlag": "edits clip", "ClearFlags": "edits clip",
    "SetProperty": "edits clip", "SetClipProperty": "edits media", "SetMetadata": "edits media",
    "SetLUT": "edits grade", "SetCDL": "edits grade", "ApplyGradeFromDRX": "edits grade",
    "AddVersion": "edits grade", "LoadVersionByName": "changes active grade version",
    "DeleteVersionByName": "edits grade", "RefreshLUTList": "rescans LUT folder",
    "GrabStill": "writes gallery", "GrabAllStills": "writes gallery", "ExportStills": "writes files",
    "ExportCurrentFrameAsStill": "renders + writes file", "Export": "timeline export (writes file)",
    "ImportTimelineFromFile": "imports", "AppendToTimeline": "edits timeline",
    "CreateTimelineFromClips": "edits project", "CreateEmptyTimeline": "edits project",
    "DeleteTimelines": "edits project", "DuplicateTimeline": "edits project",
    "DeleteClips": "edits media pool", "MoveClips": "edits media pool",
    "ImportMedia": "edits media pool", "AddItemListToMediaPool": "edits media pool",
    "LoadLayoutPreset": "changes UI layout", "SaveLayoutPreset": "writes prefs",
    "UpdateLayoutPreset": "writes prefs", "ImportLayoutPreset": "writes prefs",
    "ExportLayoutPreset": "writes file", "DeleteLayoutPreset": "writes prefs",
    "Quit": "quits Resolve", "Fusion": "hands out the Fusion scripting object (unbounded API)",
    "GetMediaStorage": "not needed; file-system browsing object",
    "InsertGeneratorIntoTimeline": "edits timeline", "InsertTitleIntoTimeline": "edits timeline",
    "InsertFusionGeneratorIntoTimeline": "edits timeline", "CreateCompoundClip": "edits timeline",
    "CreateFusionClip": "edits timeline", "ImportIntoTimeline": "edits timeline",
    "SetTrackEnable": "edits timeline", "SetTrackLock": "edits timeline", "SetTrackName": "edits timeline",
    "AddTrack": "edits timeline", "DeleteTrack": "edits timeline", "DeleteClipsFromTimeline": "edits timeline",
    "SetNodeEnabled": "edits grade", "ResetAllGrades": "edits grade", "SetNodeCacheMode": "edits grade",
    "AssignToColorGroup": "edits grade", "RemoveFromColorGroup": "edits grade",
    "CopyGrades": "edits grade", "AddFusionComp": "edits clip", "ImportFusionComp": "edits clip",
}

# Names only probe.py may use (manual diagnostic on a dummy project, never scheduled).
PROBE_EXTRA = {"GetCurrentRenderMode", "GetTrackName", "GetSourceStartFrame", "GetSourceEndFrame",
               "GetProperty", "GetNodeCacheMode", "GetFusionCompNameList", "GetCurrentVersionName"}
PROBE_UNSAFE = {"GetRenderJobList", "GetRenderJobStatus", "OpenPage"}   # only with --unsafe-render


class ForbiddenCall(Exception):
    pass


class ScanAborted(Exception):
    pass


def _flag(name):
    return (os.environ.get(name) or "").strip() == "1"


def allowed_names(extended=None, probe=False, probe_unsafe=False):
    """Set of method names the guard lets through under the current env."""
    if extended is None:
        extended = _flag("EDITORTRACK_EXTRACT_CLIP_META")
    names = set(ALLOWED_CORE)
    if extended:
        names |= set(ALLOWED_EXTENDED)
    names |= {n for n, (gate, _) in GATED.items() if _flag(gate)}
    if probe:
        names |= set(ALLOWED_EXTENDED) | PROBE_EXTRA
    if probe_unsafe:
        names |= PROBE_UNSAFE
    return names


class Ctx:
    """Per-poll guard context: allowlist, budget, call log, violations."""

    def __init__(self, deadline_ms=None, max_calls=None, names=None, clock=time.monotonic):
        self.names = allowed_names() if names is None else set(names)
        self.clock = clock
        self.t0 = clock()
        self.deadline_ms = deadline_ms
        self.max_calls = max_calls
        self.calls = 0
        self.by_name = {}
        self.violations = []
        self.aborted = None

    def elapsed_ms(self):
        return (self.clock() - self.t0) * 1000.0

    def check(self, name):
        if name not in self.names:
            self.violations.append(name)
            raise ForbiddenCall(name)
        if self.max_calls is not None and self.calls >= self.max_calls:
            self.aborted = self.aborted or "max_calls"
            raise ScanAborted("max_calls")
        if self.deadline_ms is not None and self.elapsed_ms() > self.deadline_ms:
            self.aborted = self.aborted or "deadline"
            raise ScanAborted("deadline")
        self.calls += 1
        self.by_name[name] = self.by_name.get(name, 0) + 1


_PRIMS = (type(None), bool, int, float, str, bytes)


class Guarded:
    __slots__ = ("_o", "_c")

    def __init__(self, obj, ctx):
        object.__setattr__(self, "_o", obj)
        object.__setattr__(self, "_c", ctx)

    def __getattr__(self, name):
        ctx = object.__getattribute__(self, "_c")
        obj = object.__getattribute__(self, "_o")
        if name in CONSTANTS:
            return getattr(obj, name, None)
        if name.startswith("__"):
            raise AttributeError(name)
        if name not in ctx.names:               # refuse before touching the remote object
            ctx.violations.append(name)
            raise ForbiddenCall(name)

        def call(*args):
            ctx.check(name)
            return wrap(getattr(obj, name)(*args), ctx)
        return call

    def __setattr__(self, name, value):
        raise ForbiddenCall("setattr:" + name)

    def __repr__(self):
        return "<Guarded %s>" % type(object.__getattribute__(self, "_o")).__name__

    def __bool__(self):
        return True


def wrap(value, ctx):
    if isinstance(value, _PRIMS) or isinstance(value, Guarded):
        return value
    if isinstance(value, list):
        return [wrap(v, ctx) for v in value]
    if isinstance(value, tuple):
        return tuple(wrap(v, ctx) for v in value)
    if isinstance(value, dict):
        return {k: wrap(v, ctx) for k, v in value.items()}
    return Guarded(value, ctx)


def guard(raw, ctx):
    return None if raw is None else Guarded(raw, ctx)
