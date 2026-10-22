#!/usr/bin/env python3
"""ออก/ถอน token ของ agent ต่อเครื่อง และ user สำหรับอ่านหน้า dashboard — รันบนเครื่อง server

  python manage_tokens.py issue grade-03          # ออก token ให้ host (แสดงครั้งเดียว) → ใส่ใน env.sh ของเครื่องนั้น
  python manage_tokens.py issue grade-03 --force  # ออกใหม่ทับ (token เดิมใช้ไม่ได้ทันที)
  python manage_tokens.py list                    # host ที่มี token (ไม่แสดงตัว token)
  python manage_tokens.py revoke grade-03         # ถอน token → เครื่องนั้นส่งไม่ได้ทันที
  python manage_tokens.py read-user director      # ตั้ง/เปลี่ยนรหัสผ่าน user ที่อ่าน dashboard (ถามรหัส)
  python manage_tokens.py read-user-remove director
  python manage_tokens.py read-users

ไฟล์: EDITORTRACK_TOKENS_FILE (default /srv/editortrack/agent_tokens.json) · EDITORTRACK_READ_USERS_FILE
(default /srv/editortrack/read_users.json) — สิทธิ์ 600 เก็บแค่ hash · ไม่ต้อง restart server
"""
import argparse
import getpass
import sys

import auth


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("issue")
    p.add_argument("host")
    p.add_argument("--force", action="store_true")
    sub.add_parser("list")
    p = sub.add_parser("revoke")
    p.add_argument("host")
    p = sub.add_parser("read-user")
    p.add_argument("name")
    p.add_argument("--password-stdin", action="store_true", help="อ่านรหัสจาก stdin (สคริปต์)")
    p = sub.add_parser("read-user-remove")
    p.add_argument("name")
    sub.add_parser("read-users")
    a = ap.parse_args(argv)

    if a.cmd == "issue":
        try:
            tok = auth.issue_token(a.host, force=a.force)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1
        print(f"token ของ {a.host} (แสดงครั้งเดียว — server เก็บแค่ hash ใน {auth.tokens_file()}):")
        print(tok)
        print(f"→ ใส่ในเครื่อง {a.host}: export EDITORTRACK_TOKEN={tok}   (deploy/env.sh · อย่าส่งทางแชต)")
    elif a.cmd == "list":
        hosts = (auth._load(auth.tokens_file()).get("hosts") or {})
        for h in sorted(hosts):
            v = hosts[h]
            print(f"{h}\t{(v.get('created') if isinstance(v, dict) else '') or ''}")
        if not hosts:
            print(f"(ยังไม่มี token — {auth.tokens_file()})")
    elif a.cmd == "revoke":
        print("✓ ถอนแล้ว" if auth.revoke_token(a.host) else f"✗ {a.host} ไม่มี token")
    elif a.cmd == "read-user":
        if a.password_stdin:
            pw = sys.stdin.readline().rstrip("\n")
        else:
            pw = getpass.getpass(f"รหัสผ่านของ {a.name}: ")
            if pw != getpass.getpass("ยืนยัน: "):
                print("✗ ไม่ตรงกัน", file=sys.stderr)
                return 1
        if len(pw) < 10:
            print("✗ รหัสผ่านต้องยาวอย่างน้อย 10 ตัว", file=sys.stderr)
            return 1
        auth.set_read_user(a.name, pw)
        print(f"✓ ตั้ง user {a.name} แล้ว ({auth.read_users_file()})")
    elif a.cmd == "read-user-remove":
        print("✓ ลบแล้ว" if auth.remove_read_user(a.name) else f"✗ ไม่มี user {a.name}")
    elif a.cmd == "read-users":
        for n in sorted(auth.read_users()):
            print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
