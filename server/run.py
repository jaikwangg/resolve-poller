#!/usr/bin/env python3
"""รัน server — bind 127.0.0.1 เป็น default (เข้าจาก LAN ผ่าน reverse proxy ที่มี TLS เช่น Caddy)

  python run.py                 # 127.0.0.1:8000 (แนะนำ: Caddy/nginx รับ LAN + TLS แล้ว proxy มาที่นี่)
  python run.py --lan           # 0.0.0.0:8000 — เปิดตรงให้ LAN (HTTP ไม่เข้ารหัส: token/รหัสผ่านวิ่งเป็นข้อความเปล่า)
ห้ามรัน `uvicorn dashboard_api:app` ตรงใน production (systemd unit ต้อง ExecStart=… python run.py — docs/ROLLOUT.md)
  ถ้ามีใครรันตรง: app ยัง log เตือนตอน start, หน้าอ่านยังต้อง login, READ_AUTH=none เปิดเฉพาะ connection ทาง loopback
ENV: EDITORTRACK_BIND=127.0.0.1 · EDITORTRACK_PORT=8000
     EDITORTRACK_TRUSTED_PROXIES=127.0.0.1   (IP ของ proxy ที่เชื่อ X-Forwarded-For — ใช้หา IP จริงของ agent)
"""
import ipaddress
import os
import sys


def bind_host(argv, env):
    if "--lan" in argv:
        return "0.0.0.0"
    return env.get("EDITORTRACK_BIND", "127.0.0.1")


def _loopback(h):
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return h == "localhost"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    host = bind_host(argv, os.environ)
    port = int(os.environ.get("EDITORTRACK_PORT", "8000"))
    if not _loopback(host):
        print(f"⚠️  bind {host}:{port} — เปิดให้ทั้ง LAN ผ่าน HTTP ไม่เข้ารหัส · แนะนำ bind 127.0.0.1 + reverse proxy TLS",
              file=sys.stderr)
        if (os.environ.get("EDITORTRACK_READ_AUTH") or "basic").strip().lower() == "none":
            print("✗ EDITORTRACK_READ_AUTH=none ใช้ได้เฉพาะ bind loopback (หลัง proxy ที่ทำ auth) — ไม่ start", file=sys.stderr)
            return 2
    os.environ["EDITORTRACK_VIA_RUN"] = "1"          # dashboard_api.startup_checks: รู้ว่า guard ด้านบนผ่านแล้ว
    import uvicorn
    uvicorn.run("dashboard_api:app", host=host, port=port, proxy_headers=True,
                forwarded_allow_ips=os.environ.get("EDITORTRACK_TRUSTED_PROXIES", "127.0.0.1"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
