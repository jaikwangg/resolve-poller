# วันทดสอบเฟส 3 — Checklist (พิมพ์ได้) · Phase 3 test day — printable checklist

**editortrack agent บน workstation จริง: Mac conform 1 เครื่อง + Linux grade 1 เครื่อง · ใช้ project DUMMY เท่านั้น**
**editortrack agent on real workstations: one Mac conform + one Linux grade · DUMMY projects only**

> ⛔ **ห้ามใช้กับงานลูกค้าเด็ดขาด** ไม่มี project / media / library ของลูกค้าเปิดหรือเชื่อมอยู่บนเครื่องระหว่างทดสอบ
> **Never with client work.** No client project, media or project library may be open or connected on the machine during the test.
> ที่มา / Source: `docs/research/WORKSTATION_SAFETY.md` §7 (เกณฑ์ผ่านทุกข้อมาจากที่นั่น / every threshold comes from there), §8 stage R2,
> `MAINTENANCE.md`, `NEXT_STEPS.md` เฟส 3. เครื่องมือ / tools: `testday/` (ดู / see `testday/README.md`). ผลลัพธ์ / results: `docs/testday/RESULTS_TEMPLATE.md`.

| | Mac conform | Linux grade |
|---|---|---|
| Hostname | ______________ | ______________ |
| Resolve Studio version (exact) | ______________ | ______________ |
| OS version / Python | ______________ | ______________ |
| Agent version (tag) | ______________ | ______________ |
| วันที่ / Date | ______________ | ______________ |

---

## 0. ใครทำ ใครเซ็น · Roles and signatures

| บทบาท / Role | ใคร / Who | ทำอะไร / Does |
|---|---|---|
| **ผู้ใช้เครื่อง / Operator** (editor ห้อง conform, colorist ห้อง grade) | ____________ | ทำงานปกติบน project DUMMY, ทำขั้นตอน revision, บอกทุกอย่างที่ "รู้สึกแปลก" ทันที · Works normally on the DUMMY project, runs the revision steps, reports anything odd at once. **เซ็น / signs:** S1, S2, P1 "ไม่รู้สึกว่ามีอะไรรบกวน / noticed nothing" |
| **ผู้คุมการทดสอบ / Test lead** (IT, ไม่ใช่ developer / not the developer) | ____________ | เตรียมเครื่อง รันเครื่องมือ จดเวลา เก็บหลักฐาน ตัดสิน PASS/FAIL ตามเกณฑ์ · Prepares, runs the tools, keeps time and evidence, marks PASS/FAIL. **เซ็นย่อ / initials:** every step. **K5 ต้องทำโดยคนนี้จาก runbook อย่างเดียว / K5 is done by this person from the runbook alone** |
| **ผู้ดูแล server / Server admin** | ____________ | lease (`agent_control.json`), คัดลอก record (read-only), K3/K4/C6 · Lease, read-only copy of records, K3/K4/C6 |
| **เจ้าของระบบ / Owner** | ____________ | อนุมัติเริ่ม, สั่งหยุด, เซ็นปิดเฟส 3 และ stage R2 (§8) · Approves the start, may stop it, signs phase-3 exit and stage R2 |

---

## 1. เกณฑ์หยุดทันที · Abort criteria (one FAIL stops the protocol)

หยุดทันทีเมื่อเกิดข้อใดข้อหนึ่ง · **Stop at once if any of these happens:**

