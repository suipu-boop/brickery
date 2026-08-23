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
import subprocess
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
  .steps { display: flex; gap: 8px; margin-bottom: 18px; }
  .step-ind {
    flex: 1; text-align: center; padding: 8px 10px; border-radius: 6px;
    background: var(--bg2); color: var(--dim); font-size: 12px; border: 1px solid var(--line);
  }
  .step-ind.active { background: rgba(57,197,207,0.12); color: var(--cyan); border-color: var(--cyan); }
  .nav { display: flex; gap: 10px; margin-top: 18px; justify-content: flex-end; }
  .nav .btn { min-width: 96px; }
  .pickrow { display: flex; gap: 8px; align-items: center; }
  .pickrow input { flex: 1; }
  .pick { white-space: nowrap; }
  .summary { background: var(--bg2); border: 1px solid var(--line); border-radius: 6px; padding: 12px 14px; margin-top: 6px; }
  .sum-row { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0; font-size: 13px; }
  .sum-row span { color: var(--dim); }
  .sum-row b { font-weight: 600; word-break: break-all; text-align: right; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>随朴 <b>ENGINE</b> · 安装引导</h1>
    <div class="sub">推理引擎配置向导 — API 首选 / 本地 GGUF 兜底 · 配置仅存本机</div>
  </header>

  <div class="steps">
    <div class="step-ind active" data-step="1">1 · 推理后端</div>
    <div class="step-ind" data-step="2">2 · 数据与备份</div>
    <div class="step-ind" data-step="3">3 · 完成</div>
  </div>

  <div class="card step-panel" id="step1">
    <h2>第一步 · 选择推理后端</h2>
    <label>后端模式</label>
    <select id="backend">
      <option value="api">API（首选，质量最高）</option>
      <option value="local">本地 GGUF（隐私兜底）</option>
    </select>
    <div id="apiPanel">
      <label>服务商预设（可编辑，点选即填入模板）</label>
      <select id="preset"></select>
      <div class="hint" id="presetHint" style="font-size:11px;color:var(--dim);margin-top:4px"></div>
      <div class="row" style="margin-top:6px">
        <button class="btn ghost" type="button" onclick="enterPlanMode('coding')">＋ 自定义 Coding Plan</button>
        <button class="btn ghost" type="button" onclick="enterPlanMode('custom')">＋ 其他厂商普通 API</button>
      </div>
      <div id="codingHintPanel" style="display:none;margin-top:8px;padding:8px;border-radius:8px;background:rgba(99,102,241,.06);font-size:12px;color:var(--dim)">
        Coding Plan 是各厂商的独立额度，Base URL 与普通 API 不同——填错（如用了普通 /v3 地址）会走普通额度，且用不掉 Coding Plan。
        <div style="margin-top:4px">示例 · 火山方舟 Coding Plan：<code style="user-select:all">https://ark.cn-beijing.volces.com/api/coding/v3</code></div>
        <div>示例 · 腾讯混元 Coding Plan（免费）：<code style="user-select:all">https://api.lkeap.cloud.tencent.com/coding/v3</code></div>
      </div>
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
    <div class="nav">
      <button class="btn" onclick="goStep(2)">下一步</button>
    </div>
  </div>

  <div class="card step-panel" id="step2" style="display:none">
    <h2>第二步 · 数据与备份</h2>
    <label>备份文件夹（一键备份保存位置，可自选）</label>
    <div class="pickrow">
      <input id="backup_dir" placeholder="如 ~/Documents/Brickery/Backups">
      <button class="btn ghost pick" onclick="pickFolder('backup_dir')">选择…</button>
    </div>
    <label>产出文件夹（文档/表格等产出文件存放位置，可自选）</label>
    <div class="pickrow">
      <input id="output_dir" placeholder="如 ~/Documents/Brickery/Output">
      <button class="btn ghost pick" onclick="pickFolder('output_dir')">选择…</button>
    </div>
    <div class="status" id="dirStatus"></div>
    <div class="nav">
      <button class="btn ghost" onclick="goStep(1)">上一步</button>
      <button class="btn" onclick="goStep(3)">下一步</button>
    </div>
  </div>

  <div class="card step-panel" id="step3" style="display:none">
    <h2>第三步 · 完成</h2>
    <p class="sub">确认以下配置后保存。配置仅存本机。</p>
    <div class="summary" id="summary"></div>
    <div class="status" id="saveStatus"></div>
    <div class="nav">
      <button class="btn ghost" onclick="goStep(2)">上一步</button>
      <button class="btn" id="saveBtn">保存配置</button>
      <button class="btn ghost" id="verifyBtn">验证配置</button>
    </div>
  </div>

  <div class="foot">随朴引擎 · 配置写入 config.json · 不硬编码任何第三方推理地址</div>
</div>

<script>
const $ = id => document.getElementById(id);
let presets = [];
let planMode = "preset"; // preset | coding | custom

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
  const cp = document.createElement("option");
  cp.value = "-1"; cp.textContent = "自定义 Coding Plan";
  sel.appendChild(cp);
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
  const hint = $("presetHint");
  if (i === "-1") {
    // 自定义 Coding Plan：清空模板，手填任意兼容 OpenAI 的端点
    enterPlanMode("coding");
    return;
  }
  const p = presets[i];
  if (!p) return;
  planMode = "preset";
  $("api_url").value = p.url;
  $("api_model").value = p.model;
  $("api_name").value = p.name;
  if (hint) hint.textContent = "";
  setPlanInputs(false);
}

