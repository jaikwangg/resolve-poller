"""Tiny in-process server: POST /ingest and GET /agent/control, records every request.

ingest_status: int, or a callable(body) -> int | (int, headers) | "hang" (sleep `hang_sec`, then 503),
evaluated per POST, so tests can script sequences (400 for one record, 500 then 200, timeouts, ...).
A plain 400 carries the real server's refusal marker (header + body) unless reject_marker=False or the
callable returns its own headers (a "foreign" 400, e.g. from a proxy)."""
import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeServer:
    def __init__(self, control=None, ingest_status=200, hang_sec=2.0, reject_marker=True):
        self.requests = []
        self.reject_marker = reject_marker
        self.hang_sec = hang_sec
        self.control = control if control is not None else {"enabled": True, "lease_sec": 3600}
        self.ingest_status = ingest_status
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body, headers=None):
                b = json.dumps(body).encode()
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                srv.requests.append(("GET", self.path, None))
                if self.path.startswith("/agent/control"):
                    if srv.control == "down":
                        return self._reply(503, {})
                    return self._reply(200, srv.control)
                self._reply(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"null")
                srv.requests.append(("POST", self.path, body))
                st = srv.ingest_status(body) if callable(srv.ingest_status) else srv.ingest_status
                headers = None
                if isinstance(st, tuple):
                    st, headers = st
                if st == "hang":
                    time.sleep(srv.hang_sec)
                    st = 503
                body = {"ok": st == 200} if st < 400 else {"detail": "fake %d" % st}
                if st == 400 and headers is None and srv.reject_marker:     # what the real /ingest sends
                    headers = {"X-Editortrack-Reject": "bad_type:fake"}
                    body = {"detail": "record ใช้ไม่ได้ (bad_type:fake): fake 400"}
                try:
                    self._reply(st, body, headers)
                except Exception:
                    pass                    # client gave up (timeout)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    def posts(self):
        return [b for m, p, b in self.requests if m == "POST"]
