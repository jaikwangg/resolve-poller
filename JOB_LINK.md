# Job-Link — join conform-project ↔ color-project เป็น deliverable เดียว

> **ทำไมต้องมี:** conform กับ colorist ทำงาน **คนละ Resolve project/ไฟล์ คนละเครื่อง** และ **overlap** (color เริ่มได้ก่อน conform เสร็จ) → metric #1 ไม่ใช่เลขก้อนเดียว แต่เป็น **2 track ขนาน** ที่ต้องมี key ผูกเข้าด้วยกัน
>
> โครง: `job (deliverable)` ─┬─ project `*_conform`  → **conform_pct** (edit fill)
>                            └─ project `*_grade`    → **color_pct** (graded ratio)
>                            join ด้วย **`job_id`** เดียวกัน

---

## 1. การ resolve `job_id` + `role` (ต่อเครื่อง)

แต่ละ workstation poll project ของตัวเอง → resolve ว่า project นี้เป็นของ **job ไหน + role อะไร**
ลำดับความสำคัญ (ทำใน `joblink.resolve_job()`):

| อัน | job_id | role |
|---|---|---|
| 1. **marker** `@job <id>` / `@role <r>` ใน timeline | ✅ inline override | ✅ |
| 2. **registry** (`jobs.json`) | ✅ authoritative | ✅ |
| 3. **naming convention** (suffix ของชื่อ project) | ✅ เดา | ✅ เดา |
| 4. **fallback** | = ชื่อ project (unlinked, flag) | — |
| role ตัวสุดท้าย | — | **host role** (`EDITORTRACK_ROLE`) |

- 🚫 **worker zero-touch:** ทางหลัก = **registry (PM/admin) + host role (IT)** — worker ไม่ต้องทำอะไร. **marker/naming = optional override เท่านั้น** ไม่บังคับ worker ใส่. ไม่มี registry entry → fallback `unlinked` (โชว์เดี่ยว) ดีกว่าบังคับ worker ตั้งชื่อ/ใส่ marker
- **role ตั้งที่เครื่อง** ง่ายสุด (1 เครื่อง = 1 role): env `EDITORTRACK_ROLE=colorist` ใน LaunchAgent หรือ `~/.editortrack/role.json` (IT ตั้งครั้งเดียว)
- naming: suffix `_conform/_online/_edit` → conform · `_grade/_grading/_color/_cc` → colorist (strip แล้วได้ job_id)

## 2. Registry schema — `~/.editortrack/jobs.json`

job-centric (อ่าน/แก้ง่าย) — `joblink` จะ reverse เป็น project→job เอง:
```json
{
  "jobs": {
    "PROMO_Q4_30s": {
      "title": "PROMO Q4 30s",
      "target_duration_sec": 30,
      "projects": {
        "PROMO_Q4_conform": { "role": "conform" },
        "PROMO_Q4_grade":   { "role": "colorist" }
      }
    }
  }
}
```
> วางไว้ที่เดียว sync ให้ทุกเครื่อง (หรือ serve จาก server) — แต่ละเครื่องใช้แค่ entry ของ project ตัวเอง

**host role** — `~/.editortrack/role.json`:
```json
{ "role": "colorist" }
```

## 3. เพิ่มใน record (schema 2)

`runner.py` เรียก `joblink.resolve_job()` แล้วแนบ `job` block:
```python
from joblink import resolve_job
# ... หลังได้ snap แล้ว
snap = snapshot()
job = resolve_job(snap.get("project"), snap.get("job_hint"))   # job_hint จาก @job/@role marker (poller ดึงมา)
rec["job"] = job
rec["schema"] = 2
```
> **poller ต้องดึง `job_hint`** เพิ่ม (แบบเดียวกับ `scope_hint`): อ่าน marker `@job <id>` / `@role <r>` → `snap["job_hint"] = {"job":..,"role":..}`

record จะได้ block:
```json
"job": {"job_id":"PROMO_Q4_30s","role":"conform","resolved_by":"registry",
        "linked":true,"target_duration_sec":30,"title":"PROMO Q4 30s"}
```

## 4. Join ฝั่ง server — `build_jobs()` ใน `dashboard_api.py` (เพิ่มแล้ว)

- group record ตาม `job.job_id` → แยก slot ตาม role (`conform` / `color`)
- เก็บ record **ล่าสุด**ของแต่ละ slot
- **conform track** pct = `progress.phases.edit` (edit fill vs target)
- **color track** pct = `progress.phases.color` (graded ratio; ถ้า None = ยังไม่เปิด DETECT_GRADE → flag `color_unmeasured`)
- **deliver** = `max(render.max_completion)` ของทั้งสอง project
- **overall** = ถ่วงน้ำหนัก `conform .45 / color .40 / deliver .15` renormalize เฉพาะที่วัดได้ (overlap-aware: color เริ่มก่อน conform เสร็จได้ ไม่บังคับลำดับ)

endpoint ใหม่: **`GET /api/jobs`** คืน:
```json
{
  "generated_at": "...",
  "jobs": [{
    "job_id":"PROMO_Q4_30s", "title":"PROMO Q4 30s", "linked":true,
    "conform": {"project":"PROMO_Q4_conform","worker":"nok","page":"edit","pct":80,"active":true,"last_seen":"...","render_pct":0},
    "color":   {"project":"PROMO_Q4_grade","worker":"ploy","page":"color","pct":35,"active":true,"last_seen":"...","render_pct":0},
    "deliver_pct": 0, "overall_pct": 60.7, "measured":["color","conform"],
    "flags": []
  }]
}
```

## 5. flags (สถานะที่ dashboard ควรโชว์)

| flag | หมายความว่า |
|---|---|
| `unlinked` | resolve job_id ไม่ได้ (fallback) → ตั้ง registry/marker/ชื่อให้ถูก |
| `color_not_started` | มีแต่ conform ยังไม่มี record ฝั่ง color |
| `conform_missing` | มี color แต่ไม่มี conform (ผิดปกติ — เช็ค role tag) |
| `color_unmeasured` | color track วัดไม่ได้ (เปิด `DETECT_GRADE` หรือใช้ fallback time-share หน้า color) |

## 6. ความซื่อสัตย์ / เพดาน

- **track เป็นระดับ project ไม่ใช่ระดับ shot** — ยังไม่มีวิธี reconcile ว่า "shot A conform แล้ว + เกรดแล้ว" ข้าม 2 ไฟล์ (ชื่อ clip/ชื่อ shot อาจไม่ตรงกัน) → per-reel/shot granularity เป็น open question (§8 ใน HANDOFF)
- overall เป็น proxy ถ่วงน้ำหนัก — เหมือนเดิม **ห้ามผูกเงินเดือน/ตัดสินคน**
- ถ้าทีมไม่ตั้ง registry/naming ให้ดี → job แตกเป็นชิ้นเดี่ยว ๆ (`unlinked`) dashboard จะโชว์แยก

## 7. ไฟล์ที่เกี่ยว
- `joblink.py` — resolver (ตัวจริง, stdlib, เทสได้)
- `dashboard_api.py` — `build_jobs()` + `GET /api/jobs` (เพิ่มแล้ว)
- runner (ใน `TRACK_A.md`) — ต้องเพิ่มการเรียก `resolve_job` + `job_hint` จาก poller (ข้อ 3)
- dashboard UI — ยังต้องเพิ่มหน้าแสดง per-job 2-track (follow-up)