function enterPlanMode(mode) {
  planMode = mode;
  $("api_url").value = "";
  $("api_model").value = "";
  $("api_name").value = mode === "coding" ? "我的 Coding Plan" : "";
  const hint = $("presetHint");
  if (hint) hint.textContent = "";
  setPlanInputs(mode === "coding");
}

function setPlanInputs(coding) {
  const url = $("api_url"), model = $("api_model"), panel = $("codingHintPanel");
  if (coding) {
    url.placeholder = "Base URL（Coding Plan 专用，如 …/api/coding/v3）";
    model.placeholder = "模型名（Coding Plan 里的 endpoint ID / 模型名）";
    if (panel) panel.style.display = "";
  } else {
    url.placeholder = "https://...";
    model.placeholder = "model-id";
    if (panel) panel.style.display = "none";
  }
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

function goStep(n) {
  [1, 2, 3].forEach(i => {
    $("step" + i).style.display = i === n ? "" : "none";
    document.querySelector('.step-ind[data-step="' + i + '"]').classList.toggle("active", i === n);
  });
  if (n === 3) renderSummary();
}

function renderSummary() {
  const rows = [
    ["后端模式", $("backend").value === "api" ? "API" : "本地 GGUF"],
    ["模式", planMode === "coding" ? "Coding Plan" : planMode === "custom" ? "自定义 API" : "预设"],
    ["服务商", $("api_name").value || "—"],
    ["API URL", $("api_url").value || "—"],
    ["模型", $("api_model").value || $("local_model").value || "—"],
    ["备份文件夹", $("backup_dir").value || "—"],
    ["产出文件夹", $("output_dir").value || "—"],
  ];
  $("summary").innerHTML = rows.map(([k, v]) =>
    '<div class="sum-row"><span>' + k + '</span><b>' + v + '</b></div>').join("");
}

async function pickFolder(inputId) {
  const st = $("dirStatus");
  if (st) setStatus(st, "请在弹出的窗口中选择文件夹...", true);
  const r = await jpost("/api/pick_folder", {});
  if (r.ok) {
    $(inputId).value = r.path;
    if (st) setStatus(st, "已选择：" + r.path, true);
  } else if (st) {
    setStatus(st, "未选择：" + (r.error || "已取消"), false);
  }
}

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
  if (planMode === "coding" && !body.api_name) body.api_name = "我的 Coding Plan";
  const r = await jpost("/api/config", body);
  if (r.ok) {
    setStatus(st, "配置已保存，正在进入聊天...", true);
    setTimeout(() => { location.href = "http://127.0.0.1:18767/"; }, 800);
  } else {
    setStatus(st, "保存失败：" + (r.error || ""), false);
  }
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
    url = eng.api_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    req = urllib.request.Request(
        url,
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
        elif self.path == "/api/pick_folder":
            self._pick_folder()
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
        # 同步激活 profile：ipc/status 检测读 active profile，不同步会误报"API 未配置"
        for p in (cfg.profiles or []):
            if p.get("id") == cfg.active_profile_id:
                p["backend"] = eng.backend
                p["api_url"] = eng.api_url
                p["api_key"] = eng.api_key
                p["api_model"] = eng.api_model
                p["api_name"] = eng.api_name
                p["local_model"] = eng.local_model
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

    def _pick_folder(self) -> None:
        """调 macOS 原生文件夹选择器（osascript choose folder），返回选中路径。"""
        try:
            r = subprocess.run(
                ["osascript", "-e", 'POSIX path of (choose folder with prompt "选择文件夹")'],
                capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and r.stdout.strip():
                self._send(200, {"ok": True, "path": r.stdout.strip()})
            else:
                self._send(200, {"ok": False, "error": (r.stderr or "已取消").strip()})
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