- ☐ หน้า / timeline / playhead / selection / viewer / dialog / focus ของ Resolve เปลี่ยนเองตอน agent ทำงาน · A Resolve page, timeline, playhead, selection, viewer, dialog or focus changes by itself at an agent turn
- ☐ Resolve กระตุก ค้าง แครช หรือมี error dialog · Resolve stutters, hangs, crashes or shows an error dialog
- ☐ เฟรมตกตอนเปิด agent ทั้งที่ตอนปิด agent เล่นได้ลื่น · Dropped frames with the agent ON where the agent-OFF run was clean
- ☐ record มี `forbidden_call`, `state_changed_during_scan` + `suspect_agent:true`, หรือไฟล์ `~/.editortrack/TRIPPED` เกิดขึ้น · A record shows `forbidden_call`, a suspect state change, or the breaker trips
- ☐ project มี undo entry / ขึ้นถามให้ save ทั้งที่ไม่ได้แก้ · The project gets an undo entry or a save prompt that nobody caused
- ☐ เห็น / เปิด / เชื่อม project หรือ media ของลูกค้า หรือ server ได้ข้อมูลจากเครื่องที่ไม่ใช่เครื่องทดสอบ · Any client project/media is seen, opened or connected, or the server receives data from a non-test host
- ☐ ผู้ใช้เครื่องบอกว่ามีอะไรแปลก (ถือเป็น FAIL จนกว่าจะพิสูจน์ได้ว่าไม่ใช่ agent) · The operator reports anything odd (FAIL until proven not to be the agent)

**เมื่อหยุด / On abort:** (1) `sh ~/editortrack/agent/deploy/killswitch.sh on` บนเครื่องนั้น / on that machine; (2) server admin ตั้ง `"default":{"enabled":false}` ทุก host ใน `agent_control.json`; (3) จดเวลา + เก็บหลักฐาน (ไฟล์ screen_watch, `agent.log`, record) / note the time, keep the evidence; (4) แจ้ง owner; (5) แก้ → เพิ่ม automated test → เริ่มใหม่ตั้งแต่ S1 / fix → new automated test → restart from S1.

---

## 2. เตรียมเครื่อง · Setup (วันก่อน / the day before, and the morning)

