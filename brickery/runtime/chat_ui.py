"""chat_ui.py —— 聊天界面（随朴 2026-08-15 落地）。

本地 web 聊天界面，走引擎路由（EngineRouter），工坊蓝图风，版面照搬
Shadeling 形态。未配置引擎时引导跳转安装引导页（setup_wizard 18766）。

- 服务：127.0.0.1:18767
- 路由：
  - GET  /                 聊天页
  - POST /api/chat          {messages:[{role,content}]} -> {reply}
  - GET  /api/engine        引擎状态（configured / backend / guide_url）
  - GET  /api/sessions      会话列表
  - POST /api/sessions      {title} -> 新建会话
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

from . import config as _config
from .engine_providers import EngineProviderRegistry
from .engine_router import EngineRouter, NoEngineConfigured

HOST = "127.0.0.1"
PORT = 18767
GUIDE_URL = "http://127.0.0.1:18766"

PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>随朴 · 聊天</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --line: #2d333b;
    --ink: #e6edf3; --dim: #8b949e; --accent: #ff7a18; --cyan: #39c5cf;
    --grid: rgba(57, 197, 207, 0.06); --user: #1f6feb; --assistant: #161b22;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    background:
      linear-gradient(var(--grid) 1px, transparent 1px),
      linear-gradient(90deg, var(--grid) 1px, transparent 1px),
      var(--bg);
    background-size: 24px 24px;
    color: var(--ink);
    font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
    display: flex; flex-direction: column; height: 100vh;
  }
  header {
    border-bottom: 2px solid var(--accent); padding: 12px 20px;
    display: flex; justify-content: space-between; align-items: center;
    background: rgba(13,17,23,0.9);
  }
  header h1 { font-size: 16px; letter-spacing: 2px; }
  header h1 b { color: var(--accent); }
  #engineBadge { font-size: 11px; color: var(--dim); }
  #engineBadge.ok { color: #3fb950; }
  #engineBadge.err { color: #f85149; }
  #messages { flex: 1; overflow-y: auto; padding: 20px; max-width: 860px; width: 100%; margin: 0 auto; }
  .msg { display: flex; margin-bottom: 16px; }
  .msg .bubble {
    max-width: 78%; padding: 10px 14px; border-radius: 6px;
    font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
  }
  .msg.user { justify-content: flex-end; }
  .msg.user .bubble { background: var(--user); color: #fff; }
  .msg.assistant .bubble { background: var(--assistant); border: 1px solid var(--line); }
  .msg .who { font-size: 10px; color: var(--dim); margin-bottom: 4px; }
  .msg.user .who { text-align: right; }
  #inputBar {
    border-top: 1px solid var(--line); padding: 14px 20px;
    background: rgba(13,17,23,0.9); max-width: 860px; width: 100%; margin: 0 auto;
  }
  #inputRow { display: flex; gap: 10px; }
  #input {
    flex: 1; background: #0d1117; border: 1px solid var(--line); color: var(--ink);
    padding: 10px 12px; border-radius: 4px; font-family: inherit; font-size: 13px;
    resize: none; height: 44px;
  }
  #input:focus { outline: none; border-color: var(--accent); }
  #send {
    background: var(--accent); color: #0d1117; border: none; padding: 0 22px;
    border-radius: 4px; font-family: inherit; font-size: 13px; font-weight: 700; cursor: pointer;
  }
  #send:disabled { opacity: 0.4; cursor: not-allowed; }
  #guide {
    display: none; text-align: center; padding: 8px; font-size: 12px;
    background: rgba(248,81,73,0.1); border: 1px solid #f85149; border-radius: 4px; margin-bottom: 10px;
  }
  #guide a { color: var(--accent); }
  .typing { color: var(--dim); font-size: 12px; padding: 4px 0; }
</style>
</head>
<body>
<header>
  <h1>随朴 <b>CHAT</b></h1>
  <div id="engineBadge">检测引擎...</div>
</header>
<div id="messages"></div>
<div id="inputBar">
  <div id="guide">引擎未配置，请先前往 <a href="http://127.0.0.1:18766" target="_blank">安装引导</a> 完成配置。</div>
  <div id="inputRow">
    <textarea id="input" placeholder="输入消息，Enter 发送，Shift+Enter 换行"></textarea>
    <button id="send">发送</button>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
const messages = $("messages"), input = $("input"), send = $("send");
let history = [];

function addMsg(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  const who = role === "user" ? "你" : "随朴";
  div.innerHTML = '<div><div class="who">' + who + '</div><div class="bubble"></div></div>';
  div.querySelector(".bubble").textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

async function jget(url) {
  const r = await fetch(url); return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
  return r.json();
}

async function checkEngine() {
  const e = await jget("/api/engine");
  const badge = $("engineBadge");
  if (e.configured) {
    badge.textContent = "引擎就绪 · " + (e.backend === "local" ? "本地 GGUF" : "API");
    badge.className = "ok";
    $("guide").style.display = "none";
  } else {
    badge.textContent = "引擎未配置";
    badge.className = "err";
    $("guide").style.display = "block";
  }
}

async function sendMsg() {
  const text = input.value.trim();
  if (!text || send.disabled) return;
  input.value = "";
  addMsg("user", text);
  history.push({ role: "user", content: text });
  const typing = document.createElement("div");
  typing.className = "typing"; typing.textContent = "思考中...";
  messages.appendChild(typing);
  messages.scrollTop = messages.scrollHeight;
  send.disabled = true;
  try {
    const r = await jpost("/api/chat", { messages: history });
    typing.remove();
    if (r.ok) {
      addMsg("assistant", r.reply);
      history.push({ role: "assistant", content: r.reply });
    } else {
      addMsg("assistant", "错误：" + (r.error || "未知错误"));
      if (r.guide) { $("guide").style.display = "block"; $("engineBadge").className = "err"; }
    }
  } catch (err) {
    typing.remove();
    addMsg("assistant", "网络错误：" + err);
  } finally {
    send.disabled = false;
    input.focus();
  }
}

send.onclick = sendMsg;
input.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); }
});
checkEngine();
input.focus();
</script>
</body>
</html>
"""


