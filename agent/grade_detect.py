#!/usr/bin/env python3
"""ตัดสินว่า clip "เกรดแล้ว" หรือยัง — แทนเกณฑ์เดิม node > 1 ที่นับเกินจริง
(เทสจริง 14 ต.ค.: 1/6 → 6/6 → 5/6 ในไม่กี่นาที เพราะ node เปล่า/copy grade/blade ก็นับ)

method (env EDITORTRACK_GRADE_METHOD):
  lut   (default) — TimelineItem.ExportLUT(17pt cube) แล้วเทียบกับ identity: สีเปลี่ยนจริง = เกรดแล้ว
                    node เปล่าไม่นับ · grade ใน node เดียวนับ · export ไม่ได้ → ใช้ nodes แทนทั้งรอบ
  nodes           — เกณฑ์เดิม: node > 1

LUT ไม่เห็น: window/blur/OFX ที่ไม่ใช่ color transform, grade ระดับ group/timeline (ต้อง probe ยืนยัน)
ExportLUT เขียนไฟล์ temp อย่างเดียว ไม่แก้ project · agent เป็น process ใหม่ทุกนาที → cache ผลไว้ที่
~/.editortrack/grade_cache.json · เช็คซ้ำเมื่อ: เป็น clip ที่กำลังเปิดอยู่ (ทุกรอบ), node/label/LUT/version
เปลี่ยน, หรือครบ RECHECK_SEC · จำกัด export ต่อรอบด้วย BUDGET_MS
"""
import json, os, pathlib, tempfile, time
from resolve_guard import ForbiddenCall, ScanAborted, GATED

# lut ต้องเปิด gate EDITORTRACK_ALLOW_EXPORT_LUT=1 ด้วย (ExportLUT ไม่ใช่ getter ล้วน — ดู resolve_guard.GATED)
# ไม่เปิด gate → ใช้ nodes เสมอ (fail closed) แม้ตั้ง METHOD=lut
METHOD = os.environ.get("EDITORTRACK_GRADE_METHOD", "lut").strip().lower()


def lut_allowed():
    return (os.environ.get(GATED["ExportLUT"][0]) or "").strip() == "1"

RECHECK_SEC = int(os.environ.get("EDITORTRACK_GRADE_RECHECK_SEC", "900"))
BUDGET_MS = int(os.environ.get("EDITORTRACK_GRADE_BUDGET_MS", "3000"))
EPS = float(os.environ.get("EDITORTRACK_GRADE_EPS", "0.004"))      # ~1/255 — ต่ำกว่านี้ถือว่าไม่ได้เปลี่ยนสี
MAX_TIMELINES = 8              # จำผลไว้กี่ timeline (สลับ project ไปมาแล้วไม่ต้อง export ใหม่ทั้งหมด)
def _cache_path():
    return pathlib.Path(os.environ.get("EDITORTRACK_STATE_DIR") or os.path.expanduser("~/.editortrack")) / "grade_cache.json"


def _safe(obj, method, *args):
    try:
        return getattr(obj, method)(*args)
    except (ForbiddenCall, ScanAborted):
        raise
    except Exception:
        return None


def cube_deviation(text):
    """max |LUT - identity| ของไฟล์ .cube (None = อ่านไม่ได้). ลำดับ .cube: R เปลี่ยนเร็วสุด"""
    size, rows = None, []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if ln.upper().startswith("LUT_3D_SIZE"):
            size = int(ln.split()[1])
            continue
        if ln[0].isalpha():                      # TITLE / DOMAIN_MIN / DOMAIN_MAX ...
            continue
        p = ln.split()
        if len(p) == 3:
            rows.append(tuple(float(x) for x in p))
    if not size or len(rows) != size ** 3:
        return None
    n1, dev = size - 1, 0.0
    for i, (r, g, b) in enumerate(rows):
        ir, ig, ib = i % size, (i // size) % size, i // (size * size)
        dev = max(dev, abs(r - ir / n1), abs(g - ig / n1), abs(b - ib / n1))
    return dev


def _signature(item):
    """ค่าถูก ๆ ที่เปลี่ยนเมื่อโครง grade เปลี่ยน (ไม่เห็นการหมุน wheel ใน node เดิม → ใช้ RECHECK_SEC + clip ปัจจุบัน)"""
    g = _safe(item, "GetNodeGraph")
    n = _safe(g, "GetNumNodes") if g is not None else None
    parts = [n]
    for i in range(1, (n or 0) + 1):
        parts += [_safe(g, "GetNodeLabel", i), _safe(g, "GetLUT", i), str(_safe(g, "GetToolsInNode", i))]
    parts.append(str(_safe(item, "GetCurrentVersion")))
    return json.dumps(parts, default=str), n


def _export_dev(resolve, item, tmpdir):
    kind = getattr(resolve, "EXPORT_LUT_17PTCUBE", None)
    if kind is None:
        return None
    path = os.path.join(tmpdir, "g.cube")
    try:
        if not item.ExportLUT(kind, path):
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return cube_deviation(f.read())
    except (ForbiddenCall, ScanAborted):
        raise
    except Exception:
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _load_cache():
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(c):
    try:
        cache = _cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(c), encoding="utf-8")
        tmp.replace(cache)
    except Exception:
        pass


