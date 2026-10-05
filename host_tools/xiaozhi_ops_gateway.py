#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Host-side whitelist ops gateway for xiaozhi agent server_control."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 18083
MUSIC_CACHE = "/opt/xiaozhi-server/music/cache"
CONTAINER = "xiaozhi-esp32-server"


def run(cmd, timeout=40) -> str:
    p = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    return out.strip()[:3500]


def handle(action: str, value: str) -> dict:
    action = (action or "").strip().lower()
    value = (value or "").strip()

    if action == "status":
        parts = [
            "=== uptime ===",
            run("uptime"),
            "=== memory ===",
            run("free -h"),
            "=== disk ===",
            run("df -h / /opt 2>/dev/null | head -10"),
            "=== container ===",
            run(
                f"docker ps --filter name={CONTAINER} "
                "--format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'"
            ),
        ]
        return {"ok": True, "text": "\n".join(parts)}

    if action == "disk":
        return {"ok": True, "text": run("df -h; echo; du -sh /opt/xiaozhi-server/music/cache 2>/dev/null || true")}

    if action == "memory":
        return {"ok": True, "text": run("free -h; echo; top -b -n1 | head -15")}

    if action == "containers":
        return {"ok": True, "text": run("docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'")}

    if action == "logs":
        n = 80
        try:
            n = max(20, min(int(value or 80), 200))
        except Exception:
            n = 80
        return {"ok": True, "text": run(f"docker logs --tail {n} {CONTAINER} 2>&1")}

    if action == "clear_music_cache":
        if not os.path.isdir(MUSIC_CACHE):
            return {"ok": False, "text": f"缓存目录不存在: {MUSIC_CACHE}"}
        removed = 0
        bytes_freed = 0
        for name in os.listdir(MUSIC_CACHE):
            path = os.path.join(MUSIC_CACHE, name)
            try:
                if os.path.isfile(path):
                    bytes_freed += os.path.getsize(path)
                    os.remove(path)
                    removed += 1
                elif os.path.isdir(path):
                    # size approx
                    for root, _, files in os.walk(path):
                        for f in files:
                            fp = os.path.join(root, f)
                            try:
                                bytes_freed += os.path.getsize(fp)
                            except Exception:
                                pass
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except Exception:
                continue
        mb = bytes_freed / (1024 * 1024)
        return {"ok": True, "text": f"已清理音乐缓存文件约 {removed} 项，释放约 {mb:.1f} MB"}

    if action == "restart":
        name = value or CONTAINER
        # only allow known container names
        allowed = {CONTAINER, "xiaozhi-esp32-server", "xiaozhi-server"}
        if name not in allowed:
            return {"ok": False, "text": f"不允许重启未知容器: {name}"}
        # async-ish: restart in background so HTTP can return
        subprocess.Popen(
            f"sleep 1; docker restart {name}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "text": f"已触发重启 {name}"}

    return {"ok": False, "text": f"未知 action: {action}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[ops]", fmt % args)

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._json(200, {"ok": True, "service": "xiaozhi-ops", "port": PORT})
            return
        self._json(404, {"ok": False, "text": "not found"})

    def do_POST(self):
        if self.path != "/ops":
            self._json(404, {"ok": False, "text": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8", "replace") or "{}")
            action = data.get("action") or ""
            value = data.get("value") or ""
            result = handle(str(action), str(value))
            self._json(200, result)
        except Exception as e:
            self._json(500, {"ok": False, "text": f"{e}\n{traceback.format_exc()[:500]}"})


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"xiaozhi ops gateway on {HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
