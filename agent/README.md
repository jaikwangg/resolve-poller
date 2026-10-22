# agent — รันบน workstation (7 เครื่อง: 1 edit + 6 color)

อ่าน DaVinci Resolve (read-only, ไม่เปลี่ยนหน้า) + idle / Resolve-หน้าสุด (bool) → POST record ขึ้น server ทุก ~1 นาที

## ต้องมี
- DaVinci Resolve **Studio** + `Preferences → System → General → External scripting = Local`
- Python 3 · **Windows/Linux ไม่ต้อง pip** · **macOS:** `pip install -r requirements.txt`
- Linux (ห้อง color): `libxss1` (+ option `xdotool`) · X11 เท่านั้น

## env (ตั้งต่อเครื่อง)
```
EDITORTRACK_ROLE=conform          # ห้อง edit · ห้อง color = colorist
EDITORTRACK_DETECT_GRADE=0        # ห้อง edit=0 (ไม่สแกนสี) · ห้อง color=1
EDITORTRACK_GRADE_METHOD=lut      # เกณฑ์ "เกรดแล้ว": lut (สีเปลี่ยนจริง, default) · nodes (node>1 แบบเดิม) — ยืนยันด้วย `python3 probe_grade.py`
EDITORTRACK_SERVER=http://<server>:8000/ingest
EDITORTRACK_TOKEN=<token ของเครื่องนี้>   # ผูกกับ hostname — server: python manage_tokens.py issue <host>
EDITORTRACK_EXTRACT_SHOTS=0       # default ปิด: ไม่ส่ง shot code (ข้อมูลลูกค้า) · =1 เมื่อตกลงกับเจ้าของงานแล้ว
EDITORTRACK_RESOLVE_PROCS=resolve,resolve.exe   # executable ที่นับว่าเป็น Resolve (Linux/Windows, ตรงตัว)
EDITORTRACK_RESOLVE_BUNDLES=com.blackmagic-design.DaVinciResolve,com.blackmagic-design.DaVinciResolveLite   # macOS
# offline queue (~/.editortrack/queue.jsonl): ส่งไม่ได้/5xx/401/403/408/429 → เก็บไว้ส่งรอบหน้า (401/403 = token ไม่ตรงช่วง rollout — ไม่ทิ้งข้อมูล)
#   4xx อื่น (record เสีย 400, 413, 422) → ทิ้ง + log · ทุกรอบส่ง queue ตามลำดับก่อน แล้วค่อยส่ง record ใหม่ (ค้าง = ตัวใหม่ต่อท้าย ไม่ส่งแซง)
EDITORTRACK_QUEUE_MAX=10080       # บรรทัดสูงสุด (~7 วัน) · EDITORTRACK_QUEUE_MAX_AGE_H=168 · EDITORTRACK_QUEUE_MAX_MB=16 — เกิน = ทิ้งตัวเก่าสุด (token ผิดถาวรจึงไม่ทำดิสก์เต็ม)
                                  # เขียนใหม่แบบ atomic (tmp+rename) และลบเฉพาะตัวที่ส่งสำเร็จแล้ว
EDITORTRACK_FLUSH_BUDGET_S=60      # งบส่ง backlog ต่อรอบ (วิ) · EDITORTRACK_FLUSH_MAX_LINES=2000 · EDITORTRACK_FLUSH_PERSIST_EVERY=50 (เขียนความคืบหน้าทุก N บรรทัด)
#   ส่งไม่หมดในงบ → ที่เหลือส่งรอบหน้า + record ใหม่ต่อท้าย queue (unit Linux: TimeoutStartSec=120 — งบต้องน้อยกว่านี้)
# Resolve scripting (per OS — ดู ../DATAFLOW.md §Config)
RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB / PYTHONPATH
```

## run
```
python probe.py       # ตรวจ API ต่อรุ่น Resolve (ครั้งเดียวตอนตั้งค่า)
python resolve_poller.py         # ดู snapshot จริง
python activity_sampler.py --watch   # ดู idle + resolve_frontmost สด (ส่งแค่ boolean ไม่มีชื่อแอป/หน้าต่าง)
python runner.py      # 1 รอบ (เทส) → แล้วตั้ง scheduler ทุก 1 นาที (deploy/install.sh)
```
scheduler: macOS=LaunchAgent · Windows=Task Scheduler · Linux=cron (ดู ../DATAFLOW.md §Deploy)

## ไฟล์
`resolve_poller.py` A1 · `activity_sampler.py` A2 · `scope.py` A5(%) · `joblink.py` job · `runner.py` A3 · `probe.py` diagnostic