| # | ทำ / Do | ☐ Mac | ☐ Linux | ย่อ / Init |
|---|---|---|---|---|
| 2.1 | Owner อนุมัติวันทดสอบ, เครื่อง 2 เครื่องนี้ไม่มีงานลูกค้าค้าง · Owner approved; no client work pending on these two machines | ☐ | ☐ | |
| 2.2 | สร้าง **project library แยก** ชื่อ `ET_DUMMY` (local disk) และ project `ET_DUMMY_TESTDAY`; ตัดการเชื่อม library ลูกค้าทั้งหมดใน Project Manager · Separate local project library `ET_DUMMY`, project `ET_DUMMY_TESTDAY`; disconnect every client library | ☐ | ☐ | |
| 2.3 | คัดลอก kit: `testday/` → `~/ET_TESTDAY/kit/`, สร้าง `~/ET_TESTDAY/results/` · Copy the kit folder; create the results folder | ☐ | ☐ | |
| 2.4 | สร้าง media + timeline DUMMY (`docs/testday/DUMMY_PROJECT.md`) **ที่เครื่องเตรียม แล้วคัดลอกมา** (แนะนำ) หรือบนเครื่องด้วย static ffmpeg ใน kit: `make_dummy_project.py --out ~/ET_DUMMY_TESTDAY --uhd`; ดูพื้นที่ดิสก์จาก `--dry-run` ก่อน · Make it on a prep machine and copy (preferred), or on the workstation with a static ffmpeg in the kit; check disk space with `--dry-run` | ☐ | ☐ | |
| 2.5 | Import `timelines/T0030_v1.xml` ลอง 1 อัน: clip ครบ 30, link media ได้ · Import one timeline as a check: 30 clips, media linked | ☐ | ☐ | |
| 2.6 | **ปิด Resolve** แล้ว snapshot ค่า preference ก่อนติดตั้ง (U1): `python3 ~/ET_TESTDAY/kit/drill.py --log ~/ET_TESTDAY/results/drill.csv before` · Quit Resolve, snapshot prefs **before install** | ☐ | ☐ | |
| 2.7 | ติดตั้ง agent จาก release ที่ติด tag + env จาก `deploy/stations/<host>.sh`, `EDITORTRACK_RESOLVE_VERIFIED` = เวอร์ชันที่ทดสอบ (`make-env.sh`, `install.sh`) · Install the tagged release with the station env and the exact verified Resolve version | ☐ | ☐ | |
| 2.8 | Server: lease `allow_resolve:true` **เฉพาะ 2 host นี้**, `allowed_versions` = เวอร์ชันนี้; **ปิดการส่ง PMS** (เฟส 3 ไม่ส่ง PMS) · Lease for these two hosts only; PMS push OFF | ☐ | ☐ | |
| 2.9 | ปิดแอปแชท/เมล/notification · Close chat, mail, notifications (the screen is recorded) | ☐ | ☐ | |
| 2.10 | macOS: ให้ Terminal สิทธิ์ Screen Recording (System Settings → Privacy) แล้ว **เอาออกหลังจบวัน** · Grant Terminal Screen Recording; **remove it after the test** | ☐ | — | |
| 2.11 | Linux: `echo $XDG_SESSION_TYPE` = `x11`; ใช้ `ffmpeg` หรือ `import` (ImageMagick) ที่มีอยู่แล้วบนเครื่อง — ถ้าไม่มี ใช้ static ffmpeg ใน `~/ET_TESTDAY/kit/bin` (**ห้ามติดตั้งผ่าน package manager** เพราะ U1 เทียบเครื่องก่อน/หลัง) · X11 session; use an ffmpeg/ImageMagick already there, else a static ffmpeg in the kit folder (no package install: U1 compares the machine) | — | ☐ | |
| 2.11b | ห้ามจอดับ/ล็อกระหว่าง soak: Mac `caffeinate -d -i` ค้างไว้ในอีก Terminal, ปิด screen lock/screensaver ของ session; Linux `xset s off -dpms` · No display sleep / lock / screensaver during soaks | ☐ | ☐ | |
| 2.12 | **Baseline 30 นาที agent ปิด** ไม่มีคนแตะเครื่อง: `screen_watch.py capture --out ~/ET_TESTDAY/results/baseline --hours 0.5` (ตอบคำถาม "frame แรกเห็น Resolve ไหม") แล้ว `analyze --no-operator` → ผลจะเป็น INCOMPLETE (agent ปิด) — ใช้แค่ "most frequent change boxes" กำหนด `--mask` (นาฬิกา ฯลฯ) · 30-min agent-OFF idle baseline (its verdict is INCOMPLETE by design); use its hotspots to choose the masks | ☐ | ☐ | |
| 2.13 | ทุกครั้งก่อน S1/S2/S3/P: agent **เปิด** — `sh deploy/killswitch.sh off`, ไม่มีไฟล์ system kill (`sudo rm` ถ้ามี), `killswitch.sh status` ว่าง, และ `agent.log` มีรอบที่ได้ `200` · Before every S/P step: agent ON (local and system kill files gone, status clean, a `200` turn in agent.log) | ☐ | ☐ | |

---

## 3. ขั้นตอนทดสอบและเกณฑ์ผ่าน · Checks and PASS thresholds

ทุกข้อจด: วันที่, host, version, ผล, ไฟล์หลักฐาน (ลงใน RESULTS_TEMPLATE) · Every step records date, host, versions, result, evidence file.
**FAIL ข้อเดียว = หยุด (ข้อ 1)** · **One FAIL stops the protocol (section 1).**

### S — หน้าจอและสถานะ project · UI and project state

