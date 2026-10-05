#!/usr/bin/env python3
"""Host-side memory writer: bypass broken saveMemory oauth2 on manager-api."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import subprocess
import urllib.parse

PORT = 18082
SECRET = os.environ.get("XIAOZHI_SERVER_SECRET", "change-me")
MYSQL = [
    "docker", "exec", "-i", "xiaozhi-esp32-server-db",
    "mysql", "-uroot", f"-p{os.environ.get('XIAOZHI_MYSQL_ROOT_PASSWORD', '123456')}",
    "--default-character-set=utf8mb4",
    "xiaozhi_esp32_server",
]


def sql_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "''")


def run_sql(sql: str) -> tuple[int, str]:
    try:
        p = subprocess.run(
            MYSQL,
            input=sql.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
        if "Using a password" in err:
            err = "\n".join(x for x in err.splitlines() if "Using a password" not in x)
        return p.returncode, (out + err).strip()
    except Exception as e:
        return 1, str(e)


def save_memory(mac: str, summary: str) -> tuple[bool, str]:
    mac = (mac or "").strip()
    if not mac:
        return False, "empty mac"
    summary = summary or ""
    # find agent_id by device mac
    code, out = run_sql(
        f"SELECT agent_id FROM ai_device WHERE mac_address='{sql_escape(mac)}' LIMIT 1;"
    )
    if code != 0:
        return False, f"sql error: {out}"
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    # skip header if any
    agent_id = None
    for line in lines:
        if line.lower() == "agent_id":
            continue
        agent_id = line
        break
    if not agent_id:
        return False, f"device not found: {mac}"
    code, out = run_sql(
        "UPDATE ai_agent SET summary_memory='%s', updated_at=NOW() WHERE id='%s';"
        % (sql_escape(summary), sql_escape(agent_id))
    )
    if code != 0:
        return False, f"update failed: {out}"
    return True, f"ok agent={agent_id} chars={len(summary)}"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def _json(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth_ok(self) -> bool:
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            return auth[7:].strip() == SECRET
        return False

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {"status": "ok", "service": "xiaozhi-mem-writer"})
            return
        self._json(404, {"status": "error", "message": "not found"})

    def do_PUT(self):
        if not self._auth_ok():
            self._json(401, {"code": 401, "msg": "Unauthorized", "data": None})
            return
        u = urllib.parse.urlparse(self.path)
        m = re.match(r"^/agent/saveMemory/(.+)$", u.path)
        if not m:
            self._json(404, {"code": 404, "msg": "not found", "data": None})
            return
        mac = urllib.parse.unquote(m.group(1))
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else "{}"
        try:
            data = json.loads(body or "{}")
        except Exception:
            data = {}
        summary = data.get("summaryMemory") or data.get("summary_memory") or ""
        ok, msg = save_memory(mac, summary)
        if ok:
            self._json(200, {"code": 0, "msg": "success", "data": msg})
        else:
            self._json(200, {"code": 500, "msg": msg, "data": None})


if __name__ == "__main__":
    print("mem-writer listening", PORT, flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
