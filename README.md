# resolve-poller (editortrack)

ระบบติดตาม **ความคืบหน้า + เวลาทำงานจริง** ของทีมตัดต่อ **DaVinci Resolve** ระดับองค์กร แบบ DIY + local LLM
เก็บ 2 metric: **#1** % ความคืบหน้า (conform + color, proxy) · **#2** เวลา active-in-Resolve

> **เริ่มอ่านที่ [`HANDOFF.md`](HANDOFF.md)** — มี context ครบ: ภารกิจ, กฎเหล็ก, สถาปัตยกรรม, สถานะ (verified/ยัง), schema, next steps

## กฎเหล็ก (ย่อ)
- **เปิดเผย (worker รู้) + worker zero-touch** — worker ไม่ต้องทำ/ใส่อะไรเพื่อ tracking
- **% เป็น proxy/advisory เท่านั้น — ห้ามผูกเงินเดือน/ตัดสินคน**
- **creds ทั้งหมดผ่าน env — ห้าม commit** (ดู `.gitignore`)
- ไม่ใช่คำแนะนำกฎหมาย — ต้องผ่านทนาย (PDPA) ก่อน deploy

## ไฟล์
| ไฟล์ | คือ |
|---|---|
| `HANDOFF.md` | **entry point** — อ่านก่อน |
| `TRACK_A.md` / `API_REFERENCE.md` / `JOB_LINK.md` | สเปค + reference API + กลไก job-link |
| `probe.py` | introspect Resolve จริง (รันก่อนเป็น gate) |
| `resolve_poller.py` · `activity_sampler.py` · `scope.py` · `runner.py` | agent (A1/A2/A5/A3) |
| `joblink.py` · `erp.py` | ผูก job ข้าม project (ERP booking → room+time→job) |
| `dashboard_api.py` · `dashboard.html` | server (ingest/query/serve) + dashboard per-job 2-track |

## เริ่มเทส (บนเครื่องที่มี Resolve Studio)
```bash
# ตั้ง env ให้ชี้ Resolve scripting (path ต่าง OS — ดู HANDOFF/TRACK_A)
python probe.py            # ดูว่า API เวอร์ชันนี้เผยอะไรได้จริง (โดยเฉพาะ color/audio)
python resolve_poller.py   # snapshot JSON จริงที่จะส่งเข้า server
```

## สถานะ
Track A + joblink + ERP connector + dashboard = เขียน + เทส (mock) ผ่านแล้ว · **ยังไม่รันบน Resolve/OS จริง** (gate ถัดไป) · Linux/Windows activity sampler ยังไม่ทำ (ตอนนี้ macOS) — รายละเอียดใน `HANDOFF.md`

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