| Step | ทำ / Procedure | เกณฑ์ผ่าน / PASS threshold | เครื่องมือ / Tool | Mac | Linux | Init |
|---|---|---|---|---|---|---|
| **S0** | Preferences → System → General → External scripting = **Local**; ตรวจ port: Linux `ss -ltnp`, Mac `lsof -iTCP -sTCP:LISTEN` · Scripting Local; check listening ports | Local; ไม่มี port scripting ของ Resolve บน LAN address · nothing of Resolve listening on a LAN address | screenshot + output | ☐ | ☐ | |
| **S1** | Soak 8 ชม. เวลาทำงาน agent 60 วิ, ผู้ใช้ทำงานปกติทุกหน้า (Media, Cut, Edit, Fusion, Color, Fairlight, Deliver) + ทำ revision R0–R9 ระหว่างนี้ · 8 h working soak on every page, revision steps R0–R9 done during it | **0** การเปลี่ยน page/timeline/playhead/viewer/dialog/focus ที่ตรงกับรอบ agent (±2 วิ); ไม่มี `forbidden_call`; breaker ไม่ trip; ผล analyze = **PASS** (INCOMPLETE/PENDING ไม่ใช่ผ่าน; REVIEW ทุกข้อ test lead ดูภาพแล้ว) · **0** changes at agent turns; no forbidden_call; breaker never tripped; analyze verdict **PASS** (INCOMPLETE/PENDING are not a pass; every REVIEW looked at) | `screen_watch.py capture --hours 8` → `analyze --agent-log ... --records ... --host ...` | ☐ | ☐ | |
| **S2** | Colorist 2 ชม. หน้า Color: เลือก clip, version, still, group; agent สแกนทุกรอบ (`EDITORTRACK_SCAN_MIN_IDLE_SEC=0` **เฉพาะ project DUMMY**) · 2 h colorist session, agent forced to scan every turn | 0 การเปลี่ยน clip ที่เลือก, node ปัจจุบัน, version, gallery, viewer wipe; colorist ไม่รู้สึกอะไร · 0 changes to selection/node/version/gallery/wipe; colorist reports nothing | `screen_watch.py` + ลายเซ็น colorist / colorist signature | ☐ | ☐ | |
| **S3** | เปิด project DUMMY ที่เพิ่ง save, **ไม่แตะเลย** 8 ชม. (ข้ามคืนได้) แล้วดู Edit → History, ปิด project ดูว่าถาม save ไหม, ดูเวลาแก้ไขใน Project Manager. **ทำซ้ำอีกรอบด้วย `EDITORTRACK_EXTRACT_CLIP_META=1`** (และ `EDITORTRACK_ALLOW_EXPORT_LUT=1` เฉพาะถ้าจะใช้ ExportLUT) · Idle-project soak 8 h, nobody touches; **repeat with `EXTRACT_CLIP_META=1`** (and `ALLOW_EXPORT_LUT=1` only if ExportLUT is wanted) | ไม่มี undo, **ไม่ถาม save**, เวลาแก้ไขไม่เปลี่ยน; screen_watch `--no-operator` = **PASS** ทั้งสองรอบ · No undo, **no save prompt**, modified time unchanged; screen_watch **PASS** in both runs | `screen_watch.py ... analyze --no-operator --records ... --host ...` + screenshot History/Project Manager | ☐ | ☐ | |
| **S4** | `python3 probe.py --dummy-project` และ `probe_grade.py --dummy-project` ครั้งเดียวต่อเวอร์ชัน Resolve · Run the probes once per Resolve version | หน้าไม่เปลี่ยน; เก็บ report · Page unchanged; keep the reports | probe output | ☐ | ☐ | |

> **ข้อจำกัด S1/S2 / limitation:** ขณะมีคนทำงาน การเปลี่ยนหน้าจอเกือบทั้งหมดเป็นของผู้ใช้ เครื่องมือจึงจับได้ชัดเฉพาะ "เปลี่ยนเองตอนไม่มีใครแตะ";
> การเปลี่ยนที่ซ้อนกับรอบ agent จะขึ้นเป็น REVIEW ให้ test lead ดูภาพ และคำบอกของผู้ใช้เครื่องเป็นหลักฐานหลักใน S2. S3 (ไม่มีคนแตะ) คือหลักฐานที่แรงที่สุด.
> While someone works, almost every screen change is theirs, so the tool is strong only for changes nobody caused; overlaps with agent
> turns are listed as REVIEW, and the operator's account is the main evidence in S2. S3 (nobody touching) is the strongest evidence.