def _load() -> _config.Config:
    return _config.load_config()


def _make_router(cfg: _config.Config) -> EngineRouter:
    local = EngineProviderRegistry.build("local", cfg.engine)
    api = EngineProviderRegistry.build("api", cfg.engine)
    return EngineRouter(cfg.engine, local_engine=local, api_engine=api)


def _engine_configured(cfg: _config.Config) -> bool:
    eng = cfg.engine
    if eng.backend == "local":
        return bool(eng.local_model)
    return bool(eng.api_url and eng.api_key)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默访问日志
        pass

    def _send(self, code: int, obj: Dict, ctype: str = "application/json") -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8") if ctype == "application/json" else obj
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype == "text/html" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(200, PAGE_HTML.encode("utf-8"), "text/html")
        elif self.path == "/api/engine":
            cfg = _load()
            self._send(200, {
                "ok": True,
                "configured": _engine_configured(cfg),
                "backend": cfg.engine.backend,
                "guide_url": GUIDE_URL,
            })
        elif self.path == "/api/sessions":
            self._send(200, {"ok": True, "sessions": []})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        data = self._read_json()
        if self.path == "/api/chat":
            self._chat(data)
        elif self.path == "/api/sessions":
            self._send(200, {"ok": True, "session": {"id": "local", "title": data.get("title", "新会话")}})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def _chat(self, data: Dict) -> None:
        messages = data.get("messages") or []
        if not messages:
            self._send(400, {"ok": False, "error": "缺少 messages"})
            return
        cfg = _load()
        if not _engine_configured(cfg):
            self._send(200, {"ok": False, "error": "引擎未配置", "guide": True})
            return
        # 把多轮对话序列化为 prompt（纯文本补全，无独立多轮 API）
        lines = []
        for m in messages:
            role = "用户" if m.get("role") == "user" else "助手"
            lines.append(f"{role}：{m.get('content', '')}")
        prompt = "\n".join(lines) + "\n助手："
        try:
            router = _make_router(cfg)
            reply = router.complete(prompt)
            self._send(200, {"ok": True, "reply": reply})
        except NoEngineConfigured as e:
            self._send(200, {"ok": False, "error": str(e), "guide": True})
        except Exception as e:  # noqa: BLE001
            self._send(200, {"ok": False, "error": str(e)})


def serve(host: str = HOST, port: int = PORT, daemon: bool = True) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    if daemon:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
    else:
        server.serve_forever()
    return server


if __name__ == "__main__":
    print(f"随朴聊天界面：http://{HOST}:{PORT}")
    serve(daemon=False)
