# resolve-poller (editortrack)

ระบบติดตาม **ความคืบหน้า + เวลาทำงานจริง** ของทีมตัดต่อ **DaVinci Resolve** ระดับองค์กร แบบ DIY + local LLM
เก็บ 2 metric: **#1** % ความคืบหน้า (conform + color, proxy) · **#2** เวลา active-in-Resolve

> **เริ่มอ่านที่ [`HANDOFF.md`](HANDOFF.md)** — มี context ครบ: ภารกิจ, กฎเหล็ก, สถาปัตยกรรม, สถานะ (verified/ยัง), schema, next steps

## กฎเหล็ก (ย่อ)
- **เปิดเผย (worker รู้) + worker zero-touch** — worker ไม่ต้องทำ/ใส่อะไรเพื่อ tracking
- **% เป็น proxy/advisory เท่านั้น — ห้ามผูกเงินเดือน/ตัดสินคน**
- **creds ทั้งหมดผ่าน env — ห้าม commit** (ดู `.gitignore`)
- ไม่ใช่คำแนะนำกฎหมาย — ต้องผ่านทนาย (PDPA) ก่อน deploy

## โครงสร้าง (2 folder ตามการ deploy)

```
resolve-poller/
├── agent/     ← รันบน workstation 7 เครื่อง (อ่าน Resolve + idle → POST)   ดู agent/README.md
│   resolve_poller · activity_sampler · scope · joblink · runner · probe
├── server/    ← รันบนเครื่องกลาง 1 เครื่อง (ingest + ERP join + dashboard)  ดู server/README.md
│   dashboard_api · erp · dashboard.html
└── docs: HANDOFF (entry) · DATAFLOW · TRACK_A · API_REFERENCE · JOB_LINK
```

> **เริ่มอ่าน [`HANDOFF.md`](HANDOFF.md)** · dataflow + คำสั่งครบ [`DATAFLOW.md`](DATAFLOW.md)

## เริ่มเร็ว
```bash
# workstation (มี Resolve Studio)
cd agent   &&  python probe.py  &&  python runner.py
# server
cd server  &&  pip install -r requirements.txt  &&  EDITORTRACK_TOKEN=xxx uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
```

## สถานะ
API metric #1 (color/edit/shots) พิสูจน์บนโปรเจคจริงแล้ว (Resolve 21.1) · zero-touch (ไม่เด้งหน้า) · sampler mac/win/linux · ERP join + dashboard 2-track + per-shot reconcile (เทส) · **เหลือ:** ต่อ ERP จริง + Linux sampler บนห้อง grade + deploy — รายละเอียด `HANDOFF.md`

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
