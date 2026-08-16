"""setup_wizard.py —— 安装引导页（随朴 2026-08-15 落地）。

本地 HTTP 服务（127.0.0.1:18766），浏览器打开即用：
  - 八家 API 预设模板（火山/腾讯/DeepSeek/通义/智谱/Kimi/OpenAI/xAI）
  - 本地 GGUF 推荐下载（复用 model_catalog 下载管理器，标准库 urllib）
  - 配置写入（复用 load_config + save，不硬编码任何推理地址）
  - 配置验证（api 后端发最小请求；local 后端检查文件存在）

红线遵守：
  - 不硬编码第三方推理地址：预设 URL 仅作可编辑模板，用户点选并保存才算显式填写
  - API Key 必须用户手填，本文件不猜测、不预置
  - 本地模型仅用户主动触发下载
"""

from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

from . import config as _config
from . import model_catalog as _catalog

HOST = "127.0.0.1"
PORT = 18766

# 八家 API 预设模板：url 为可编辑默认值，key 必须用户手填
API_PRESETS: List[Dict] = [
    {"name": "火山方舟", "url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-seed-2.1-pro-260628", "key": ""},
    {"name": "腾讯混元", "url": "https://api.hunyuan.cloud.tencent.com/v1", "model": "hunyuan-turbos-latest", "key": ""},
    {"name": "DeepSeek", "url": "https://api.deepseek.com/v1", "model": "deepseek-v4-flash", "key": ""},
    {"name": "通义千问", "url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3.8-max", "key": ""},
    {"name": "智谱 GLM", "url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.2", "key": ""},
    {"name": "Kimi", "url": "https://api.moonshot.cn/v1", "model": "kimi-k3", "key": ""},
    {"name": "OpenAI", "url": "https://api.openai.com/v1", "model": "gpt-5.5", "key": ""},
    {"name": "xAI", "url": "https://api.x.ai/v1", "model": "grok-4.3", "key": ""},
]

PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>随朴 · 引擎安装引导</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --line: #2d333b;
    --ink: #e6edf3; --dim: #8b949e; --accent: #ff7a18; --cyan: #39c5cf;
    --grid: rgba(57, 197, 207, 0.06); --ok: #3fb950; --err: #f85149;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background:
      linear-gradient(var(--grid) 1px, transparent 1px),
      linear-gradient(90deg, var(--grid) 1px, transparent 1px),
      var(--bg);
    background-size: 24px 24px;
    color: var(--ink);
    font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
    min-height: 100vh; padding: 40px 20px;
  }
  .wrap { max-width: 860px; margin: 0 auto; }
  header { border-bottom: 2px solid var(--accent); padding-bottom: 14px; margin-bottom: 28px; }
  header h1 { font-size: 22px; letter-spacing: 2px; }
  header h1 b { color: var(--accent); }
  header .sub { color: var(--dim); font-size: 12px; margin-top: 6px; }
  .card {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 6px; padding: 20px; margin-bottom: 20px;
    box-shadow: 0 0 0 1px rgba(255,122,24,0.05);
  }
  .card h2 { font-size: 14px; color: var(--cyan); margin-bottom: 14px; letter-spacing: 1px; }
  .card h2::before { content: "▸ "; color: var(--accent); }
  label { display: block; font-size: 12px; color: var(--dim); margin: 10px 0 4px; }
  input, select {
    width: 100%; background: #0d1117; border: 1px solid var(--line);
    color: var(--ink); padding: 8px 10px; border-radius: 4px;
    font-family: inherit; font-size: 13px;
  }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }
  .btn {
    display: inline-block; background: var(--accent); color: #0d1117;
    border: none; padding: 10px 22px; border-radius: 4px;
    font-family: inherit; font-size: 13px; font-weight: 700; cursor: pointer;
    letter-spacing: 1px; margin-top: 14px;
  }
  .btn.ghost { background: transparent; color: var(--cyan); border: 1px solid var(--cyan); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .status { font-size: 12px; margin-top: 10px; min-height: 18px; }
  .status.ok { color: var(--ok); } .status.err { color: var(--err); }
  .model-item {
    display: flex; justify-content: space-between; align-items: center;
    border: 1px solid var(--line); border-radius: 4px; padding: 10px 12px; margin-bottom: 8px;
  }
  .model-item .m-name { font-size: 13px; }
  .model-item .m-meta { font-size: 11px; color: var(--dim); margin-top: 3px; }
  .model-item .m-act { font-size: 12px; }
  .model-item .m-act button {
    background: transparent; border: 1px solid var(--accent); color: var(--accent);
    padding: 5px 12px; border-radius: 3px; cursor: pointer; font-family: inherit; font-size: 12px;
  }
  .model-item .m-act button:disabled { opacity: 0.4; cursor: not-allowed; }
  .tag { display: inline-block; font-size: 10px; padding: 2px 6px; border-radius: 3px; margin-left: 6px; }
  .tag.installed { background: rgba(63,185,80,0.15); color: var(--ok); border: 1px solid var(--ok); }
  .tag.dl { background: rgba(57,197,207,0.15); color: var(--cyan); border: 1px solid var(--cyan); }
  .foot { color: var(--dim); font-size: 11px; text-align: center; margin-top: 30px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>随朴 <b>ENGINE</b> · 安装引导</h1>
    <div class="sub">推理引擎配置向导 — API 首选 / 本地 GGUF 兜底 · 配置仅存本机</div>
  </header>

  <div class="card">
    <h2>第一步 · 选择推理后端</h2>
    <label>后端模式</label>
    <select id="backend">
      <option value="api">API（首选，质量最高）</option>
      <option value="local">本地 GGUF（隐私兜底）</option>
    </select>
    <div id="apiPanel">
      <label>服务商预设（可编辑，点选即填入模板）</label>
      <select id="preset"></select>
      <div class="row">
        <div><label>API URL</label><input id="api_url" placeholder="https://..."></div>
        <div><label>模型名</label><input id="api_model" placeholder="model-id"></div>
      </div>
      <label>API Key（必须手填，仅存本机）</label>
      <input id="api_key" type="password" placeholder="sk-...">
      <label>显示名称</label>
      <input id="api_name" placeholder="如：DeepSeek">
    </div>
    <div id="localPanel" style="display:none">
      <label>本地模型文件（GGUF，相对 models_root 或绝对路径）</label>
      <input id="local_model" placeholder="如 qwen3.5-4b-q4.gguf">
      <div id="modelList" style="margin-top:12px"></div>
    </div>
    <div class="status" id="saveStatus"></div>
    <button class="btn" id="saveBtn">保存配置</button>
    <button class="btn ghost" id="verifyBtn">验证配置</button>
  </div>

  <div class="card">
    <h2>第二步 · 数据与备份</h2>
    <label>备份文件夹（一键备份保存位置，可自选）</label>
    <input id="backup_dir" placeholder="如 ~/Documents/Brickery/Backups">
    <label>产出文件夹（文档/表格等产出文件存放位置，可自选）</label>
    <input id="output_dir" placeholder="如 ~/Documents/Brickery/Output">
    <div class="status" id="dirStatus"></div>
  </div>

  <div class="foot">随朴引擎 · 配置写入 config.json · 不硬编码任何第三方推理地址</div>
</div>

<script>
const $ = id => document.getElementById(id);
let presets = [];

async function jget(url) {
  const r = await fetch(url);
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
  return r.json();
}

function setStatus(el, text, ok) {
  el.textContent = text;
  el.className = "status " + (ok ? "ok" : "err");
}

async function init() {
  const data = await jget("/api/presets");
  presets = data.presets;
  const sel = $("preset");
  presets.forEach((p, i) => {
    const o = document.createElement("option");
    o.value = i; o.textContent = p.name;
    sel.appendChild(o);
  });
  sel.onchange = () => applyPreset(sel.value);
  applyPreset(0);

  const cfg = await jget("/api/config");
  if (cfg.ok) {
    $("backend").value = cfg.config.backend || "api";
    // 仅当 config 已有值时覆盖预设填充，避免空值清掉预设默认（如火山 url）
    if (cfg.config.api_url) $("api_url").value = cfg.config.api_url;
    if (cfg.config.api_key) $("api_key").value = cfg.config.api_key;
    if (cfg.config.api_model) $("api_model").value = cfg.config.api_model;
    if (cfg.config.api_name) $("api_name").value = cfg.config.api_name;
    if (cfg.config.local_model) $("local_model").value = cfg.config.local_model;
    toggleBackend();
  }
  loadModels();
}

function applyPreset(i) {
  const p = presets[i];
  if (!p) return;
  $("api_url").value = p.url;
  $("api_model").value = p.model;
  $("api_name").value = p.name;
}

function toggleBackend() {
  const api = $("backend").value === "api";
  $("apiPanel").style.display = api ? "" : "none";
  $("localPanel").style.display = api ? "none" : "";
}

async function loadModels() {
  const data = await jget("/api/models");
  const box = $("modelList");
  box.innerHTML = "";
  (data.models || []).forEach(m => {
    const div = document.createElement("div");
    div.className = "model-item";
    const tags = [];
    if (m.installed) tags.push('<span class="tag installed">已安装</span>');
    if (m.downloading) tags.push('<span class="tag dl">下载中</span>');
    div.innerHTML =
      '<div><div class="m-name">' + m.id + tags.join("") + '</div>' +
      '<div class="m-meta">' + (m.size || "") + " · " + (m.ram || "") + '</div></div>' +
      '<div class="m-act"><button data-id="' + m.id + '" ' + (m.installed || m.downloading ? "disabled" : "") + '>下载</button></div>';
    box.appendChild(div);
  });
  box.querySelectorAll("button").forEach(b => {
    b.onclick = () => downloadModel(b.dataset.id);
  });
}

async function downloadModel(id) {
  const st = $("saveStatus");
  setStatus(st, "开始下载 " + id + " ...", true);
  const r = await jpost("/api/download", { model_id: id });
  if (r.ok) { setStatus(st, "已启动下载：" + id, true); loadModels(); }
  else setStatus(st, "下载失败：" + (r.error || ""), false);
}

$("backend").onchange = toggleBackend;

$("saveBtn").onclick = async () => {
  const st = $("saveStatus");
  const body = {
    backend: $("backend").value,
    api_url: $("api_url").value.trim(),
    api_key: $("api_key").value.trim(),
    api_model: $("api_model").value.trim(),
    api_name: $("api_name").value.trim(),
    local_model: $("local_model").value.trim(),
    backup_dir: $("backup_dir").value.trim(),
    output_dir: $("output_dir").value.trim(),
  };
  const r = await jpost("/api/config", body);
  if (r.ok) setStatus(st, "配置已保存 ✓", true);
  else setStatus(st, "保存失败：" + (r.error || ""), false);
};

$("verifyBtn").onclick = async () => {
  const st = $("saveStatus");
  setStatus(st, "验证中...", true);
  const r = await jpost("/api/verify", {});
  if (r.ok) setStatus(st, "验证通过：" + (r.detail || ""), true);
  else setStatus(st, "验证失败：" + (r.error || ""), false);
};

init();
</script>
</body>
</html>
"""


def _load() -> _config.Config:
    return _config.load_config()


def _verify_api(cfg: _config.Config) -> Dict:
    """对已保存的 API 配置发一个最小 chat 请求验证连通性。"""
    eng = cfg.engine
    if not eng.api_url or not eng.api_key:
        return {"ok": False, "error": "API URL 或 Key 未填写"}
    payload = {
        "model": eng.api_model or "default",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    req = urllib.request.Request(
        eng.api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + eng.api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if resp.status == 200:
            return {"ok": True, "detail": "HTTP 200，端点可达"}
        return {"ok": False, "error": f"HTTP {resp.status}"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _verify_local(cfg: _config.Config) -> Dict:
    eng = cfg.engine
    if not eng.local_model:
        return {"ok": False, "error": "未指定本地模型文件"}
    p = Path(eng.local_model)
    if not p.is_absolute():
        p = cfg.models_root / p
    if p.exists():
        return {"ok": True, "detail": f"模型文件存在：{p.name}（{p.stat().st_size / 1e6:.0f} MB）"}
    return {"ok": False, "error": f"模型文件不存在：{p}"}


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
        elif self.path == "/api/presets":
            self._send(200, {"ok": True, "presets": API_PRESETS})
        elif self.path == "/api/models":
            self._send(200, {"ok": True, "models": self._model_list()})
        elif self.path == "/api/config":
            cfg = _load()
            self._send(200, {
                "ok": True,
                "config": {
                    "backend": cfg.engine.backend,
                    "api_url": cfg.engine.api_url,
                    "api_key": cfg.engine.api_key,
                    "api_model": cfg.engine.api_model,
                    "api_name": cfg.engine.api_name,
                    "local_model": cfg.engine.local_model,
                },
            })
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        data = self._read_json()
        if self.path == "/api/config":
            self._save_config(data)
        elif self.path == "/api/verify":
            self._verify()
        elif self.path == "/api/download":
            self._download(data)
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def _model_list(self) -> List[Dict]:
        cfg = _load()
        installed = {m["name"] for m in _catalog.list_installed(cfg.models_root)}
        out = []
        for entry in _catalog.GGUF_MODELS:
            mid = entry.get("id", entry.get("name", ""))
            out.append({
                "id": mid,
                "size": entry.get("size", ""),
                "ram": entry.get("ram", ""),
                "installed": mid in installed,
                "downloading": bool(_catalog.download_status(mid).get("active")),
            })
        return out

    def _save_config(self, data: Dict) -> None:
        cfg = _load()
        eng = cfg.engine
        eng.backend = data.get("backend", eng.backend)
        eng.api_url = data.get("api_url", eng.api_url)
        eng.api_key = data.get("api_key", eng.api_key)
        eng.api_model = data.get("api_model", eng.api_model)
        eng.api_name = data.get("api_name", eng.api_name)
        eng.local_model = data.get("local_model", eng.local_model)
        if data.get("backup_dir"):
            cfg.backup_dir = Path(data["backup_dir"]).expanduser()
        if data.get("output_dir"):
            cfg.output_dir = Path(data["output_dir"]).expanduser()
        try:
            cfg.save()
            self._send(200, {"ok": True})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(e)})

    def _verify(self) -> None:
        cfg = _load()
        if cfg.engine.backend == "local":
            self._send(200, _verify_local(cfg))
        else:
            self._send(200, _verify_api(cfg))

    def _download(self, data: Dict) -> None:
        mid = data.get("model_id", "")
        if not mid:
            self._send(400, {"ok": False, "error": "缺少 model_id"})
            return
        cfg = _load()
        try:
            result = _catalog.start_download(mid, cfg.models_root)
            self._send(200, {"ok": True, "detail": result})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(e)})


def serve(host: str = HOST, port: int = PORT, daemon: bool = True) -> ThreadingHTTPServer:
    """启动引导服务。daemon=True 时后台线程运行，返回 server 对象。"""
    server = ThreadingHTTPServer((host, port), _Handler)
    if daemon:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
    else:
        server.serve_forever()
    return server


if __name__ == "__main__":
    print(f"随朴引擎安装引导：http://{HOST}:{PORT}")
    serve(daemon=False)
