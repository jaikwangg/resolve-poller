# เอกสารข้อมูลที่ editortrack เก็บ (สำหรับขอความยินยอม) — 1 หน้า

ฉบับ 2026-10-26 · ใช้กับ agent รุ่นที่ลดข้อมูลแล้ว (branch `night/security-min`) · **ยังไม่ได้ติดตั้งบนเครื่องจริง**
ร่างนี้ต้องผ่านผู้บริหาร (เจ้าของข้อมูล) และทนาย (PDPA + สัญญา/NDA ลูกค้า) ก่อนใช้จริง

## ระบบนี้ทำอะไร
โปรแกรมเล็ก ๆ (agent) บนเครื่องตัดต่อ/เกรดสี อ่านสถานะงานจาก DaVinci Resolve **แบบอ่านอย่างเดียว** ทุก ~1 นาที
แล้วส่งตัวเลขความคืบหน้าของงาน (%) และ "เครื่องกำลังถูกใช้งานใน Resolve หรือไม่" ไปที่ server ภายในบริษัท
เพื่อให้รู้ว่าแต่ละงานคืบหน้าแค่ไหน และชั่วโมงห้องที่จองถูกใช้จริงเท่าไร **ไม่ใช่เครื่องมือประเมินตัวบุคคล**

## สิ่งที่ไม่เก็บ
- ไม่เก็บชื่อผู้ใช้ (login) ของเครื่อง · ไม่เก็บชื่อแอปอื่นที่เปิดอยู่ (เก็บแค่ "Resolve อยู่หน้าสุดไหม" และ "จอล็อกอยู่ไหม")
- ไม่ถ่ายภาพหน้าจอ ไม่อ่านไฟล์งาน ไม่อ่านภาพ/เสียง ไม่บันทึกการกดแป้น (นับแค่ "ไม่มีการใช้เมาส์/คีย์บอร์ดมากี่วินาที")
- ไม่เก็บ shot code, ชื่อ clip, path ไฟล์, metadata ของ clip, ชื่อ LUT (ปิดไว้เป็นค่าเริ่มต้น — ดูตาราง "เปิดได้เฉพาะเมื่อได้รับยินยอมเพิ่ม")
- server ตัด `user` และชื่อแอปหน้าสุดทิ้งก่อนเขียนลง disk เสมอ แม้ agent รุ่นเก่าจะส่งมา

## เก็บที่ไหน · ใครเห็น · นานเท่าไร
| เรื่อง | คำตอบ |
|---|---|
| เก็บที่ไหน | server ภายในบริษัทเครื่องเดียว โฟลเดอร์ `EDITORTRACK_STORE` (ค่าเริ่มต้น `/srv/editortrack`, สิทธิ์ 700 เจ้าของเท่านั้น, ไฟล์ 600) ไฟล์ละ 1 วัน · ไม่ส่งออกนอกบริษัท ไม่ขึ้น cloud |
| ใครเห็น | ผู้ที่ได้ user/รหัสอ่าน (ผู้บริหาร, PM, ผู้ดูแลระบบ) ผ่านหน้าเว็บที่ต้อง login · เครื่อง workstation ส่งข้อมูลได้แต่อ่านไม่ได้ |
| ส่งต่อให้ใคร | PMS ของบริษัท (เมื่อเปิดใช้): เฉพาะ job/stage/% และช่วงเวลาที่เครื่องทำงาน (host, ช่วงเวลา, วินาที active, booking) — ไม่มีข้อมูลระบุตัวคน |
| เก็บนานเท่าไร | 90 วัน (ตั้งได้ด้วย `EDITORTRACK_RETENTION_DAYS`) แล้วลบอัตโนมัติทุกวันด้วย `server/retention.py` |
| ใช้เพื่ออะไร | ความคืบหน้างานและชั่วโมงห้องต่อโปรเจกต์ (อธิบายว่างานส่วนไหนใช้เวลา ไม่ใช่ใครผิด) · ห้ามใช้ประเมินผลงานรายบุคคล |

