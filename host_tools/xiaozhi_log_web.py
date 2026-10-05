#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import json as _ops_json
import os as _ops_os
import shutil as _ops_shutil
import subprocess as _ops_sp

def _ops_run(cmd, timeout=40):
    p = _ops_sp.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    return out.strip()[:3500]

def _ops_handle(action, value=""):
    action = (action or "").strip().lower()
    value = (value or "").strip()
    MUSIC_CACHE = "/opt/xiaozhi-server/music/cache"
    CONTAINER = "xiaozhi-esp32-server"
    if action == "status":
        parts = [
            "=== uptime ===", _ops_run("uptime"),
            "=== memory ===", _ops_run("free -h"),
            "=== disk ===", _ops_run("df -h / /opt 2>/dev/null | head -10"),
            "=== container ===",
            _ops_run("docker ps --filter name=%s --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'" % CONTAINER),
        ]
        return {"ok": True, "text": "\n".join(parts)}
    if action == "disk":
        return {"ok": True, "text": _ops_run("df -h; echo; du -sh /opt/xiaozhi-server/music/cache 2>/dev/null || true")}
    if action == "memory":
        return {"ok": True, "text": _ops_run("free -h; echo; top -b -n1 | head -15")}
    if action == "containers":
        return {"ok": True, "text": _ops_run("docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'")}
    if action == "logs":
        try:
            n = max(20, min(int(value or 80), 200))
        except Exception:
            n = 80
        return {"ok": True, "text": _ops_run("docker logs --tail %d %s 2>&1" % (n, CONTAINER))}
    if action == "clear_music_cache":
        if not _ops_os.path.isdir(MUSIC_CACHE):
            return {"ok": False, "text": "缓存目录不存在: " + MUSIC_CACHE}
        removed = 0
        bytes_freed = 0
        for name in _ops_os.listdir(MUSIC_CACHE):
            path = _ops_os.path.join(MUSIC_CACHE, name)
            try:
                if _ops_os.path.isfile(path):
                    bytes_freed += _ops_os.path.getsize(path)
                    _ops_os.remove(path)
                    removed += 1
                elif _ops_os.path.isdir(path):
                    for root, _, files in _ops_os.walk(path):
                        for f in files:
                            fp = _ops_os.path.join(root, f)
                            try:
                                bytes_freed += _ops_os.path.getsize(fp)
                            except Exception:
                                pass
                    _ops_shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except Exception:
                continue
        return {"ok": True, "text": "已清理音乐缓存约 %d 项，释放约 %.1f MB" % (removed, bytes_freed / 1024 / 1024)}
    if action == "restart":
        name = value or CONTAINER
        if name not in {CONTAINER, "xiaozhi-esp32-server", "xiaozhi-server"}:
            return {"ok": False, "text": "不允许重启未知容器: " + name}
        _ops_sp.Popen("sleep 1; docker restart " + name, shell=True, stdout=_ops_sp.DEVNULL, stderr=_ops_sp.DEVNULL)
        return {"ok": True, "text": "已触发重启 " + name}
    return {"ok": False, "text": "未知 action: " + action}

import os, time, json, subprocess, html, re, urllib.parse

PORT = 10009
BUILD_LOG = "/tmp/xiaozhi-go.log"