### P — ประสิทธิภาพ · Performance

| Step | ทำ / Procedure | เกณฑ์ผ่าน / PASS threshold | เครื่องมือ / Tool | Mac | Linux | Init |
|---|---|---|---|---|---|---|
| **P1** | Timeline 4K และ 8K (`PB_4K`, `PB_8K`), 2 นาที × 10 ครั้งต่อสถานะ สลับ ปิด/เปิด agent, มือออกจากคีย์บอร์ด · 4K and 8K, 10 × 2 min per state, alternating, hands off | **เฟรมตกเพิ่ม 0**; รอบ agent ระหว่างเล่นเป็น skip `playback` ทั้งหมด และ `guard.calls` ≤ 12; ถ้ามีการสแกนระหว่างเล่น → FAIL · **0 extra dropped frames**; every turn during playback is a skip with ≤ 12 calls; any scan during playback = FAIL. (ถ้าตอนปิด agent ยังเฟรมตก = timeline นั้นใช้ไม่ได้ / if it drops with the agent OFF the timeline is invalid) | `playback_trials.py run` → `analyze --rate 24` | ☐ | ☐ | |
| **P2** | Timeline 300 และ 3000 clip, ผู้ใช้ไม่แตะ, agent เปิด 50 รอบ; ผู้ใช้ scrub ทันทีหลัง scan เริ่ม · 300 and 3000 clips, 50 turns; operator scrubs right after a scan starts | p95 `guard.ms` ≤ 1500 (หรือ budget ที่ owner เลือก); scan หยุดภายใน 1 chunk หลังผู้ใช้แตะ (`user_returned`); **ผู้ใช้ไม่รู้สึกหน่วง** · p95 guard.ms ≤ 1500; abort within one chunk; **operator notices no lag** | `turn_stats.py --budget-ms 1500` | ☐ | ☐ | |
| **P3** | Render preset ~10 นาที 3× ปิด agent, 3× เปิด agent (สลับ) แต่ละครั้งลงโฟลเดอร์ใหม่ · Render ~10 min, 3× off, 3× on, alternating, new folder each | ค่าเฉลี่ยต่างกัน ≤ 1 % และอยู่ใน 2σ ของรอบปิด; agent skip `rendering` ระหว่าง render · Mean delta ≤ 1 % and within 2σ of the off runs | `render_timer.py watch` × 6 → `analyze` | ☐ | ☐ | |
| **P4** | ใช้งานปกติ 1 ชม. วัดทุก 5 วิ · 1 h normal use, sample every 5 s | CPU เฉลี่ย < 0.5 % ของ 1 core; รอบไหนก็ < 2 วิ CPU; RSS < 100 MB; `~/.editortrack` < 15 MB; network < 5 KB/นาที (ตัวเลขจาก record เป็น**ค่าประมาณ** ถ้าใกล้เกณฑ์ให้วัดจริง: Linux `IPAccounting`/`nethogs`, Mac `nettop`) · avg CPU < 0.5 %, any turn < 2 s CPU, RSS < 100 MB, state < 15 MB, < 5 KB/min (the record-based network figure is an **estimate**; measure it if near the limit) | `agent_cost.py --minutes 60` + `turn_stats.py` | ☐ | ☐ | |
| **P5** | แนวโน้ม 7 วัน (`/api/health`) — หลังวันทดสอบ · 7-day trend, after the test day | RSS / เวลาต่อรอบไม่โต; queue ระบายหมด; ไม่มี alert · No growth; queue drains; zero alerts | `/api/health`, `turn_stats.py` per day | ☐ | ☐ | |

### C — เสถียรภาพ · Stability

