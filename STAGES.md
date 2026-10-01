# Stages — pipeline 5 stage (pluggable collector)

ระบบรองรับ 5 stage · backbone เดียวกัน (activity + ERP job-link + POST) เปลี่ยนแค่ **collector** (ตัวอ่าน progress)

| stage | ทำใน | collector | อ่าน progress จาก | pct |
|---|---|---|---|---|
| **data** | tool DIT (ไม่ใช่ Resolve) | `fs_collector` | นับไฟล์ที่ ingest ในโฟลเดอร์ | files / target_count |
| **conform** | Resolve | `resolve_poller`+`scope` | duration + shots (marker) | avg(duration/target, shots/expected) |
| **color** | Resolve | `resolve_poller`+`scope` | graded clips (node>1) | graded / total |
| **subtitle** | tool ซับ (ไม่ใช่ Resolve) | `subtitle_collector` | นับ cue ในไฟล์ .srt/.vtt | cues / target_cues |
| **master** | tool master (ไม่ใช่ Resolve) | `fs_collector` | นับไฟล์ master ที่ผลิต | files / target_count |

> conform/color ทำใน Resolve (อ่าน API) · data/subtitle/master **ไม่ใช่ Resolve** → อ่านจาก **ไฟล์ที่ผลิต** (ไม่พึ่ง API ของ tool)

## Goal (ตัวหาร / 100% ของแต่ละ stage) — **ERP ไม่มี เราออกแบบเอง**
| stage | goal (100%) | ที่มา |
|---|---|---|
| **color** | graded / total clips | **auto** จากไทม์ไลน์ (self-defined) |
| **conform** | duration / **offline edit** | **auto** — อ่านความยาว offline/editorial timeline (`_offline_target`, env `EDITORTRACK_OFFLINE_MATCH`) · ⚠️ ต้อง validate ชื่อ/โครงสร้าง offline timeline บนโปรเจคจริง (ไม่เจอ → ค่าดิบ) |
| **subtitle** | — | **ค่าดิบ** (นับ cue ไม่ทำ %) |
| **master** | — | **ค่าดิบ** (นับไฟล์ ไม่ทำ %) |
| **data** | — | ⏸️ **pause** (detail งานยังไม่ชัด) |
> ค่าดิบ = รายงาน count/ความเคลื่อนไหว โดยไม่มี % (ยังมีประโยชน์ดูว่าขยับ) · เปิด % ภายหลังได้เมื่อมีแหล่ง goal

## หลักการ
- **1 workstation = 1 stage** · ตั้ง `EDITORTRACK_STAGE` ต่อเครื่อง
- ทุก stage ส่ง record **schema 3** รูปเดียวกัน → server รวมต่อ job เป็น N track
- job + stage มาจาก **ERP booking** (room+time → job + activity→stage) · fallback = agent-side

## record schema 3
```json
{ "schema":3, "ts":"<UTC>", "host":"grade-03", "user":"ploy", "stage":"color",
  "activity":{ "idle_sec":3.2, "active":true, "resolve_frontmost":true },
  "source":{ ...raw ของ collector (resolve snap / files / cues)... },
  "job":{ "job_id":"KPP-2026-5871", "role":null, "linked":false },
  "progress":{ "stage":"color", "pct":60.0, "done":null, "total":null, "detail":{...} },
  "page":"color" }
```

## รัน agent ต่อ stage (env)
```sh
export EDITORTRACK_STAGE=<data|conform|color|subtitle|master>
export EDITORTRACK_SERVER=http://SERVER:8000/ingest   EDITORTRACK_TOKEN=...

# data / master (fs_collector):
export EDITORTRACK_WATCH_DIR=/path/ingest   EDITORTRACK_WATCH_GLOB="*.mxf"
export EDITORTRACK_TARGET_COUNT=120         # หรือ EDITORTRACK_TARGET_SIZE_GB

# subtitle:
export EDITORTRACK_SUBTITLE_PATH=/path/subs   EDITORTRACK_TARGET_CUES=600

# conform / color: ตั้ง RESOLVE_SCRIPT_* (ดู DATAFLOW.md) + EDITORTRACK_DETECT_GRADE (color=1, conform=0)

python runner.py
```

## ERP activity → stage (server, `erp.py` STAGE_MAP)
`Grading→color` · `Conform/Edit/Online→conform` · `Data/DIT/Ingest→data` · `Subtitle/Caption→subtitle` · `Mastering→master`
(ปรับ map ได้ถ้า ERP ใช้ชื่อ activity อื่น)

## dashboard
`/api/jobs` → ต่อ job แสดง **N track ตามลำดับ** `data → conform → color → subtitle → master` + overall (เฉลี่ย stage ที่วัดได้)
`/api/shots` → reconcile ราย shot (conform vs color)

## เพิ่ม stage ใหม่ภายหลัง
1. เขียน collector คืน normalized `{ok, stage, project, pct, done, total, detail, raw}`
2. เพิ่มใน `agent/collect.py` (STAGES + dispatch)
3. เพิ่มใน `server/dashboard_api.py STAGES` + `erp.py STAGE_MAP` + `dashboard.html STAGE_LABEL/STAGE_COLOR`
