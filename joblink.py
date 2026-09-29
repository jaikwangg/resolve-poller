#!/usr/bin/env python3
"""
joblink.py — resolve job_id + role ต่อ project เพื่อ "join" conform-project กับ color-project
เป็น deliverable เดียว (เพราะ 2 ฝ่ายอยู่คนละไฟล์ + overlap)

ลำดับการ resolve job_id:  marker @job  >  registry  >  naming convention  >  fallback(unlinked)
ลำดับการ resolve role:    marker @role >  registry  >  host role (env/ไฟล์)

stdlib อย่างเดียว → test ได้โดยไม่ต้องมี Resolve

env:
  EDITORTRACK_JOBS       path ของ registry (default ~/.editortrack/jobs.json)
  EDITORTRACK_ROLE       role ของเครื่องนี้ (conform|colorist) — วิธี tag role ที่ง่ายสุด
  EDITORTRACK_ROLE_FILE  หรือเก็บใน ~/.editortrack/role.json = {"role":"colorist"}
"""
import os, json, re

REG_PATH = os.environ.get("EDITORTRACK_JOBS", os.path.expanduser("~/.editortrack/jobs.json"))
ROLE_PATH = os.environ.get("EDITORTRACK_ROLE_FILE", os.path.expanduser("~/.editortrack/role.json"))

# suffix ของชื่อ project → เดา job_id + role (naming convention)
_SUFFIX = re.compile(r"[_-](conform|online|edit|editorial|grade|grading|color|colour|cc)$", re.I)
_SUFFIX_ROLE = {"conform": "conform", "online": "conform", "edit": "conform", "editorial": "conform",
                "grade": "colorist", "grading": "colorist", "color": "colorist", "colour": "colorist", "cc": "colorist"}


def _load_registry():
    try:
        with open(REG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"jobs": {}}


def _reverse(reg):
    """project_name -> (job_id, role, job_meta)"""
    rev = {}
    for jid, j in (reg.get("jobs") or {}).items():
        for pname, pmeta in (j.get("projects") or {}).items():
            rev[pname] = (jid, (pmeta or {}).get("role"), j)
    return rev


def _host_role():
    r = os.environ.get("EDITORTRACK_ROLE")
    if r:
        return r
    try:
        with open(ROLE_PATH, encoding="utf-8") as f:
            return json.load(f).get("role")
    except Exception:
        return None


def resolve_job(project_name, marker_hint=None):
    """
    marker_hint = {"job": <id>, "role": <r>} ที่ poller ดึงจาก @job/@role marker
    คืน dict: job_id, role, resolved_by, linked, target_duration_sec, title
    """
    reg = _load_registry()
    rev = _reverse(reg)
    marker_hint = marker_hint or {}

    job_id = marker_hint.get("job")
    role = marker_hint.get("role")
    resolved_by = "marker" if job_id else None
    meta = {}

    # registry: เอา job_id ถ้ายังไม่มี + เอา meta/role เสมอถ้า project อยู่ใน registry
    if project_name in rev:
        rjid, rrole, meta = rev[project_name]
        if job_id is None:
            job_id, resolved_by = rjid, "registry"
        role = role or rrole

    # naming convention
    if job_id is None:
        mnm = _SUFFIX.search(project_name or "")
        if mnm:
            job_id = _SUFFIX.sub("", project_name)
            role = role or _SUFFIX_ROLE.get(mnm.group(1).lower())
            resolved_by = "naming"

    linked = job_id is not None
    if job_id is None:
        job_id, resolved_by = (project_name or "unknown"), "fallback"

    role = role or _host_role()   # host role = default สุดท้าย (1 เครื่อง = 1 role)

    return {
        "job_id": job_id,
        "role": role,                     # conform | colorist | None
        "resolved_by": resolved_by,       # marker | registry | naming | fallback
        "linked": linked,                 # False = ยังไม่ผูกเป็น job (dashboard โชว์เดี่ยว + flag)
        "target_duration_sec": (meta or {}).get("target_duration_sec"),
        "title": (meta or {}).get("title", job_id),
    }


if __name__ == "__main__":
    import sys
    pn = sys.argv[1] if len(sys.argv) > 1 else "PROMO_Q4_conform"
    print(json.dumps(resolve_job(pn), ensure_ascii=False, indent=2))