| Step | ทำ / Procedure | เกณฑ์ผ่าน / PASS threshold | Mac | Linux | Init |
|---|---|---|---|---|---|
| **C1** | `kill -9` agent ระหว่างสแกน timeline 3000 clip, 50 ครั้ง · kill -9 mid-scan ×50 | Resolve ยังตอบสนอง ไม่มี error dialog ไม่แครช; รอบถัดไปปกติ · Responsive, no dialog, no crash; next turn normal | ☐ | ☐ | |
| **C2** | เปิด modal dialog (Project Settings / Save) ค้างไว้ 5 รอบ · Leave a modal dialog open for 5 turns | ไม่ค้างเกิน watchdog 90 วิ; dialog ไม่ถูกแตะ; Resolve ปกติหลังปิด dialog · No hang beyond 90 s; dialog unaffected | ☐ | ☐ | |
| **C3** | ปิด Resolve ระหว่างสแกน; เปิดใหม่ค้างหน้า splash 3 รอบ; ไม่เปิด Resolve 1 ชม. · Quit mid-scan; splash for 3 turns; Resolve closed 1 h | ไม่แครชตอนปิด; skip `resolve_busy` / `resolve_not_running`; ไม่มี zombie process · No crash; correct skips; no zombies | ☐ | ☐ | |
| **C4** | สลับ project และ timeline ระหว่างสแกน (ผู้ใช้ทำเอง) · Operator switches project and timeline mid-scan | รอบนั้นถูกทิ้ง (`state_changed_during_scan`, `suspect_agent:false`); breaker ไม่ trip; ไม่มีอะไรถูก "คืนค่า" · Discarded, no trip, nothing restored | ☐ | ☐ | |
| **C5** | Sleep/wake (Mac) และ reboot (ทั้งสอง) · Sleep/wake and reboot | agent กลับมาทำงาน ≤ 2 นาทีหลัง login; ไม่มีรอบซ้อน; ไม่มี backlog storm · Resumes ≤ 2 min; no duplicates; no storm | ☐ | ☐ | |
| **C6** | Server ดับ 3 นาที แล้ว 1 วัน (server admin) · Server stopped 3 min, then 1 day | queue เก็บแล้วระบาย ≤ 300 record/รอบ; เกินอายุ lease agent เงียบ แล้วกลับมาเมื่อ server กลับ · Queue fills and drains; silent after lease expiry; resumes | ☐ | ☐ | |

### K / U / R — สวิตช์ฉุกเฉิน ถอนการติดตั้ง ย้อนเวอร์ชัน · Kill switch, uninstall, rollback

