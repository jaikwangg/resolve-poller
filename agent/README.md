# agent — รันบน workstation (7 เครื่อง: 1 edit + 6 color)

อ่าน DaVinci Resolve (read-only, ไม่เปลี่ยนหน้า) + idle/frontmost → POST record ขึ้น server ทุก ~10 นาที

## ต้องมี
- DaVinci Resolve **Studio** + `Preferences → System → General → External scripting = Local`
- Python 3 · **Windows/Linux ไม่ต้อง pip** · **macOS:** `pip install -r requirements.txt`
- Linux (ห้อง color): `libxss1` (+ option `xdotool`) · X11 เท่านั้น

## env (ตั้งต่อเครื่อง)
```
EDITORTRACK_ROLE=conform          # ห้อง edit · ห้อง color = colorist
EDITORTRACK_DETECT_GRADE=0        # ห้อง edit=0 (ไม่สแกนสี) · ห้อง color=1
EDITORTRACK_SERVER=http://<server>:8000/ingest
EDITORTRACK_TOKEN=<token>
# Resolve scripting (per OS — ดู ../DATAFLOW.md §Config)
RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB / PYTHONPATH
```

## run
```
python probe.py       # ตรวจ API ต่อรุ่น Resolve (ครั้งเดียวตอนตั้งค่า)
python resolve_poller.py         # ดู snapshot จริง
python activity_sampler.py --watch   # ดู idle/frontmost สด
python runner.py      # 1 รอบ (เทส) → แล้วตั้ง scheduler ทุก 10 นาที
```
scheduler: macOS=LaunchAgent · Windows=Task Scheduler · Linux=cron (ดู ../DATAFLOW.md §Deploy)

## ไฟล์
`resolve_poller.py` A1 · `activity_sampler.py` A2 · `scope.py` A5(%) · `joblink.py` job · `runner.py` A3 · `probe.py` diagnostic