class Detector:
    """ใช้ 1 ตัวต่อ 1 poll: graded(item) ต่อ clip แล้ว finish() คืนสรุป"""

    def __init__(self, resolve, timeline, method=None, key=None):
        self.resolve = resolve
        self.method = (method or METHOD)
        self.gated = self.method == "lut" and not lut_allowed()
        if self.gated:
            self.method = "nodes"                         # ExportLUT ยังไม่ได้รับอนุญาต → nodes
        self.cur = _safe(_safe(timeline, "GetCurrentVideoItem"), "GetUniqueId") if self.method == "lut" else None
        self.key = key or str(_safe(timeline, "GetUniqueId") or _safe(timeline, "GetName"))
        self.all = _load_cache() if self.method == "lut" else {}     # timeline key -> {uid: ผล}
        self.cache = self.all.pop(self.key, {})
        self.seen = set()
        self.t0 = time.time()
        self.exported = self.pending = self.failed = 0
        self.tmp = tempfile.mkdtemp(prefix="et-grade-") if self.method == "lut" else None

    def _nodes(self, n):
        return bool(n and n > 1)

    def graded(self, item):
        if self.method != "lut":                           # 2 calls ต่อ clip เท่านั้น
            g = _safe(item, "GetNodeGraph")
            return self._nodes(_safe(g, "GetNumNodes") if g is not None else None)
        sig, n = _signature(item)
        uid = _safe(item, "GetUniqueId") or f"{_safe(item, 'GetStart')}:{_safe(item, 'GetName')}"
        self.seen.add(uid)
        c = self.cache.get(uid)
        now = time.time()
        fresh = c and c.get("sig") == sig and now - c.get("at", 0) < RECHECK_SEC and uid != self.cur
        if fresh:
            return c["graded"]
        if (time.time() - self.t0) * 1000 > BUDGET_MS and uid != self.cur:
            self.pending += 1                             # หมดงบรอบนี้ → ใช้ผลเก่า (ถ้ามี) รอบหน้าค่อยเช็ค
            return c["graded"] if c else False
        dev = _export_dev(self.resolve, item, self.tmp)
        if dev is None:                                    # export ไม่ได้ → ใช้ nodes สำหรับ clip นี้
            self.failed += 1
            return self._nodes(n)
        self.exported += 1
        g = dev > EPS
        self.cache[uid] = {"sig": sig, "at": now, "graded": g, "dev": round(dev, 4)}
        return g

    def finish(self):
        method = self.method
        if self.method == "lut":
            for k in [k for k in self.cache if k not in self.seen]:   # clip ที่ไม่อยู่ใน timeline แล้ว
                del self.cache[k]
            keep = dict(list(self.all.items())[-(MAX_TIMELINES - 1):]) if MAX_TIMELINES > 1 else {}
            keep[self.key] = self.cache                            # timeline ล่าสุดอยู่ท้าย
            _save_cache(keep)
            try:
                os.rmdir(self.tmp)
            except OSError:
                pass
            if self.failed and not self.exported:
                method = "nodes"                           # ExportLUT ใช้ไม่ได้ทั้งรอบ
        return {"method": method, "lut_gated": self.gated, "exported": self.exported, "pending": self.pending,
                "failed": self.failed, "ms": int((time.time() - self.t0) * 1000)}