| Step | ทำ / Procedure | เกณฑ์ผ่าน / PASS threshold | เครื่องมือ / Tool | Mac | Linux | Init |
|---|---|---|---|---|---|---|
| **K1** | `killswitch.sh on` | scheduler หยุดทันที; ไม่มีรอบใหม่ใน log 3 นาที; `status` = DISABLED · Scheduler stopped at once; no turn for 3 min | `drill.py k1` | ☐ | ☐ | |
| **K2** | ไฟล์ระดับเครื่อง (sudo) `/etc/editortrack/DISABLED` หรือ `/Library/Application Support/editortrack/DISABLED` · System kill file | รอบถัดไปเงียบ (≤ 60 วิ) · Next turn silent (≤ 60 s) | `drill.py k2` | ☐ | ☐ | |
| **K3** | Server admin ตั้ง `"enabled": false` · Fleet switch | ทุกเครื่องทดสอบเงียบภายใน 60 วิ · Every test agent silent within 60 s | `drill.py silence --tag K3` + `turn_stats.py` | ☐ | ☐ | |
| **K4** | Server เข้าไม่ได้นานกว่า lease (ทดสอบด้วย lease สั้น เช่น 10 นาที) · Server unreachable longer than the lease (use a short test lease, e.g. 10 min) | agent เงียบเมื่อ lease หมด: ไม่มี record และ queue ไม่โตอีก · Silent at lease expiry: nothing sent, queue stops growing | `drill.py silence --tag K4 --grace lease` | ☐ | ☐ | |
| **K5** | จับเวลา "owner สั่ง" → "agent หายไป" ถอนทั้ง 2 เครื่อง โดย **คนที่ไม่ใช่ developer** จาก runbook อย่างเดียว · Stopwatch, by someone other than the developer, runbook only | ≤ 5 นาทีต่อเครื่อง · ≤ 5 min per machine | `drill.py uninstall` | ☐ | ☐ | |
| **U1** | Snapshot ก่อนติดตั้ง (ข้อ 2.6) → ติดตั้ง + 1 วัน + uninstall + reboot → ปิด Resolve → snapshot หลัง · Before/after snapshot | `uninstall.sh --verify` ผ่าน; **preference ของ Resolve ตรงกันทุก byte**; ไม่มี unit ใหม่; ไม่มีไฟล์เหลือ · verify passes; **Resolve prefs byte-identical**; no new units; nothing left | `drill.py after` (`prefs_snapshot.py compare`; ถ้ายังไม่ purge ใส่ / if not purged add `--expected editortrack`) | ☐ | ☐ | |
| **U2** | `uninstall.sh --purge` | โฟลเดอร์ agent หายด้วย · Agent folder gone too | `drill.py uninstall --purge` | ☐ | ☐ | |
| **R1** | ติดตั้ง release N → อัปเกรด N+1 → ย้อนกลับ N ด้วยคำสั่งเดียว · Install N, upgrade, roll back | ย้อน ≤ 5 นาที; ไม่กระทบ Resolve; health แสดง N · Rollback ≤ 5 min, health shows N | stopwatch | ☐ | ☐ | |
| **R2** | อัปเกรด Resolve หรือใส่เวอร์ชันปลอมที่ไม่ verify · Unverified Resolve version | agent skip `resolve_version_unverified` จน owner เพิ่มเวอร์ชัน · Skips until the owner adds the version | `turn_stats.py` reasons | ☐ | ☐ | |

> **U1 หมายเหตุ / note:** "ก่อน" = snapshot ข้อ 2.6 (D0 ก่อนติดตั้งครั้งแรก); "หลัง" = หลัง K5/U2 uninstall + reboot ท้ายสัปดาห์.
> Resolve เขียน preference ของตัวเองเมื่อใช้งาน จึงต้องมี**วันควบคุม**หลัง uninstall โดย**ไม่มี agent**: `prefs_snapshot.py snapshot` เช้า → ใช้ Resolve ตามปกติ → snapshot เย็น → `compare --emit-allow`.
> "Before" = the 2.6 snapshot (D0, before the first install); "after" = after the K5/U2 uninstall + reboot at the end of the week; then a control
> day without the agent (snapshot morning and evening around normal Resolve use, `compare --emit-allow`).
> ไฟล์ที่ต่างเฉพาะวันมี agent = FAIL; ไฟล์ที่ต่างในวันควบคุมด้วย → ผลเป็น **CHECK** ไม่ใช่ PASS (§7 กำหนด byte-identical; ยอมรับได้หรือไม่ owner ตัดสิน).
> Resolve rewrites some prefs itself, so U1 is a dedicated cycle plus a control day without the agent. Files that differ only with the
> agent = FAIL; files that also differ in the control day give **CHECK**, not PASS (§7 says byte-identical; the owner decides).

### V — ตรวจ progress v2 ด้วย revision จริง (ไม่ใช่เกณฑ์ความปลอดภัย แต่ต้องผ่านก่อนใช้ตัวเลข) · Progress v2 validation

ทำระหว่าง S1 บน project DUMMY ตาม `REVISION_STEPS.txt` (R0–R9, ดู `DUMMY_PROJECT.md`) จดเวลาของแต่ละขั้น; หลังจบวันรัน
`progress_replay.py` กับ record ของวันนั้น (offline, ไม่ส่ง PMS) แล้วเทียบค่ากับขั้นตอน.
Done during S1 on the DUMMY project; note each step's time; afterwards replay the day's records offline with
`progress_replay.py` (nothing goes to PMS) and match the values to the steps.

