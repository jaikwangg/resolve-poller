"""Run ONE agent turn (runner.main) in a fresh process against the mock Resolve and print a JSON
report: every file-system write the process made (via sys.addaudithook), wall time, peak RSS,
guard call counts. Used by test_resource_budget (measurement) and test_no_foreign_writes.

usage: python3 run_turn.py <server_url> <n_clips> <tracks> <latency_us> [scan_only]
env: EDITORTRACK_* as for the agent (STATE_DIR must be set by the caller)
"""
import json, os, sys, time, tracemalloc

WRITES = []


def hook(event, args):
    try:
        if event == "open":
            path, mode, flags = args
            if isinstance(path, (str, bytes)) and ((mode and any(c in mode for c in "wax+")) or
                                                    (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))):
                WRITES.append(("open", os.fsdecode(path)))
        elif event in ("os.rename", "os.replace"):
            WRITES.append((event, os.fsdecode(args[0])))
            WRITES.append((event, os.fsdecode(args[1])))
        elif event in ("os.remove", "os.mkdir", "os.rmdir", "os.chmod", "os.truncate", "shutil.rmtree"):
            if isinstance(args[0], (str, bytes, os.PathLike)):
                WRITES.append((event, os.fsdecode(args[0])))
    except Exception:
        pass


def main():
    server, n_clips, tracks, lat_us = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
    scan_only = len(sys.argv) > 5 and sys.argv[5] == "scan_only"
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(here))
    sys.path.insert(0, os.path.dirname(os.path.dirname(here)))
    os.environ["EDITORTRACK_SERVER"] = server + "/ingest"
    from tests.mock_resolve import World, loader
    import resolve_poller, runner
    w = World(n_clips=n_clips, tracks=tracks, latency_s=lat_us / 1e6)
    resolve_poller._load_resolve = loader(w)
    runner.sample = lambda: {"idle_sec": 30, "frontmost_name": "DaVinci Resolve", "resolve_frontmost": True}
    sys.addaudithook(hook)
    tracemalloc.start()
    t0 = time.perf_counter()
    if scan_only:
        snap = resolve_poller.snapshot(idle_fn=lambda: 30, sleep=lambda s: None)
        status = None
    else:
        status = runner.main()
        snap = None
    wall = (time.perf_counter() - t0) * 1000
    _, peak = tracemalloc.get_traced_memory()
    import safety
    rss = safety.peak_rss_mb()          # handles macOS bytes vs Linux KiB
    g = (snap or {}).get("guard") or {}
    tl = (snap or {}).get("timeline") or {}
    print(json.dumps({"status": status, "wall_ms": round(wall, 1), "py_heap_peak_mb": round(peak / 2**20, 2),
                      "maxrss_mb": round(rss, 1), "mock_calls": len(w.calls), "guard": g,
                      "reason": (snap or {}).get("reason"), "per_clip": tl.get("per_clip"),
                      "video_clips": tl.get("video_clips"), "writes": WRITES, "page": w.page}))


if __name__ == "__main__":
    main()