CSS = """
:root {
  --bg:#0a0e14; --panel:#10161f; --line:rgba(255,255,255,.07); --text:#e6edf7;
  --muted:#7d8aa3; --ok:#3ecf8e; --err:#ff6b7a; --warn:#e6b450; --evt:#6aa8ff;
  --cmd:#c9a27a; --accent:#4d8bff; --rail:rgba(125,138,163,.38);
}
* { box-sizing:border-box; }
html,body { height:100%; }
body {
  margin:0; color:var(--text);
  background:
    radial-gradient(900px 480px at 8% -8%, rgba(77,139,255,.14), transparent 55%),
    radial-gradient(700px 420px at 100% 0%, rgba(62,207,142,.08), transparent 50%),
    linear-gradient(180deg, #0a0e14, #0c1118 60%, #0a0e14);
  font:13px/1.45 "Segoe UI","PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif;
}
* { scrollbar-width: thin; scrollbar-color: var(--rail) transparent; }
*::-webkit-scrollbar { width:4px; height:4px; }
*::-webkit-scrollbar-button { display:none; width:0; height:0; }
*::-webkit-scrollbar-track { background:transparent; }
*::-webkit-scrollbar-thumb { background:var(--rail); border-radius:8px; min-height:24px; }
*::-webkit-scrollbar-thumb:hover { background:rgba(125,138,163,.62); }
*::-webkit-scrollbar-corner { background:transparent; }
.wrap { max-width:1320px; margin:0 auto; padding:16px 14px 24px; }
.top { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:flex-end; margin-bottom:12px; }
h1 { margin:0; font-size:20px; letter-spacing:-.02em; font-weight:700; }
.sub { color:var(--muted); margin-top:3px; font-size:12px; }
.badge {
  display:inline-flex; align-items:center; gap:7px; padding:6px 11px; border-radius:999px;
  background:rgba(62,207,142,.08); border:1px solid rgba(62,207,142,.22);
  font-size:11.5px; font-weight:650; color:#b7f0d2;
}
.badge i { width:6px; height:6px; border-radius:50%; background:var(--ok); box-shadow:0 0 0 3px rgba(62,207,142,.14); }
.status { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:10px; }
.st {
  display:inline-flex; align-items:center; gap:6px; padding:5px 9px; border-radius:8px;
  font-size:11.5px; font-weight:650; border:1px solid var(--line); background:rgba(255,255,255,.025); color:var(--muted);
}
.st i { width:6px; height:6px; border-radius:50%; background:#5a6578; }
.st.on { color:#cfe4ff; border-color:rgba(77,139,255,.28); background:rgba(77,139,255,.08); }
.st.on i { background:var(--ok); }
.st.off { color:#ffb0b8; border-color:rgba(255,107,122,.25); }
.st.off i { background:var(--err); }
.chips { display:flex; flex-wrap:wrap; gap:7px; margin-bottom:10px; }
.chip { background:rgba(255,255,255,.025); border:1px solid var(--line); border-radius:9px; padding:7px 9px; min-width:132px; }
.chip b { display:block; font-size:11.5px; }
.chip span { color:var(--muted); font-size:11px; }
.toolbar { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
.tabs { display:flex; gap:6px; flex-wrap:wrap; }
.tabs a {
  text-decoration:none; color:var(--muted); border:1px solid var(--line); background:rgba(255,255,255,.02);
  padding:6px 11px; border-radius:8px; font-weight:650; font-size:12px;
}
.tabs a.on { color:#fff; background:rgba(77,139,255,.2); border-color:rgba(77,139,255,.35); }
.actions { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
.actions form { margin:0; }
button, .link {
  appearance:none; border:1px solid var(--line); background:rgba(255,255,255,.03);
  color:var(--text); padding:6px 10px; border-radius:8px; font:inherit; font-weight:650;
  cursor:pointer; text-decoration:none; display:inline-block; font-size:12px;
}
button:hover, .link:hover { background:rgba(255,255,255,.07); }
button.pri { background:rgba(77,139,255,.88); border-color:transparent; color:#fff; }
.toggle { display:inline-flex; align-items:center; gap:6px; color:var(--muted); font-size:12px; user-select:none; }
.toggle input { accent-color:var(--accent); }
.panel {
  background:linear-gradient(180deg, rgba(16,22,31,.96), rgba(12,17,24,.98));
  border:1px solid var(--line); border-radius:12px; overflow:hidden; box-shadow:0 18px 40px rgba(0,0,0,.28);
}
.phd {
  display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;
  padding:9px 12px; border-bottom:1px solid var(--line); background:rgba(255,255,255,.015);
  font-size:12px; font-weight:650;
}
.phd span:last-child { color:var(--muted); font-weight:500; }
.log {
  font-family: ui-monospace,"Cascadia Mono","SF Mono",Consolas,monospace;
  font-size:12px; max-height:calc(100vh - 250px); min-height:460px;
  overflow:auto; background:#070a10; color:#d3dcec;
  scrollbar-width: thin; scrollbar-color: var(--rail) transparent;
}
.log::-webkit-scrollbar { width:3px; height:3px; }
.log::-webkit-scrollbar-button { display:none; width:0; height:0; }
.log::-webkit-scrollbar-track { background:transparent; margin:4px 0; }
.log::-webkit-scrollbar-thumb { background:rgba(125,138,163,.35); border-radius:8px; }
.log::-webkit-scrollbar-thumb:hover { background:rgba(125,138,163,.55); }
.ln { display:grid; grid-template-columns:42px 1fr; gap:8px; padding:1px 12px; border-left:2px solid transparent; }
.ln:hover { background:rgba(255,255,255,.025); }
.ln .n { color:#4d5a70; text-align:right; user-select:none; font-size:10.5px; padding-top:2px; }
.ln code { white-space:pre-wrap; word-break:break-word; font-family:inherit; }
.ln.ok { border-left-color:rgba(62,207,142,.5); }
.ln.ok code { color:#8fe8bf; }
.ln.err { border-left-color:rgba(255,107,122,.65); background:rgba(255,107,122,.045); }
.ln.err code { color:#ff9aa5; }
.ln.warn { border-left-color:rgba(230,180,80,.45); }
.ln.warn code { color:#f0d08a; }
.ln.evt code { color:#9cc8ff; }
.ln.cmd code { color:#e0c2a0; }
.ln.muted code { color:#66758f; }
.toast {
  margin-bottom:10px; padding:9px 11px; border-radius:9px;
  border:1px solid rgba(77,139,255,.3); background:rgba(77,139,255,.1);
  font-weight:650; font-size:12.5px;
}
.foot { margin-top:10px; color:var(--muted); font-size:11.5px; display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; }
.foot a { color:#9bb7e8; text-decoration:none; }
"""