| เกณฑ์ / Criterion | ☐ Mac | ☐ Linux |
|---|---|---|
| ไม่เคยขึ้น `done` / 100 % โดยไม่มี scope ที่ยืนยัน (R1–R9) · Never `done` / 100 % without a confirmed scope | ☐ | ☐ |
| ทุกครั้งที่ % ลด มี reason (`scope_change`, `scope_shrink`, `rework`, `regression`, …) · Every drop carries a reason | ☐ | ☐ |
| R8 (เหตุการณ์ 14 ต.ค.): ไม่มี 100 / ไม่มี `done` · The 14 Oct replay never reaches 100 or `done` | ☐ | ☐ |
| ไม่มีอะไรถูกส่งไป PMS ระหว่างเฟส 3 · Nothing pushed to PMS during phase 3 | ☐ | ☐ |

---

## 4. ตารางเวลาที่แนะนำ · Suggested schedule

| วัน / Day | Mac conform | Linux grade |
|---|---|---|
| D0 (เตรียม / prep) | 2.1–2.12, S0 | 2.1–2.12, S0 |
| D1 | S4, P1, P2, P3, P4, C1–C4 · แล้ว **2.13 เปิด agent** · คืนนั้น / overnight: **S3** | เหมือนกัน / same |
| D2 | **S1** (8 ชม. + revision R0–R9 = V), C5 · คืนนั้น: S3 ซ้ำด้วย `EXTRACT_CLIP_META=1` | **S1** + **S2** (colorist 2 ชม.), C5 · overnight S3 repeat |
| D3 | K1, K2, K3 (แล้ว **2.13 เปิด agent** กลับ / then turn the agent back on) | เหมือนกัน / same |
| D3–D9 | P5 (7 วัน / 7 days), C6 + K4 (server admin, lease สั้น / short lease) | เหมือนกัน / same |
| ท้ายสัปดาห์ / end of week | วันควบคุม U1 (ไม่มี agent) + รอบ U1, K5, U2, R1, R2 · U1 control day + U1 cycle, K5, U2, R1, R2 | เหมือนกัน / same |

---

## 5. ปิดเฟส 3 · Phase-3 exit

ทุกข้อ PASS ทั้ง 2 เครื่อง + ผู้ใช้เครื่องเซ็นว่าไม่รู้สึกว่ามีอะไรรบกวน + owner เซ็น stage R2 (§8). บันทึกใน `docs/research/SIGNOFF.md`
และเพิ่มแถวใน compatibility matrix (`MAINTENANCE.md` §2).
Every step PASS on both machines, the operators sign "noticed nothing", the owner signs stage R2; record it in SIGNOFF.md and add the
compatibility-matrix row.

| | ชื่อ / Name | ลายเซ็น / Signature | วันที่ / Date |
|---|---|---|---|
| Operator — Mac conform: "ไม่รู้สึกว่ามีอะไรรบกวน / I noticed nothing" | | | |
| Operator — Linux grade (colorist): "ไม่รู้สึกว่ามีอะไรรบกวน / I noticed nothing" | | | |
| Test lead: ทุกข้อ PASS ตามเกณฑ์ / every step PASS | | | |
| Owner: อนุมัติ stage R2 / approves stage R2 | | | |

**หลังจบ / Afterwards:** ถอนสิทธิ์ Screen Recording ของ Terminal (Mac) · เก็บโฟลเดอร์ `~/ET_TESTDAY/results` ไว้เป็นหลักฐาน แล้วลบ media DUMMY ได้ ·
Remove Terminal's Screen Recording permission (Mac); keep `~/ET_TESTDAY/results` as evidence; the DUMMY media may be deleted.