## หยุดเก็บได้อย่างไร
- เครื่องเดียว ทันที: สร้างไฟล์ `~/.editortrack/DISABLED` หรือรัน `sh agent/deploy/killswitch.sh on` → agent หยุดทั้งการอ่าน Resolve และการส่งข้อมูล
- ทุกเครื่องพร้อมกัน: ผู้ดูแลแก้ `agent_control.json` บน server เป็น `enabled: false` → มีผลภายใน 1 นาที
- ถอนออกทั้งหมด: `sh agent/deploy/uninstall.sh --purge` (ลบ agent และข้อมูลค้างส่งบนเครื่อง)
- ขอลบข้อมูลที่เก็บไปแล้ว: แจ้งผู้ดูแลระบบ (IT) — ลบไฟล์รายวันที่เกี่ยวข้องบน server

## ทุก field ที่ agent ส่ง (ค่าเริ่มต้น) — เทส `agent/tests/test_data_sheet.py` บังคับให้ตรงกับรายการนี้
ประเภท: **งาน** = ข้อมูลงานของลูกค้า/โปรเจกต์ · **เครื่อง** = สถานะเครื่อง/โปรแกรม · **ใช้งาน** = การใช้งานเครื่อง (ไม่ระบุชื่อคน)

<!-- fields:agent:begin -->
| field | ประเภท | ความหมาย |
|---|---|---|
| `schema` | เครื่อง | เวอร์ชันรูปแบบ record |
| `ts` | เครื่อง | เวลาที่เก็บ (UTC) |
| `host` | เครื่อง | ชื่อเครื่อง (ใช้ผูกกับห้องที่จอง) |
| `stage` | เครื่อง | หน้าที่ของเครื่อง: data/conform/color/subtitle/master |
| `page` | ใช้งาน | หน้า Resolve ที่เปิดอยู่ (edit/color/...) |
| `activity.idle_sec` | ใช้งาน | วินาทีนับจากการใช้เมาส์/คีย์บอร์ดครั้งล่าสุด (ไม่บันทึกว่ากดอะไร) |
| `activity.active` | ใช้งาน | มีการใช้งานภายใน 90 วินาทีหรือไม่ |
| `activity.resolve_frontmost` | ใช้งาน | Resolve อยู่หน้าสุดหรือไม่ (ใช่/ไม่ใช่ เท่านั้น) |
| `activity.locked` | ใช้งาน | จอล็อกอยู่ (มีเฉพาะตอนล็อก) |
| `activity.platform` | เครื่อง | ระบบปฏิบัติการ |
| `activity.unavailable` | เครื่อง | อ่านสถานะการใช้งานไม่ได้ |
| `activity.error` | เครื่อง | ข้อความ error สั้น ๆ เมื่ออ่านไม่ได้ |
| `source.ok` | เครื่อง | อ่านข้อมูลงานสำเร็จหรือไม่ |
| `source.reason` | เครื่อง | เหตุผลที่ไม่อ่าน/อ่านไม่ได้ (เช่น มีคนใช้เครื่องอยู่) |
| `source.detail` | เครื่อง | รายละเอียดของเหตุผล (stage data/master: โฟลเดอร์ที่ตั้งให้นับไฟล์) |
| `source.skipped` | เครื่อง | ข้ามรอบนี้โดยตั้งใจ (ไม่แตะ Resolve) |
| `source.ts` | เครื่อง | เวลาที่อ่าน Resolve |
| `source.page` | ใช้งาน | หน้า Resolve |
| `source.project` | งาน | ชื่อ project ใน Resolve (ใช้ผูกกับ booking) |
| `source.resolve_version` | เครื่อง | เวอร์ชัน Resolve |
| `source.timeline_count` | งาน | จำนวน timeline ใน project |
| `source.rendering` | เครื่อง | กำลัง render หรือไม่ |
| `source.timeline.name` | งาน | ชื่อ timeline ที่เปิดอยู่ |
| `source.timeline.duration_sec` | งาน | ความยาว timeline |
| `source.timeline.fps` | งาน | frame rate |
| `source.timeline.video_tracks` | งาน | จำนวน video track |
| `source.timeline.audio_tracks` | งาน | จำนวน audio track |
| `source.timeline.video_clips` | งาน | จำนวน clip วิดีโอ |
| `source.timeline.graded_clips` | งาน | จำนวน clip ที่เกรดแล้ว (ตัวเลขเท่านั้น) |
| `source.timeline.markers` | งาน | จำนวน marker |
| `source.timeline.scan_ms` | เครื่อง | เวลาที่ใช้อ่าน |
| `source.timeline.per_clip` | เครื่อง | อ่านราย clip หรือนับอย่างเดียว (timeline ใหญ่) |
| `source.timeline.grade.method` | เครื่อง | วิธีนับ clip ที่เกรด (nodes/lut) |
| `source.timeline.grade.lut_gated` | เครื่อง | วิธี lut ถูกปิดไว้หรือไม่ |
| `source.timeline.grade.exported` | เครื่อง | จำนวน clip ที่ตรวจใหม่รอบนี้ |
| `source.timeline.grade.pending` | เครื่อง | clip ที่ยังไม่ได้ตรวจ |
| `source.timeline.grade.failed` | เครื่อง | clip ที่ตรวจไม่ได้ |
| `source.timeline.grade.ms` | เครื่อง | เวลาที่ใช้ตรวจ |
| `source.scope_hint` | งาน | เป้าหมายจาก marker `@scope` ที่ทีมใส่เอง (ถ้ามี) |
| `source.job_hint` | งาน | เลข job จาก marker `@job` ที่ทีมใส่เอง (ถ้ามี) |
| `source.offline_target_sec` | งาน | ความยาว offline edit ที่ใช้เป็นเป้า conform |
| `source.render.jobs` | เครื่อง | (ปิดถาวร ค่าว่างเสมอ) |
| `source.render.max_completion` | เครื่อง | (ปิดถาวร ค่าว่างเสมอ) |
| `source.render.polled` | เครื่อง | (ปิดถาวร = false) |
| `source.guard.calls` | เครื่อง | จำนวนคำสั่งอ่านที่ส่งให้ Resolve |
| `source.guard.ms` | เครื่อง | เวลารวมที่ใช้กับ Resolve |
| `source.guard.aborted` | เครื่อง | หยุดอ่านกลางคันหรือไม่ (เช่น มีคนกลับมาใช้เครื่อง) |
| `source.guard.violations` | เครื่อง | คำสั่งต้องห้ามที่ถูกกันไว้ (ควรว่างเสมอ) |
| `source.files` | งาน | stage data/master: จำนวนไฟล์ที่ผลิตแล้วในโฟลเดอร์ที่ตั้ง |
| `source.size_bytes` | งาน | stage data/master: ขนาดรวม |
| `source.cues` | งาน | stage subtitle: จำนวน cue |
| `job.job_id` | งาน | เลข job ที่ agent หาได้ |
| `job.title` | งาน | ชื่อ job |
| `job.role` | เครื่อง | หน้าที่ของเครื่อง |
| `job.resolved_by` | เครื่อง | ได้เลข job มาจากไหน |
| `job.linked` | เครื่อง | ผูกกับ job จริงแล้วหรือยัง |
| `job.target_duration_sec` | งาน | ความยาวเป้าหมายที่ตั้งไว้ |
| `progress.stage` | งาน | stage ของ % นี้ |
| `progress.pct` | งาน | % ความคืบหน้า |
| `progress.done` | งาน | จำนวนที่เสร็จ (ถ้ามี) |
| `progress.total` | งาน | จำนวนทั้งหมด (ถ้ามี) |
| `progress.detail.graded_clips` | งาน | color: clip ที่เกรดแล้ว |
| `progress.detail.video_clips` | งาน | color: clip ทั้งหมด |
| `progress.detail.duration_sec` | งาน | conform: ความยาว timeline |
| `progress.detail.offline_target_sec` | งาน | conform: ความยาวเป้า |
| `progress.detail.shots_seen` | งาน | conform: จำนวน shot ที่เห็น (ตัวเลข) |
| `progress.detail.parts` | งาน | conform: % แยกส่วน (ความยาว/จำนวน shot) |
| `progress.detail.files` | งาน | data/master/subtitle: จำนวนไฟล์ |
| `progress.detail.size_gb` | งาน | data/master: ขนาดรวม |
| `progress.detail.newest` | งาน | เวลาของไฟล์ล่าสุด |
| `progress.detail.watch_dir` | งาน | data/master: โฟลเดอร์ที่ตั้งให้นับ |
| `progress.detail.glob` | งาน | data/master: รูปแบบชื่อไฟล์ที่นับ |
| `progress.detail.cues` | งาน | subtitle: จำนวน cue |
| `progress.detail.last_cue` | งาน | subtitle: timecode ของ cue สุดท้าย |
| `agent.version` | เครื่อง | เวอร์ชัน agent |
| `agent.python` | เครื่อง | เวอร์ชัน Python |
| `agent.os` | เครื่อง | ระบบปฏิบัติการ + เวอร์ชัน |
| `agent.priority` | เครื่อง | ลำดับความสำคัญของ process (ต่ำสุด) |
| `agent.rss_mb` | เครื่อง | หน่วยความจำที่ agent ใช้ |
| `agent.turn_ms` | เครื่อง | เวลาที่ใช้ต่อรอบ |
| `agent.guard` | เครื่อง | สรุปการเรียก Resolve รอบนี้ (ว่างถ้าไม่ได้เรียก) |
| `agent.guard.calls` | เครื่อง | เหมือน `source.guard.calls` |
| `agent.guard.ms` | เครื่อง | เหมือน `source.guard.ms` |
| `agent.guard.aborted` | เครื่อง | เหมือน `source.guard.aborted` |
| `agent.guard.violations` | เครื่อง | เหมือน `source.guard.violations` |
| `agent.tripped` | เครื่อง | ระบบป้องกันตัดการทำงานไว้หรือไม่ |
| `agent.backoff_fails` | เครื่อง | จำนวนครั้งที่ล้มเหลวติดกัน |
| `agent.queue_lines` | เครื่อง | record ที่ค้างส่ง (server ติดต่อไม่ได้) |
<!-- fields:agent:end -->

