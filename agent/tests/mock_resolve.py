"""Mock DaVinci Resolve scripting objects for safety tests.

Every method call on any mock object is appended to `world.calls` (name) BEFORE it runs,
including names the mock does not implement — so a test can prove the agent never sent a
forbidden name to "Resolve" at all, not just that it failed. Mutating methods that matter
for the hazards (OpenPage, GetRenderJobList's real page jump, SetCurrentTimecode, ...)
change `world` state so tests can observe the effect.
"""
import os, time


class World:
    def __init__(self, n_clips=8, tracks=1, page="color", version="20.2.1.5",
                 product="DaVinci Resolve Studio", rendering=False, playing=False,
                 latency_s=0.0, nodes=2, markers=None, project="DUMMY_PROJECT_color",
                 timelines=("Main_Grade", "Offline_Ref"), no_project=False):
        self.calls = []
        self.page = page
        self.version = version
        self.product = product
        self.rendering = rendering
        self.playing = playing
        self.latency_s = latency_s
        self.faults = {}            # name -> exception instance | "none"
        self.fault_at = None        # (index, exc) — raise on the Nth call overall
        self.on_call = None         # hook(world, name) — e.g. simulate the user switching page
        self.tc_frame = 86400
        self.no_project = no_project
        self.current_timeline = 1
        per = max(1, n_clips // tracks)
        self.tracks = []
        start = 86400
        made = 0
        for t in range(tracks):
            items = []
            cnt = per if t < tracks - 1 else n_clips - made
            for k in range(cnt):
                items.append(Item(self, "clip_%d_%d" % (t, k), start, start + 48, nodes if k % 2 == 0 else 1))
                start += 48
                made += 1
            self.tracks.append(items)
        self.markers = markers if markers is not None else {
            0: {"name": "SH010", "note": "@job DUMMYJOB", "color": "Blue"},
            96: {"name": "SH020", "note": "", "color": "Blue"}}
        self.timeline_names = list(timelines)
        self.project_name = project

    def record(self, name):
        self.calls.append(name)
        if self.on_call:
            self.on_call(self, name)
        if self.fault_at and len(self.calls) == self.fault_at[0]:
            raise self.fault_at[1]
        f = self.faults.get(name)
        if isinstance(f, Exception):
            raise f
        if self.latency_s:
            end = time.perf_counter() + self.latency_s
            while time.perf_counter() < end:
                pass
        return f


class Base:
    def __init__(self, world):
        self._w = world

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        w = self.__dict__["_w"]

        def unknown(*a):
            w.record(name)
            return None
        return unknown

    def _r(self, name, value):
        f = self._w.record(name)
        return None if f == "none" else value


class Resolve(Base):
    EXPORT_LUT_17PTCUBE = "17pt"

    def GetProjectManager(self): return self._r("GetProjectManager", PM(self._w))
    def GetCurrentPage(self): return self._r("GetCurrentPage", self._w.page)
    def GetProductName(self): return self._r("GetProductName", self._w.product)
    def GetVersionString(self): return self._r("GetVersionString", self._w.version)

    def OpenPage(self, p):
        self._r("OpenPage", None)
        self._w.page = p
        return True


class PM(Base):
    def GetCurrentProject(self):
        return self._r("GetCurrentProject", None if self._w.no_project else Project(self._w))


class Project(Base):
    def GetName(self): return self._r("GetName", self._w.project_name)
    def GetTimelineCount(self): return self._r("GetTimelineCount", len(self._w.timeline_names))
    def GetTimelineByIndex(self, i): return self._r("GetTimelineByIndex", Timeline(self._w, i))
    def GetCurrentTimeline(self): return self._r("GetCurrentTimeline", Timeline(self._w, self._w.current_timeline))
    def IsRenderingInProgress(self): return self._r("IsRenderingInProgress", self._w.rendering)
    def GetSetting(self, k=None): return self._r("GetSetting", "24")

    def GetRenderJobList(self):            # the real incident: page jumps to Deliver
        self._r("GetRenderJobList", None)
        self._w.page = "deliver"
        return []

    def SetCurrentTimeline(self, tl):
        self._r("SetCurrentTimeline", None)
        self._w.current_timeline = tl._i
        return True


class Timeline(Base):
    def __init__(self, world, i):
        super().__init__(world)
        self._i = i

    def GetName(self): return self._r("GetName", self._w.timeline_names[self._i - 1])
    def GetUniqueId(self): return self._r("GetUniqueId", "tl-%d" % self._i)
    def GetStartFrame(self): return self._r("GetStartFrame", 86400)
    def GetEndFrame(self): return self._r("GetEndFrame", 86400 + 48 * sum(len(t) for t in self._w.tracks))
    def GetTrackCount(self, kind): return self._r("GetTrackCount", len(self._w.tracks) if kind == "video" else 2)
    def GetItemListInTrack(self, kind, i): return self._r("GetItemListInTrack", list(self._w.tracks[i - 1]) if kind == "video" else [])
    def GetMarkers(self): return self._r("GetMarkers", dict(self._w.markers))
    def GetCurrentVideoItem(self): return self._r("GetCurrentVideoItem", self._w.tracks[0][0] if self._w.tracks and self._w.tracks[0] else None)

    def GetCurrentTimecode(self):
        v = self._r("GetCurrentTimecode", "01:00:00:%02d" % (self._w.tc_frame % 24))
        if self._w.playing:
            self._w.tc_frame += 6
        return v

    def SetCurrentTimecode(self, tc):
        self._r("SetCurrentTimecode", None)
        return True


class Item(Base):
    def __init__(self, world, name, s, e, nodes):
        super().__init__(world)
        self._name, self._s, self._e, self._n = name, s, e, nodes

    def GetName(self): return self._r("GetName", self._name)
    def GetUniqueId(self): return self._r("GetUniqueId", "uid-" + self._name)
    def GetStart(self): return self._r("GetStart", self._s)
    def GetEnd(self): return self._r("GetEnd", self._e)
    def GetDuration(self): return self._r("GetDuration", self._e - self._s)
    def GetNodeGraph(self): return self._r("GetNodeGraph", Graph(self._w, self._n))
    def GetCurrentVersion(self): return self._r("GetCurrentVersion", {"versionName": "Version 1"})
    def GetMediaPoolItem(self): return self._r("GetMediaPoolItem", MediaPoolItem(self._w))
    def GetColorGroup(self): return self._r("GetColorGroup", None)
    def GetVersionNameList(self, t): return self._r("GetVersionNameList", ["Version 1"])

    def ExportLUT(self, kind, path):
        self._r("ExportLUT", None)
        size = 17
        with open(path, "w") as f:
            f.write("LUT_3D_SIZE 17\n")
            off = 0.1 if self._n > 1 else 0.0
            for i in range(size ** 3):
                r, g, b = i % size, (i // size) % size, i // (size * size)
                f.write("%f %f %f\n" % (min(1, r / 16 + off), g / 16, b / 16))
        return True


class Graph(Base):
    def __init__(self, world, n):
        super().__init__(world)
        self._n = n

    def GetNumNodes(self): return self._r("GetNumNodes", self._n)
    def GetNodeLabel(self, i): return self._r("GetNodeLabel", "")
    def GetLUT(self, i): return self._r("GetLUT", "")
    def GetToolsInNode(self, i): return self._r("GetToolsInNode", [])


class MediaPoolItem(Base):
    def GetClipProperty(self, k=None): return self._r("GetClipProperty", {"FPS": "24", "File Path": "/client/A001.mov"})
    def GetMetadata(self, k=None): return self._r("GetMetadata", {})


def loader(world):
    """resolve_poller.snapshot(load=...) factory: returns the guarded mock like _load_resolve."""
    from resolve_guard import guard

    def load(ctx=None):
        from resolve_guard import Ctx
        return guard(Resolve(world), ctx if ctx is not None else Ctx())
    return load