def sh(cmd, timeout=25):
    try:
        out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=timeout)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output or str(e)
    except Exception as e:
        return 1, str(e)


def docker_logs(name, n=220):
    code, out = sh(f"docker logs --tail {n} {name} 2>&1")
    return out


def read_build_log(max_bytes=400000):
    if not os.path.exists(BUILD_LOG):
        return "", 0, "-"
    size = os.path.getsize(BUILD_LOG)
    mtime = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(BUILD_LOG)))
    with open(BUILD_LOG, "rb") as f:
        if size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
            nl = data.find(b"\n")
            if nl != -1:
                data = data[nl + 1 :]
            return data.decode("utf-8", "replace"), size, mtime
        return f.read().decode("utf-8", "replace"), size, mtime


def human_size(n):
    n = float(n)
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{int(n)}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def strip_ansi(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def line_kind(s):
    if re.search(r"ERROR|FATAL|Traceback|Exception|failed|失败|EOFError|没有找到对应的函数", s, re.I):
        return "err"
    if re.search(r"WARN|warning|警告|重试", s, re.I):
        return "warn"
    if re.search(r"DONE|成功|WEB_OK|BASE_OK|Started|初始化组件|Websocket地址|Server is running|准备注册配置函数", s, re.I):
        return "ok"
    if re.search(r"识别文本|点歌|JuiceMusic|发送音频|关键词命中|intent|新闻|天气|搜索", s, re.I):
        return "evt"
    if s.startswith("+ ") or s.startswith("++ "):
        return "cmd"
    return "plain"


def render_lines(text, limit=1000):
    lines = text.splitlines()
    truncated = False
    if len(lines) > limit:
        lines = lines[-limit:]
        truncated = True
    parts = []
    if truncated:
        parts.append('<div class="ln muted"><span class="n">…</span><code>仅显示最近日志</code></div>')
    if not lines:
        parts.append('<div class="ln muted"><span class="n">-</span><code>暂无日志</code></div>')
    for i, raw in enumerate(lines, 1):
        k = line_kind(raw)
        clean = strip_ansi(raw)
        parts.append(
            f'<div class="ln {k}"><span class="n">{i}</span><code>{html.escape(clean)}</code></div>'
        )
    return "\n".join(parts)


def containers():
    code, out = sh("docker ps --format '{{.Names}}|{{.Status}}' | grep xiaozhi || true")
    rows = []
    for line in out.splitlines():
        if "|" in line:
            n, s = line.split("|", 1)
            rows.append((n.strip(), s.strip()))
    return rows


def port_ok(port):
    code, out = sh(f"ss -lntp | grep ':{port} ' || true")
    return bool(out.strip())


def get_tab_data(tab):
    tab = tab if tab in ("server", "web", "build") else "server"
    if tab == "server":
        text = docker_logs("xiaozhi-esp32-server", 220)
        title = "Server 实时日志"
        meta = "docker logs · xiaozhi-esp32-server"
        size, mtime = len(text.encode()), time.strftime("%H:%M:%S")
    elif tab == "web":
        text = docker_logs("xiaozhi-esp32-server-web", 180)
        title = "智控台 Web 日志"
        meta = "docker logs · xiaozhi-esp32-server-web"
        size, mtime = len(text.encode()), time.strftime("%H:%M:%S")
    else:
        text, size, mtime = read_build_log()
        title = "构建 / 部署日志"
        meta = BUILD_LOG
    return tab, text, title, meta, size, mtime


def status_html():
    bits = [("WS :8000", port_ok(8000)), ("OTA :8002", port_ok(8002)), ("LOG :10009", port_ok(10009))]
    return "".join(
        f'<span class="st {"on" if ok else "off"}"><i></i>{html.escape(name)}</span>'
        for name, ok in bits
    )


def chips_html():
    rows = containers()
    return "".join(
        f'<div class="chip"><b>{html.escape(n.replace("xiaozhi-esp32-server-", "").replace("xiaozhi-esp32-", ""))}</b><span>{html.escape(s)}</span></div>'
        for n, s in rows
    ) or '<div class="chip"><b>无容器</b><span>-</span></div>'


def fragment(tab):
    tab, text, title, meta, size, mtime = get_tab_data(tab)
    return {
        "tab": tab,
        "title": title,
        "meta": f"{meta} · {human_size(size)} · {mtime}",
        "html": render_lines(text),
        "status": status_html(),
        "chips": chips_html(),
        "clock": time.strftime("%H:%M:%S"),
    }


def page(tab="server", msg=""):
    data = fragment(tab)
    msg_html = (
        f'<div class="toast" id="toast">{html.escape(msg)}</div>'
        if msg
        else '<div class="toast" id="toast" style="display:none"></div>'
    )
    tab_json = json.dumps(tab)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>小智日志 · 10009</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1>小智运行日志</h1>
      <div class="sub">端口 10009 · 静默刷新 · 保留滚动位置</div>
    </div>
    <div class="badge"><i></i>LIVE · <span id="clock">{html.escape(data['clock'])}</span></div>
  </div>
  {msg_html}
  <div class="status" id="status">{data['status']}</div>
  <div class="chips" id="chips">{data['chips']}</div>
  <div class="toolbar">
    <div class="tabs">
      <a class="{'on' if tab=='server' else ''}" href="/?tab=server">Server</a>
      <a class="{'on' if tab=='web' else ''}" href="/?tab=web">智控台</a>
      <a class="{'on' if tab=='build' else ''}" href="/?tab=build">构建日志</a>
    </div>
    <div class="actions">
      <label class="toggle"><input type="checkbox" id="follow" checked/>跟随底部</label>
      <button type="button" id="btnRefresh">刷新</button>
      <form method="post" action="/action"><input type="hidden" name="a" value="restart_server"/><input type="hidden" name="tab" value="{tab}"/><button class="pri" type="submit">重启 Server</button></form>
      <form method="post" action="/action"><input type="hidden" name="a" value="restart_web"/><input type="hidden" name="tab" value="{tab}"/><button type="submit">重启智控台</button></form>
      <a class="link" href="/raw?tab={tab}" target="_blank">纯文本</a>
      <a class="link" href="http://154.64.254.253:8002/" target="_blank">智控台</a>
    </div>
  </div>
  <div class="panel">
    <div class="phd">
      <span id="title">{html.escape(data['title'])}</span>
      <span id="meta">{html.escape(data['meta'])}</span>
    </div>
    <div class="log" id="log">{data['html']}</div>
  </div>
  <div class="foot">
    <span>WS ws://154.64.254.253:8000/xiaozhi/v1/ · OTA http://154.64.254.253:8002/xiaozhi/ota/</span>
    <span>AJAX 静默刷新 4s · 3px 滚动条</span>
  </div>
</div>
<script>
(function(){{
  const tab = {tab_json};
  const el = document.getElementById('log');
  const follow = document.getElementById('follow');
  let busy = false;
  let userScrolling = false;
  let scrollTimer = null;

  function nearBottom() {{
    if (!el) return true;
    return (el.scrollHeight - el.scrollTop - el.clientHeight) < 48;
  }}
  function stick() {{
    if (el && follow && follow.checked && !userScrolling) el.scrollTop = el.scrollHeight;
  }}
  if (el) {{
    el.addEventListener('scroll', function() {{
      userScrolling = true;
      if (follow) follow.checked = nearBottom();
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(function(){{ userScrolling = false; }}, 800);
    }}, {{passive:true}});
  }}
  stick();

  async function silentRefresh() {{
    if (busy) return;
    busy = true;
    const prevTop = el ? el.scrollTop : 0;
    const wasFollow = follow && follow.checked;
    try {{
      const r = await fetch('/api/logs?tab=' + encodeURIComponent(tab), {{cache:'no-store'}});
      if (!r.ok) return;
      const data = await r.json();
      if (el) el.innerHTML = data.html;
      const t = document.getElementById('title'); if (t) t.textContent = data.title;
      const m = document.getElementById('meta'); if (m) m.textContent = data.meta;
      const s = document.getElementById('status'); if (s) s.innerHTML = data.status;
      const c = document.getElementById('chips'); if (c) c.innerHTML = data.chips;
      const clock = document.getElementById('clock'); if (clock) clock.textContent = data.clock;
      const toast = document.getElementById('toast');
      if (toast && toast.style.display !== 'none' && toast.textContent) {{
        setTimeout(function(){{ toast.style.display='none'; }}, 2500);
      }}
      if (wasFollow) stick();
      else if (el) el.scrollTop = prevTop;
    }} catch (e) {{}}
    finally {{ busy = false; }}
  }}
  document.getElementById('btnRefresh').addEventListener('click', silentRefresh);
  setInterval(silentRefresh, 4000);
}})();
</script>
</body></html>"""


def action(name):
    if name == "refresh":
        return True, "已刷新"
    if name == "restart_server":
        code, out = sh("docker restart xiaozhi-esp32-server 2>&1")
        return True, (out.strip() or "server restarted")
    if name == "restart_web":
        code, out = sh("docker restart xiaozhi-esp32-server-web 2>&1")
        return True, (out.strip() or "web restarted")
    return False, "未知操作"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        tab = q.get("tab", ["server"])[0]
        if u.path == "/api/logs":
            self._send(200, json.dumps(fragment(tab), ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path == "/raw":
            tab, text, *_rest = get_tab_data(tab)
            self._send(200, strip_ansi(text), "text/plain; charset=utf-8")
            return
        msg = q.get("msg", [""])[0]
        self._send(200, page(tab, msg))

    def do_POST(self):
        try:
            from urllib.parse import urlparse as _up
            _path = _up(self.path).path
        except Exception:
            _path = self.path.split("?", 1)[0]
        if _path in ("/api/ops", "/ops"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                data = _ops_json.loads(raw.decode("utf-8", "replace") or "{}")
                result = _ops_handle(str(data.get("action") or ""), str(data.get("value") or ""))
                body = _ops_json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = _ops_json.dumps({"ok": False, "text": str(e)}, ensure_ascii=False).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return

        u = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = urllib.parse.parse_qs(raw)
        if u.path == "/action":
            a = form.get("a", [""])[0]
            tab = form.get("tab", ["server"])[0]
            ok, msg = action(a)
            loc = f"/?tab={urllib.parse.quote(tab)}&msg={urllib.parse.quote(msg)}"
            self.send_response(303)
            self.send_header("Location", loc)
            self.end_headers()
            return
        self._send(404, "not found")


if __name__ == "__main__":
    print("listening", PORT, flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