server เติมเอง 2 field ตอนรับ:
<!-- fields:server:begin -->
| field | ประเภท | ความหมาย |
|---|---|---|
| `_rx.at` | เครื่อง | เวลาที่ server ได้รับ |
| `_rx.ip` | เครื่อง | IP ภายในของเครื่องที่ส่ง (ใช้ตรวจปัญหาการส่ง) |
<!-- fields:server:end -->

## เปิดได้เฉพาะเมื่อได้รับยินยอมเพิ่ม (ค่าเริ่มต้นปิด)
<!-- fields:optin:begin -->
| field | เปิดด้วย | ความหมาย |
|---|---|---|
| `source.shots` | `EDITORTRACK_EXTRACT_SHOTS=1` | shot code จาก marker + สถานะเกรดราย shot (ข้อมูลลูกค้า) |
| `source.clips_detail` | `EDITORTRACK_EXTRACT_CLIP_META=1` | รายละเอียดราย clip (ชื่อ clip, path, metadata, ชื่อ node) — ใช้ตรวจระบบบนงาน dummy เท่านั้น |
| `source.clips_detail_count` | `EDITORTRACK_EXTRACT_CLIP_META=1` | จำนวน clip ที่ดึงรายละเอียด |
<!-- fields:optin:end -->
ชื่อ LUT ไม่ถูกเก็บในทุกกรณี

## ไฟล์อื่นบน server
`agent_control.json` (เปิด/ปิด agent รายเครื่อง), `health_state.json` (สถานะแจ้งเตือน), ไฟล์ token (เก็บเฉพาะค่า hash) — ไม่มีข้อมูลงานหรือข้อมูลบุคคล
