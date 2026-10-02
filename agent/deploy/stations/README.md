# Station templates — 7 workstation

ก็อปไฟล์ของเครื่องนั้นเป็น `deploy/env.sh` แล้วแก้ 2 ค่า (`SERVER_IP`, `TOKEN`) → `sh deploy/install.sh`

| hostname | ไฟล์ | stage | DETECT_GRADE | OS | room (ERP) |
|---|---|---|---|---|---|
| grade-01..06 | `grade-0N.sh` | color | 1 | Linux | GRADE1..6 |
| conform-01 | `conform-01.sh` | conform | 0 | Mac | CONFORM1 |

> โค้ดทุกเครื่องเหมือนกัน — ต่างแค่ไฟล์ env นี้ + **hostname** (ตั้งที่ OS, อยู่ใน comment หัวไฟล์)

## ขั้นตอนต่อเครื่อง
```sh
# ตัวอย่าง grade-03
sudo hostnamectl set-hostname grade-03          # Mac: sudo scutil --set HostName conform-01
cp deploy/stations/grade-03.sh deploy/env.sh
nano deploy/env.sh                               # แก้ SERVER_IP + TOKEN
sh deploy/install.sh                             # หลายกะ: sh deploy/install.sh --system
```

## ฝั่ง server (ครั้งเดียว)
ก็อป `server/room_map.example.json` → `~/.editortrack/room_map.json` แล้วแก้ค่าขวา (`GRADE1`...)
ให้ตรง **resource_code จริงของห้องใน ERP** · server ใช้ map นี้ join `host → ห้อง → booking → job`

## การแยก (สรุป)
- **เครื่องไหน** = hostname (auto)
- **ทำ stage อะไร** = `EDITORTRACK_STAGE` ในไฟล์นี้
- **งานไหน** = server join host+เวลา กับ ERP booking
- worker ไม่ต้องเลือกอะไร (zero-touch)
