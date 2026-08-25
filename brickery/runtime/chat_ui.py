"""chat_ui.py —— 桌面 Agent 界面（随朴 2026-08-16 改造）。

本地 web 桌面 agent 界面，SPA 结构：左侧 220px 侧边栏（12 功能区块）+ 顶栏
（区块标题 + 引擎状态圆点 + daemon 启停）+ 内容区。所有功能页通过通用 IPC
桥接（POST /api/ipc）直连底座 IpcServer（127.0.0.1:18765，JSON Lines 协议），
method 走白名单校验，防越权。

- 服务：127.0.0.1:18767
- 路由：
  - GET  /                       桌面 Agent 界面（SPA）
  - POST /api/ipc                {method, params, stream?} -> 桥接底座 IPC
  - GET  /api/status             daemon / 引擎状态（顶栏）
  - POST /api/messages/delete    {session_id, ids} -> 删除会话内指定消息
  - POST /api/chat               兼容旧接口（非流式）
  - GET  /api/engine             引擎状态（兼容旧接口）
"""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from . import config as _config
from .engine_providers import EngineProviderRegistry
from .engine_router import EngineRouter, NoEngineConfigured

HOST = "127.0.0.1"
PORT = 18767
GUIDE_URL = "http://127.0.0.1:18766"

# ----- 底座 IPC 桥接（IpcServer，127.0.0.1:18765，JSON Lines）-----
IPC_HOST = "127.0.0.1"
IPC_PORT = 18765
IPC_TIMEOUT = 5.0
IPC_STREAM_TIMEOUT = 300.0

# method 白名单：仅放行前端功能页实际用到的 handler，防越权调用。
IPC_ALLOWED_METHODS = {
    # 聊天 / 会话
    "chat", "chat_cancel",
    "session_list", "session_new", "session_get", "session_rename",
    "session_delete", "session_set_profile",
    # 工具确认（模型请求 MEDIUM/HIGH 风险工具时前端轮询裁决）
    "confirm_next", "confirm_resolve",
    # 技能库 / 工具
    "skill_list", "skill_toggle", "skill_trigger",
    "skill_library_list", "skill_library_install", "skill_library_uninstall",
    "skill_library_upgrade", "skill_library_review",
    "skill_library_import_preview", "skill_library_import",
    "tool_list", "tool_toggle", "tool_trigger",
    # 记忆柜 / 保险库
    "vault_list", "vault_add", "vault_delete", "vault_detail", "vault_ocr",
    "vault_snapshot", "vault_scan", "vault_enhance", "vault_sync_skills",
    # 记忆
    "memory_search", "memory_export", "recall", "portrait", "portrait_update",
    "core_get", "core_set", "core_smart_get", "core_smart_delete",
    "core_candidates", "core_candidate_resolve", "core_candidate_dismiss",
    "evolve_candidates", "evolve_confirm", "evolve_reject", "evolve_refine_stats",
    "suggestions", "suggestion_feedback",
    # agent 配置只读
    "agent_get",
    # 设置 / 模型
    "config_get", "config_set", "models_list", "model_recommend",
    "model_download_start", "model_download_status", "model_download_pause",
    "model_download_cancel", "model_download_resume", "model_delete",
    # 医生
    "doctor", "health",
    # 定时任务
    "task_submit", "task_list", "task_get", "task_cancel",
    # 工作台
    "drawer_list", "drawer_get", "drawer_create", "drawer_update",
    "drawer_delete", "node_add", "node_update", "node_delete",
    "edge_add", "edge_delete", "recordbook_sync", "recordbook_get",
    "explain_node", "drawer_chat", "recommend_detect",
    # 文件
    "file_index", "file_update", "file_remove", "file_search",
    # 备份恢复
    "backup_export", "backup_restore", "backup_default", "backup_list",
    # 规则
    "rules_list", "rules_add", "rules_remove", "rules_reload",
    # 连接器
    "feishu_setup", "telegram_setup",
    # 其他
    "mcp_list", "set_mode", "daemon_start", "daemon_stop", "daemon_status",
    "status", "interoception_state", "open_folder",
}


def _ipc_call(method: str, params: Optional[Dict] = None,
              timeout: float = IPC_TIMEOUT) -> Dict:
    """非流式 IPC 调用：socket 直连 18765，JSON Lines 请求/响应。"""
    req_id = int(time.time() * 1000) % 1000000
    req = {"req_id": req_id, "method": method, "params": params or {}}
    s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=timeout)
    try:
        s.settimeout(timeout)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while True:
            data = s.recv(65536)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if frame.get("req_id") != req_id:
                    continue
                if not frame.get("ok", False):
                    raise RuntimeError(frame.get("error", "IPC 调用失败"))
                return frame.get("data") or {}
    finally:
        s.close()
    raise RuntimeError("IPC 服务无响应")


def _ipc_stream(method: str, params: Optional[Dict] = None) -> Iterator[Dict]:
    """流式 IPC：逐行 yield JSON 帧（delta / done / error）。"""
    req_id = int(time.time() * 1000) % 1000000
    req = {"req_id": req_id, "method": method, "params": params or {}}
    s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=IPC_TIMEOUT)
    try:
        s.settimeout(IPC_STREAM_TIMEOUT)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while True:
            data = s.recv(65536)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                yield frame
                if frame.get("type") == "done":
                    return
    finally:
        s.close()


PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brickery · 桌面 Agent</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --panel2: #1c2128; --line: #2d333b;
    --ink: #e6edf3; --dim: #8b949e; --accent: #ff7a18; --cyan: #39c5cf;
    --green: #3fb950; --red: #f85149; --user: #1f6feb;
    --grid: rgba(57, 197, 207, 0.05);
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
    display: flex; height: 100vh; overflow: hidden;
  }
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-thumb { background: var(--line); border-radius: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }

  /* ---------- 侧边栏 ---------- */
  #sidebar {
    width: 220px; min-width: 220px; height: 100vh;
    background: rgba(13,17,23,0.95);
    border-right: 1px solid var(--line);
    display: flex; flex-direction: column;
  }
  .brand {
    display: flex; align-items: center; gap: 10px;
    padding: 16px 14px; border-bottom: 1px solid var(--line);
  }
  .brand-logo {
    width: 34px; height: 34px; border-radius: 8px;
    background: linear-gradient(135deg, var(--accent), #ffb347);
    color: #0d1117; font-size: 18px; font-weight: 800;
    display: flex; align-items: center; justify-content: center;
  }
  .brand-name { font-size: 15px; font-weight: 700; letter-spacing: 1px; }
  .brand-name b { color: var(--accent); }
  .brand-tag { font-size: 10px; color: var(--dim); margin-top: 2px; }
  #navList { flex: 1; overflow-y: auto; padding: 8px 0; }
  .nav-group { font-size: 10px; color: var(--dim); padding: 10px 16px 4px; letter-spacing: 1px; }
  .nav-item {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 16px; cursor: pointer; font-size: 13px;
    color: var(--dim); border-left: 3px solid transparent;
    transition: background 0.15s, color 0.15s;
  }
  .nav-item:hover { background: rgba(255,122,24,0.06); color: var(--ink); }
  .nav-item.active {
    background: rgba(255,122,24,0.12); color: var(--accent);
    border-left-color: var(--accent); font-weight: 700;
  }
  .nav-item .ico { width: 18px; text-align: center; font-size: 14px; }
  .sidebar-footer {
    border-top: 1px solid var(--line); padding: 12px 16px; font-size: 11px;
  }
  .sidebar-footer .row { display: flex; align-items: center; gap: 8px; color: var(--dim); margin-bottom: 6px; }
  .sidebar-footer .row:last-child { margin-bottom: 0; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot.ok { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot.err { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .dot.bad { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .dot.warn { background: #d29922; box-shadow: 0 0 6px #d29922; }
  .dot.off { background: var(--dim); }

  /* ---------- 主区 ---------- */
  #main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  #topbar {
    height: 52px; min-height: 52px; display: flex; align-items: center;
    justify-content: space-between; padding: 0 20px;
    border-bottom: 2px solid var(--accent);
    background: rgba(13,17,23,0.9);
  }
  #sectionTitle { font-size: 15px; font-weight: 700; letter-spacing: 2px; }
  #sectionTitle b { color: var(--accent); }
  .topbar-right { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--dim); }
  .topbar-right .dot { margin-right: 4px; }
  .btn {
    background: var(--panel2); color: var(--ink); border: 1px solid var(--line);
    padding: 5px 12px; border-radius: 4px; font-family: inherit; font-size: 12px;
    cursor: pointer; transition: border-color 0.15s, color 0.15s;
  }
  .btn:hover { border-color: var(--accent); color: var(--accent); }
  .btn.primary { background: var(--accent); color: #0d1117; border-color: var(--accent); font-weight: 700; }
  .btn.primary:hover { filter: brightness(1.1); color: #0d1117; }
  .btn.danger { color: var(--red); }
  .btn.danger:hover { border-color: var(--red); color: var(--red); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn.sm { padding: 2px 8px; font-size: 11px; }

  #content { flex: 1; overflow-y: auto; padding: 20px; }

  /* ---------- 通用组件 ---------- */
  .card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
    padding: 14px 16px; margin-bottom: 14px;
  }
  .card h3 { font-size: 13px; margin-bottom: 10px; color: var(--accent); letter-spacing: 1px; }
  .card .hint { font-size: 11px; color: var(--dim); margin-bottom: 10px; }
  .section-card { border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; background: rgba(255,255,255,0.02); }
  .section-card > h3 { font-size: 12px; color: var(--accent); letter-spacing: 1.5px; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
  .section-card .sec-sub { font-size: 11px; color: var(--dim); margin-bottom: 10px; }
  .settings-wrap { max-width: 1000px; }
  .settings-layout { display: flex; gap: 16px; align-items: flex-start; }
  .settings-nav { flex: 0 0 172px; display: flex; flex-direction: column; gap: 2px; background: rgba(255,255,255,0.02); border: 1px solid var(--line); border-radius: 10px; padding: 8px; position: sticky; top: 12px; }
  .settings-nav button { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; background: transparent; border: none; color: var(--dim); padding: 9px 10px; border-radius: 6px; font-size: 12.5px; cursor: pointer; font-family: inherit; }
  .settings-nav button:hover { background: rgba(255,255,255,0.06); color: var(--ink); }
  .settings-nav button.active { background: var(--accent); color: #fff; }
  .settings-nav button .nav-ico { width: 16px; text-align: center; font-style: normal; }
  .settings-body { flex: 1 1 auto; min-width: 0; }
  .vendor-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .vendor-tags .vt { font-size: 11px; padding: 3px 10px; border: 1px solid var(--line); border-radius: 20px; color: var(--dim); cursor: pointer; user-select: none; }
  .vendor-tags .vt.active { border-color: var(--accent); color: var(--accent); background: rgba(99,179,237,0.08); }
  @media (max-width: 720px) { .settings-layout { flex-direction: column; } .settings-nav { flex-direction: row; flex-wrap: wrap; position: static; } .settings-nav button { width: auto; } }
  .backend-btn { min-width: 110px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  @media (max-width: 1100px) { .grid3 { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 800px) { .grid2, .grid3 { grid-template-columns: 1fr; } }
  .field { margin-bottom: 10px; }
  .field label { display: block; font-size: 11px; color: var(--dim); margin-bottom: 4px; }
  .field input, .field textarea, .field select {
    width: 100%; background: #0d1117; border: 1px solid var(--line); color: var(--ink);
    padding: 8px 10px; border-radius: 4px; font-family: inherit; font-size: 12px;
  }
  .field input:focus, .field textarea:focus, .field select:focus { outline: none; border-color: var(--accent); }
  .field textarea { resize: vertical; min-height: 60px; }
  .engine-status { margin-bottom: 12px; padding: 8px 10px; background: #0d1117; border: 1px solid var(--line); border-radius: 6px; }
  .status-row { display: flex; align-items: center; gap: 8px; font-size: 12px; padding: 3px 0; }
  .modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center; z-index: 100; }
  .modal-box { width: 420px; max-width: 92vw; max-height: 86vh; overflow-y: auto; background: var(--bg); border: 1px solid var(--line); border-radius: 10px; padding: 18px; box-shadow: 0 8px 30px rgba(0,0,0,.4); }
  .modal-box h3 { font-size: 14px; margin-bottom: 14px; color: var(--accent); letter-spacing: 1px; }
  .row { display: flex; align-items: center; gap: 8px; }
  .row.between { justify-content: space-between; }
  .row.wrap { flex-wrap: wrap; }
  .muted { color: var(--dim); font-size: 11px; }
  .ok-text { color: var(--green); }
  .err-text { color: var(--red); }
  .tag {
    display: inline-block; font-size: 10px; padding: 1px 7px; border-radius: 3px;
    border: 1px solid var(--line); color: var(--dim); margin-right: 4px;
  }
  .tag.on { color: var(--green); border-color: var(--green); }
  .tag.off { color: var(--red); border-color: var(--red); }
  .dropzone {
    margin: 10px 0 14px; padding: 14px 16px; border: 1.5px dashed var(--line);
    border-radius: 8px; text-align: center; font-size: 12px; color: var(--dim);
    cursor: pointer; transition: border-color .15s, background .15s;
  }
  .dropzone:hover, .dropzone.drag { border-color: var(--accent); background: rgba(255,122,24,0.05); color: var(--ink); }
  .dropzone .dz-title { font-size: 13px; font-weight: 600; color: var(--ink); margin-bottom: 4px; }
  .import-item { padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; margin-bottom: 8px; background: var(--panel2); font-size: 12px; }
  .import-item .i-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .import-item .i-desc { color: var(--dim); font-size: 11px; margin-top: 4px; }
  .import-item.err { border-color: var(--red); }
  .import-item.ok { border-color: var(--green); }
  .modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
  .list-item {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 9px 12px; border: 1px solid var(--line); border-radius: 4px;
    margin-bottom: 8px; background: var(--panel2); font-size: 12px;
  }
  .list-item .title { font-weight: 600; }
  .list-item .sub { font-size: 11px; color: var(--dim); margin-top: 2px; }
  .empty { text-align: center; color: var(--dim); font-size: 12px; padding: 30px 0; }
  .tabs { display: flex; gap: 6px; margin-bottom: 14px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
  .tab {
    padding: 5px 14px; border-radius: 4px; font-size: 12px; cursor: pointer;
    color: var(--dim); border: 1px solid transparent;
  }
  .tab:hover { color: var(--ink); }
  .tab.active { background: rgba(255,122,24,0.12); color: var(--accent); border-color: var(--accent); }
  .mono { font-family: "SF Mono", Menlo, monospace; }
  .pre {
    background: #0d1117; border: 1px solid var(--line); border-radius: 4px;
    padding: 10px; font-size: 11px; white-space: pre-wrap; word-break: break-word;
    max-height: 320px; overflow-y: auto; line-height: 1.6;
  }
  .switch { position: relative; width: 34px; height: 18px; cursor: pointer; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .switch .slider {
    position: absolute; inset: 0; background: var(--line); border-radius: 9px; transition: 0.2s;
  }
  .switch .slider::before {
    content: ""; position: absolute; width: 14px; height: 14px; left: 2px; top: 2px;
    background: #fff; border-radius: 50%; transition: 0.2s;
  }
  .switch input:checked + .slider { background: var(--accent); }
  .switch input:checked + .slider::before { transform: translateX(16px); }

  /* ---------- 聊天页 ---------- */
  .chat-layout { display: flex; height: 100%; margin: -20px; }
  .session-sidebar {
    width: 220px; min-width: 220px; border-right: 1px solid var(--line);
    display: flex; flex-direction: column; background: rgba(13,17,23,0.6);
  }
  .session-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 12px; border-bottom: 1px solid var(--line);
  }
  .session-header span { font-size: 12px; color: var(--dim); letter-spacing: 1px; }
  #sessionList { flex: 1; overflow-y: auto; padding: 8px; }
  .sess-item {
    padding: 8px 10px; border-radius: 4px; cursor: pointer; font-size: 12px;
    color: var(--dim); margin-bottom: 2px; display: flex; align-items: center; justify-content: space-between; gap: 6px;
  }
  .sess-item:hover { background: rgba(255,122,24,0.06); color: var(--ink); }
  .sess-item.active { background: rgba(255,122,24,0.12); color: var(--accent); }
  .sess-item .t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sess-item .ops { display: none; gap: 2px; }
  .sess-item:hover .ops { display: flex; }
  .sess-item .ops button {
    background: none; border: none; color: var(--dim); cursor: pointer; font-size: 11px; padding: 0 2px;
  }
  .sess-item .ops button:hover { color: var(--accent); }
  .chat-pane { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .chat-toolbar {
    display: flex; align-items: center; gap: 8px; padding: 8px 16px;
    border-bottom: 1px solid var(--line); font-size: 12px;
  }
  .chat-toolbar .spacer { flex: 1; }
  #messages { flex: 1; overflow-y: auto; padding: 20px; }
  .msg { display: flex; margin-bottom: 14px; }
  .msg .bubble {
    max-width: 78%; padding: 10px 14px; border-radius: 6px;
    font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
    position: relative;
  }
  .msg.user { justify-content: flex-end; }
  .msg.user .bubble { background: var(--user); color: #fff; }
  .msg.assistant .bubble { background: var(--assistant, #161b22); border: 1px solid var(--line); }
  .msg .who { font-size: 10px; color: var(--dim); margin-bottom: 4px; }
  .msg.user .who { text-align: right; }
  .msg .copy {
    position: absolute; top: 6px; right: 8px; font-size: 10px; color: var(--dim);
    cursor: pointer; display: none; background: rgba(0,0,0,0.4); padding: 1px 6px; border-radius: 3px;
  }
  .msg:hover .copy { display: block; }
  .msg .copy:hover { color: var(--accent); }
  .msg.selected .bubble { outline: 2px solid var(--accent); }
  .msg .meta { font-size: 10px; color: var(--dim); margin-top: 4px; }
  .chat-input-bar {
    border-top: 1px solid var(--line); padding: 12px 16px;
    background: rgba(13,17,23,0.9);
  }
  #attachments { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
  .attach-chip {
    font-size: 11px; background: var(--panel2); border: 1px solid var(--line);
    padding: 3px 8px; border-radius: 3px; color: var(--cyan);
  }
  .attach-chip .x { cursor: pointer; margin-left: 4px; color: var(--dim); }
  .attach-chip .x:hover { color: var(--red); }
  #toolRow { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  #toolRow .toolRow-hint { font-size: 11px; color: var(--dim); }
  #modelSel {
    background: #0d1117; border: 1px solid var(--line); color: var(--ink);
    padding: 6px 10px; border-radius: 4px; font-family: inherit; font-size: 12px;
    height: 30px; min-width: 180px; cursor: pointer;
  }
  #modelSel:focus { outline: none; border-color: var(--accent); }
  #inputRow { display: flex; gap: 8px; align-items: flex-end; }
  #input {
    flex: 1; background: #0d1117; border: 1px solid var(--line); color: var(--ink);
    padding: 10px 12px; border-radius: 4px; font-family: inherit; font-size: 13px;
    resize: none; height: 44px; max-height: 140px;
  }
  #input:focus { outline: none; border-color: var(--accent); }
  #send {
    background: var(--accent); color: #0d1117; border: none; padding: 0 22px;
    border-radius: 4px; font-family: inherit; font-size: 13px; font-weight: 700; cursor: pointer; height: 44px;
  }
  #send:disabled { opacity: 0.4; cursor: not-allowed; }
  #stopBtn {
    background: var(--red); color: #fff; border: none; padding: 0 18px;
    border-radius: 4px; font-family: inherit; font-size: 13px; font-weight: 700; cursor: pointer; height: 44px;
  }
  .icon-btn {
    background: var(--panel2); border: 1px solid var(--line); color: var(--dim);
    width: 44px; height: 44px; border-radius: 4px; cursor: pointer; font-size: 16px;
  }
  .icon-btn:hover { border-color: var(--accent); color: var(--accent); }
  .typing { color: var(--dim); font-size: 12px; padding: 4px 0; animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }

  /* ---------- 技能库 / 保险库卡片 ---------- */
  .item-card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
    padding: 12px 14px; margin-bottom: 10px;
  }
  .item-card .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .item-card .name { font-size: 13px; font-weight: 700; }
  .item-card .desc { font-size: 11px; color: var(--dim); margin-top: 6px; line-height: 1.5; }
  .item-card .triggers { margin-top: 6px; }

  /* ---------- 医生 ---------- */
  .check-item {
    display: flex; align-items: flex-start; gap: 10px; padding: 8px 0;
    border-bottom: 1px solid var(--line); font-size: 12px;
  }
  .check-item:last-child { border-bottom: none; }
  .check-item .st { width: 16px; text-align: center; font-weight: 700; }
  .check-item .nm { font-weight: 600; }
  .check-item .dt { color: var(--dim); font-size: 11px; margin-top: 2px; word-break: break-all; }

  /* ---------- 工作台图谱 ---------- */
  #graphCanvas {
    width: 100%; height: 360px; background: #0d1117; border: 1px solid var(--line);
    border-radius: 6px; position: relative; overflow: hidden;
  }
  .g-node {
    position: absolute; background: var(--panel2); border: 1px solid var(--cyan);
    color: var(--ink); padding: 6px 10px; border-radius: 4px; font-size: 11px;
    cursor: pointer; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .g-node:hover { border-color: var(--accent); }
  .g-node.selected { border-color: var(--accent); background: rgba(255,122,24,0.12); }
  .g-edge { position: absolute; height: 1px; background: var(--line); transform-origin: left center; }
  .g-edge .rel { position: absolute; top: -14px; left: 50%; transform: translateX(-50%); font-size: 9px; color: var(--dim); white-space: nowrap; }

  /* ---------- 表格 ---------- */
  table.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
  table.tbl th, table.tbl td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); }
  table.tbl th { color: var(--dim); font-weight: 600; font-size: 11px; }
  table.tbl tr:hover td { background: rgba(255,122,24,0.04); }
</style>
</head>
<body>
<aside id="sidebar">
  <div class="brand">
    <div class="brand-logo"></div>
    <div>
      <div class="brand-name">Brickery <b>AGENT</b></div>
      <div class="brand-tag">本地桌面 Agent</div>
    </div>
  </div>
  <nav id="navList"></nav>
  <div class="sidebar-footer">
    <div class="row"><span class="dot" id="daemonDot"></span><span id="daemonText">daemon 检测中...</span></div>
    <div class="row"><span class="dot" id="engineDot"></span><span id="engineText">引擎检测中...</span></div>
  </div>
</aside>
<div id="main">
  <header id="topbar">
    <div id="sectionTitle">聊天</div>
    <div class="topbar-right">
      <span id="engineBadge"></span>
      <button class="btn sm" id="daemonBtn" onclick="toggleDaemon()">启动 daemon</button>
    </div>
  </header>
  <div id="content"></div>
</div>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtTime = ts => { if (!ts) return ""; const d = new Date(ts * 1000); return d.getFullYear() + "-" + String(d.getMonth()+1).padStart(2,"0") + "-" + String(d.getDate()).padStart(2,"0") + " " + String(d.getHours()).padStart(2,"0") + ":" + String(d.getMinutes()).padStart(2,"0"); };

/* ================= IPC 桥接 ================= */
async function ipc(method, params) {
  const r = await fetch("/api/ipc", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ method, params: params || {} }) });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "IPC 调用失败");
  return j.data;
}
async function ipcStream(method, params, onDelta, onDone, onError, onEvent) {
  const r = await fetch("/api/ipc", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ method, params: params || {}, stream: true }) });
  if (!r.ok || !r.body) { onError && onError("流式请求失败"); return; }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\\n\\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        for (const line of chunk.split("\\n")) {
          if (!line.startsWith("data: ")) continue;
          let frame; try { frame = JSON.parse(line.slice(6)); } catch (e) { continue; }
          if (frame.type === "delta") onDelta && onDelta(frame.delta || "");
          else if (frame.type === "done") { onDone && onDone(frame.data || {}); return; }
          else if (frame.type === "error") { onError && onError(frame.error || "流式错误"); return; }
          else if (frame.type === "event") { onEvent && onEvent(frame.text || ""); }
        }
      }
    }
  } catch (e) { onError && onError(String(e)); }
}

/* ================= 导航 ================= */
const NAV = [
  { id: "chat", title: "聊天" },
  { id: "skills", title: "技能库" },
  { id: "market", title: "积木市场" },
  { id: "cabinet", title: "记忆柜" },
  { id: "memory", title: "记忆" },
  { id: "settings", title: "设置" },
  { id: "doctor", title: "医生" },
  { id: "tasks", title: "定时任务" },
  { id: "vault", title: "保险库" },
];
const NAV_EXT = [
  { id: "backup", title: "备份恢复" },
  { id: "rules", title: "规则" },
  { id: "connectors", title: "连接器" },
];
let currentSection = "chat";

function buildNav() {
  const nav = $("navList");
  let html = "";
  for (const n of NAV) html += '<div class="nav-item" data-sec="' + n.id + '"><span>' + n.title + '</span></div>';
  html += '<div class="nav-group">扩展</div>';
  for (const n of NAV_EXT) html += '<div class="nav-item" data-sec="' + n.id + '"><span>' + n.title + '</span></div>';
  nav.innerHTML = html;
  nav.querySelectorAll(".nav-item").forEach(el => el.onclick = () => switchSection(el.dataset.sec));
}
function switchSection(sec) {
  currentSection = sec;
  document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.sec === sec));
  const meta = [...NAV, ...NAV_EXT].find(n => n.id === sec);
  $("sectionTitle").textContent = meta.title;
  const renderer = renderers[sec];
  if (renderer) renderer();
}

/* ================= 顶栏状态 ================= */
let daemonRunning = false;
async function loadStatus() {
  try {
    const d = await ipc("status", {});
    const eng = d.engine || {};
    const daemon = d.daemon || {};
    daemonRunning = !!daemon.running;
    const eDot = $("engineDot"), eText = $("engineText");
    if (eng.backend === "local") {
      eDot.className = "dot " + (eng.local_available ? "ok" : "err");
      eText.textContent = "本地引擎" + (eng.local_available ? "就绪" : "未就绪");
    } else {
      eDot.className = "dot " + (eng.network_configured ? "ok" : "warn");
      eText.textContent = eng.network_configured ? ("API · " + (eng.api_name || eng.api_model || "已配置")) : "API 未配置";
    }
    const dDot = $("daemonDot"), dText = $("daemonText");
    dDot.className = "dot " + (daemonRunning ? "ok" : "off");
    dText.textContent = daemonRunning ? "daemon 运行中" : "daemon 已停止";
    $("daemonBtn").textContent = daemonRunning ? "停止 daemon" : "启动 daemon";
    const counts = d.counts || {};
    $("engineBadge").textContent = "会话 " + (counts.sessions || 0) + " · 技能 " + (counts.skills || 0) + " · 工具 " + (counts.tools || 0);
  } catch (e) {
    $("engineDot").className = "dot err"; $("engineText").textContent = "IPC 不可达";
    $("daemonDot").className = "dot off"; $("daemonText").textContent = "daemon 未知";
  }
}
async function toggleDaemon() {
  const btn = $("daemonBtn"); btn.disabled = true;
  try {
    if (daemonRunning) await ipc("daemon_stop", {});
    else await ipc("daemon_start", {});
  } catch (e) { alert("daemon 操作失败：" + e.message); }
  btn.disabled = false;
  loadStatus();
}

/* ================= 聊天页 ================= */
let sessions = [], currentSessionId = null, messages = [];
let isThinking = false, selectionMode = false, selectedMsgs = new Set();
let attachments = [], streamAbort = null;
let profiles = [], activeProfileId = "";

function renderChat() {
  $("content").innerHTML = `
  <div class="chat-layout">
    <div class="session-sidebar">
      <div class="session-header"><span>会话</span><button class="btn sm primary" onclick="newSession()">＋ 新建</button></div>
      <div id="sessionList"></div>
    </div>
    <div class="chat-pane">
      <div class="chat-toolbar">
        <button class="btn sm" onclick="toggleSelection()">${selectionMode ? "退出选择" : "选择消息"}</button>
        <button class="btn sm" id="delSelBtn" style="display:none" onclick="deleteSelected()">删除所选</button>
        <span class="muted" id="selCount"></span>
        <span class="spacer"></span>
        <button class="btn sm" onclick="exportChat()">导出 Markdown</button>
      </div>
      <div id="messages"></div>
      <div class="chat-input-bar">
        <div id="attachments"></div>
        <div id="toolRow">
          <select id="modelSel" title="模型预设（绑定当前会话）" onchange="onModelSelChange()"></select>
          <span class="toolRow-hint">模型</span>
        </div>
        <div id="inputRow">
          <textarea id="input" placeholder="输入消息，Enter 发送，Shift+Enter 换行"></textarea>
          <button id="send" onclick="sendMsg()">发送</button>
          <button class="icon-btn" title="添加附件" onclick="addAttachment()">＋</button>
          <button id="stopBtn" style="display:none" onclick="stopGen()">中断</button>
        </div>
      </div>
    </div>
  </div>`;
  const input = $("input");
  input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); } });
  loadSessions();
  if (currentSessionId) openSession(currentSessionId);
  else { messages = []; renderMessages(); }
  loadModelSel();
  input.focus();
}

/* ---- 聊天框模型选择器（列全部预设，绑当前会话） ---- */
async function loadModelSel() {
  const sel = $("modelSel");
  if (!sel) return;
  try {
    const c = await ipc("config_get", {});
    profiles = (c.profiles || []).filter(p => p && p.id);
    activeProfileId = c.active_profile_id || "";
  } catch (e) { profiles = []; }
  let cur = activeProfileId;
  if (currentSessionId) {
    try { const s = await ipc("session_get", { session_id: currentSessionId }); cur = s.profile_id || cur; } catch (e) {}
  }
  sel.innerHTML = profiles.map(p => `<option value="${esc(p.id)}" ${p.id === cur ? "selected" : ""}>${esc(p.api_model || p.api_name || p.name || p.id)}</option>`).join("") || '<option value="">默认</option>';
}
async function onModelSelChange() {
  const sel = $("modelSel");
  if (!sel || !currentSessionId) return;
  const pid = sel.value;
  try { await ipc("session_set_profile", { session_id: currentSessionId, profile_id: pid }); }
  catch (e) { alert("绑定模型失败：" + e.message); }
}

async function loadSessions() {
  try {
    const d = await ipc("session_list", {});
    sessions = d.items || [];
  } catch (e) { sessions = []; }
  const list = $("sessionList");
  if (!list) return;
  if (!sessions.length) { list.innerHTML = '<div class="empty">暂无会话</div>'; return; }
  list.innerHTML = sessions.map(s => `
    <div class="sess-item ${s.id === currentSessionId ? "active" : ""}" onclick="openSession('${s.id}')">
      <span class="t">${esc(s.title || "新会话")}</span>
      <span class="ops">
        <button title="重命名" onclick="event.stopPropagation();renameSession('${s.id}')">改</button>
        <button title="删除" onclick="event.stopPropagation();deleteSession('${s.id}')">删</button>
      </span>
    </div>`).join("");
}

async function newSession() {
  try {
    const d = await ipc("session_new", { title: "新会话" });
    currentSessionId = d.session.id;
    messages = d.session.messages || [];
    selectionMode = false; selectedMsgs.clear();
    loadSessions(); renderMessages();
  } catch (e) { alert("新建会话失败：" + e.message); }
}
async function openSession(sid) {
  currentSessionId = sid;
  try {
    const d = await ipc("session_get", { session_id: sid });
    messages = (d.session && d.session.messages) || [];
  } catch (e) { messages = []; }
  selectionMode = false; selectedMsgs.clear();
  loadSessions(); renderMessages();
}
async function renameSession(sid) {
  const s = sessions.find(x => x.id === sid);
  const title = prompt("重命名会话：", s ? s.title : "");
  if (title == null) return;
  try { await ipc("session_rename", { session_id: sid, title: title.trim() || "新会话" }); loadSessions(); }
  catch (e) { alert("重命名失败：" + e.message); }
}
async function deleteSession(sid) {
  if (!confirm("确定删除该会话？此操作不可恢复。")) return;
  try {
    await ipc("session_delete", { session_id: sid });
    if (currentSessionId === sid) { currentSessionId = null; messages = []; renderMessages(); }
    loadSessions();
  } catch (e) { alert("删除失败：" + e.message); }
}

function renderMessages() {
  const box = $("messages");
  if (!box) return;
  if (!messages.length) { box.innerHTML = '<div class="empty">开始一段新对话吧</div>'; return; }
  box.innerHTML = messages.map((m, i) => {
    const role = m.role === "user" ? "user" : "assistant";
    const who = role === "user" ? "你" : "Brickery";
    const sel = selectionMode ? ` onclick="toggleMsgSel(${i})"` : "";
    const selCls = selectedMsgs.has(i) ? " selected" : "";
    const meta = (m.used_tools && m.used_tools.length ? "工具: " + m.used_tools.join(", ") : "") + (m.used_skills && m.used_skills.length ? (m.used_tools && m.used_tools.length ? " · " : "") + "技能: " + m.used_skills.join(", ") : "");
    return `<div class="msg ${role}${selCls}" data-i="${i}"${sel}>
      <div>
        <div class="who">${who}</div>
        <div class="bubble">${esc(m.text || "")}<span class="copy" onclick="event.stopPropagation();copyMsg(${i})">复制</span></div>
        ${meta ? `<div class="meta">${esc(meta)}</div>` : ""}
      </div>
    </div>`;
  }).join("");
  box.scrollTop = box.scrollHeight;
}
function toggleSelection() {
  selectionMode = !selectionMode;
  if (!selectionMode) selectedMsgs.clear();
  renderChat();
}
function toggleMsgSel(i) {
  if (selectedMsgs.has(i)) selectedMsgs.delete(i); else selectedMsgs.add(i);
  const el = document.querySelector('.msg[data-i="' + i + '"]');
  if (el) el.classList.toggle("selected", selectedMsgs.has(i));
  const c = $("selCount"); if (c) c.textContent = selectedMsgs.size ? "已选 " + selectedMsgs.size + " 条" : "";
  const b = $("delSelBtn"); if (b) b.style.display = selectedMsgs.size ? "inline-block" : "none";
}
async function deleteSelected() {
  if (!selectedMsgs.size) return;
  if (!confirm("删除所选 " + selectedMsgs.size + " 条消息？")) return;
  const ids = [...selectedMsgs].map(i => messages[i]).filter(m => m && m.id).map(m => m.id);
  if (ids.length) {
    try {
      const r = await fetch("/api/messages/delete", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ session_id: currentSessionId, ids }) });
      await r.json();
    } catch (e) { alert("删除失败：" + e.message); return; }
  }
  messages = messages.filter((m, i) => !selectedMsgs.has(i));
  selectedMsgs.clear(); selectionMode = false;
  renderChat();
}
function copyMsg(i) {
  const m = messages[i]; if (!m) return;
  navigator.clipboard.writeText(m.text || "").then(() => { /* 已复制 */ });
}
function exportChat() {
  if (!messages.length) { alert("当前会话无消息可导出"); return; }
  const s = sessions.find(x => x.id === currentSessionId);
  let md = "# " + (s ? s.title : "会话") + "\\n\\n";
  for (const m of messages) {
    md += "**" + (m.role === "user" ? "你" : "Brickery") + "**\\n\\n" + (m.text || "") + "\\n\\n";
  }
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = ((s ? s.title : "会话") || "会话") + ".md";
  a.click();
  URL.revokeObjectURL(a.href);
}
function addAttachment() {
  const inp = document.createElement("input");
  inp.type = "file"; inp.multiple = true;
  inp.onchange = () => {
    for (const f of inp.files) attachments.push(f.name);
    renderAttachments();
  };
  inp.click();
}
function renderAttachments() {
  const box = $("attachments");
  if (!box) return;
  box.innerHTML = attachments.map((a, i) => '<span class="attach-chip">' + esc(a) + '<span class="x" onclick="removeAttachment(' + i + ')">×</span></span>').join("");
}
function removeAttachment(i) { attachments.splice(i, 1); renderAttachments(); }

/* 工具确认轮询：模型请求 MEDIUM/HIGH 风险工具时，后端阻塞等裁决，
   前端必须轮询 confirm_next 取待确认项并弹窗，否则 chat 会卡到超时。 */
let confirmPolling = false;
/* App 风格确认弹窗（替代原生 confirm：原生在 webview 中样式不统一且可能被拦截） */
function appConfirm(title, body, okLabel, cancelLabel) {
  return new Promise(resolve => {
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = '<div class="modal-box" style="width:460px">'
      + '<h3>' + esc(title) + '</h3>'
      + '<div style="font-size:13px;line-height:1.7;white-space:pre-wrap;color:var(--text);word-break:break-all;margin-bottom:16px">' + esc(body) + '</div>'
      + '<div class="modal-foot">'
      + '<button class="btn" id="appCfmNo">' + esc(cancelLabel || "取消") + '</button>'
      + '<button class="btn primary" id="appCfmYes">' + esc(okLabel || "确定") + '</button>'
      + '</div></div>';
    const close = (v) => { mask.remove(); resolve(v); };
    mask.querySelector("#appCfmNo").onclick = () => close(false);
    mask.querySelector("#appCfmYes").onclick = () => close(true);
    mask.addEventListener("click", e => { if (e.target === mask) close(false); });
    document.body.appendChild(mask);
  });
}
async function confirmPoller() {
  while (confirmPolling) {
    let item = null;
    try {
      const r = await ipc("confirm_next", { wait: 25 });
      item = r && r.confirmation;
    } catch (e) { break; }
    if (!item) continue;
    let argsTxt = "";
    try {
      const a = item.args || {};
      const keys = Object.keys(a);
      if (keys.length) argsTxt = "\\n参数：" + JSON.stringify(a).slice(0, 400);
    } catch (e) {}
    const ok = await appConfirm(
      "等待确认：执行工具",
      "工具「" + (item.tool_name || "?") + "」" + argsTxt + "\\n\\n允许则继续执行，拒绝则跳过该工具。",
      "允许执行", "拒绝");
    try { await ipc("confirm_resolve", { id: item.id, decision: ok }); } catch (e) {}
  }
}
/* App 风格单行输入弹窗（替代原生 prompt：webview 中原生 prompt 常被拦截导致无法输入） */
function appPrompt(title, placeholder, initial) {
  return new Promise(resolve => {
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = '<div class="modal-box" style="width:440px">'
      + '<h3>' + esc(title) + '</h3>'
      + '<input id="appPromptInput" class="ipt" style="width:100%;box-sizing:border-box" placeholder="' + esc(placeholder || "") + '" value="' + esc(initial || "") + '">'
      + '<div class="modal-foot">'
      + '<button class="btn" id="appPromptNo">取消</button>'
      + '<button class="btn primary" id="appPromptYes">确定</button>'
      + '</div></div>';
    const input = mask.querySelector("#appPromptInput");
    const close = (v) => { mask.remove(); resolve(v); };
    const submit = () => close(input.value);
    mask.querySelector("#appPromptNo").onclick = () => close(null);
    mask.querySelector("#appPromptYes").onclick = submit;
    input.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
    mask.addEventListener("click", e => { if (e.target === mask) close(null); });
    document.body.appendChild(mask);
    input.focus();
  });
}
/* App 风格提示（替代原生 alert） */
function appAlert(msg) {
  return new Promise(resolve => {
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = '<div class="modal-box" style="width:400px">'
      + '<h3>提示</h3>'
      + '<div style="font-size:13px;line-height:1.7;white-space:pre-wrap;color:var(--text);word-break:break-all;margin-bottom:16px">' + esc(msg) + '</div>'
      + '<div class="modal-foot"><button class="btn primary" id="appAlertOk">确定</button></div>'
      + '</div></div>';
    const close = () => { mask.remove(); resolve(); };
    mask.querySelector("#appAlertOk").onclick = close;
    mask.addEventListener("click", e => { if (e.target === mask) close(); });
    document.body.appendChild(mask);
  });
}

async function sendMsg() {
  const input = $("input");
  const text = input.value.trim();
  if (!text || isThinking) return;
  let full = text;
  if (attachments.length) full = "[附件: " + attachments.join(", ") + "]\\n" + text;
  input.value = ""; attachments = []; renderAttachments();
  messages.push({ role: "user", text: full });
  renderMessages();
  isThinking = true;
  confirmPolling = true; confirmPoller();
  const send = $("send"), stop = $("stopBtn");
  send.disabled = true; send.style.display = "none"; stop.style.display = "inline-block";
  let reply = "";
  const aiIdx = messages.length;
  messages.push({ role: "assistant", text: "" });
  renderMessages();
  const box = $("messages");
  const typing = document.createElement("div");
  typing.className = "typing"; typing.textContent = "准备中…";
  box.appendChild(typing); box.scrollTop = box.scrollHeight;
  const showStatus = (s) => { typing.textContent = s; box.scrollTop = box.scrollHeight; };
  const updateBubble = () => {
    const el = document.querySelector('.msg[data-i="' + aiIdx + '"] .bubble');
    if (!el) return;
    const cp = el.querySelector(".copy");
    el.textContent = reply;
    if (cp) el.appendChild(cp);
    box.scrollTop = box.scrollHeight;
  };
  try {
    await ipcStream("chat", { message: full, session_id: currentSessionId, stream: true },
      delta => { reply += delta; updateBubble(); },
      data => {
        messages[aiIdx] = { role: "assistant", text: data.reply || reply, used_tools: data.used_tools, used_skills: data.used_skills };
        if (data.session_id) currentSessionId = data.session_id;
        typing.remove(); updateBubble(); loadSessions();
      },
      err => {
        messages[aiIdx] = { role: "assistant", text: "错误：" + err };
        typing.remove(); updateBubble();
      },
      s => { showStatus(s); });
  } catch (e) {
    messages[aiIdx] = { role: "assistant", text: "网络错误：" + e.message };
    typing.remove(); updateBubble();
  } finally {
    confirmPolling = false;
    isThinking = false;
    send.disabled = false; send.style.display = ""; stop.style.display = "none";
    input.focus();
  }
}
async function stopGen() {
  try { await ipc("chat_cancel", {}); } catch (e) {}
}

/* ================= 技能库 ================= */
async function renderSkills() {
  $("content").innerHTML = '<div class="empty">加载中...</div>';
  let skills = [], tools = [];
  try { const d = await ipc("skill_list", {}); skills = d.items || []; } catch (e) {}
  try { const d = await ipc("tool_list", {}); tools = d.items || []; } catch (e) {}
  $("content").innerHTML = `
    <div class="grid2">
      <div>
        <div class="card"><h3>技能</h3><div class="hint">共 ${skills.length} 个技能 · 点击开关启停，可手动触发</div>
          <div id="skillList">${skills.map(s => `
            <div class="item-card">
              <div class="head">
                <span class="name">${esc(s.name)}</span>
                <div class="row">
                  <label class="switch"><input type="checkbox" ${s.disabled ? "" : "checked"} onchange="toggleSkill('${esc(s.name)}', this.checked)"><span class="slider"></span></label>
                  <button class="btn sm" onclick="triggerSkill('${esc(s.name)}')">触发</button>
                </div>
              </div>
              ${s.summary ? `<div class="desc">${esc(s.summary)}</div>` : ""}
              ${s.trigger && s.trigger.length ? `<div class="triggers">${s.trigger.map(t => '<span class="tag">' + esc(t) + '</span>').join("")}</div>` : ""}
            </div>`).join("") || '<div class="empty">暂无技能</div>'}
          </div>
        </div>
      </div>
      <div>
        <div class="card"><h3>工具</h3><div class="hint">共 ${tools.length} 个工具</div>
          <div id="toolList">${tools.map(t => `
            <div class="item-card">
              <div class="head">
                <span class="name">${esc(t.name)}</span>
                <div class="row">
                  <span class="tag ${t.disabled ? "off" : "on"}">${t.disabled ? "停用" : "启用"}</span>
                  <label class="switch"><input type="checkbox" ${t.disabled ? "" : "checked"} onchange="toggleTool('${esc(t.name)}', this.checked)"><span class="slider"></span></label>
                  <button class="btn sm" ${t.executable ? "" : "disabled"} onclick="triggerTool('${esc(t.name)}')">触发</button>
                </div>
              </div>
              <div class="desc">${esc(t.description || "")}</div>
            </div>`).join("") || '<div class="empty">暂无工具</div>'}
          </div>
        </div>
      </div>
    </div>`;
}
async function toggleSkill(name, on) {
  try { await ipc("skill_toggle", { name, disabled: !on }); } catch (e) { alert("操作失败：" + e.message); }
}
async function toggleTool(name, on) {
  try { await ipc("tool_toggle", { name, disabled: !on }); } catch (e) { alert("操作失败：" + e.message); }
}
async function triggerSkill(name) {
  const r = prompt("触发技能「" + name + "」，输入指令（留空用技能内容）：");
  if (r === null) return;
  try {
    const d = await ipc("skill_trigger", { name, session_id: currentSessionId });
    alert("技能已触发：\\n" + (d.reply || "").slice(0, 500));
  } catch (e) { alert("触发失败：" + e.message); }
}
async function triggerTool(name) {
  const args = prompt("触发工具「" + name + "」，输入 JSON 参数（留空为 {}）：");
  if (args === null) return;
  let parsed = {};
  if (args.trim()) { try { parsed = JSON.parse(args); } catch (e) { alert("参数不是合法 JSON"); return; } }
  try {
    const d = await ipc("tool_trigger", { name, args: parsed });
    alert((d.ok ? "执行成功" : "执行失败") + "：\\n" + (d.output || "").slice(0, 500));
  } catch (e) { alert("触发失败：" + e.message); }
}

/* ================= 积木市场 ================= */
async function renderMarket() {
  $("content").innerHTML = '<div class="empty">加载中...</div>';
  let items = [], err = "";
  try {
    const d = await ipc("skill_library_list", { force: false });
    if (!d.ok) err = d.error || "加载失败";
    items = d.items || [];
  } catch (e) { err = e.message; }
  const installed = items.filter(i => i.installed);
  const upgradable = items.filter(i => i.installed && i.installed_version && i.version && i.installed_version !== i.version);
  $("content").innerHTML = `
    <div class="card">
      <div class="row between">
        <h3 style="margin:0">积木市场 · 热插拔</h3>
        <div class="row">
          <button class="btn sm" onclick="renderMarket()">刷新</button>
        </div>
      </div>
      <div class="hint">没网也能装积木：在工坊或网页点「下载 .brick」，把文件拖到这里，或点按钮选择文件。可一次拖多个。</div>
      <div class="dropzone" id="brickDropzone" onclick="pickBrickFiles()">
        <div class="dz-title">导入积木包（.brick）</div>
        <div>点击选择文件，或把 .brick 文件拖到这里 · 可一次拖多个 / 选多个</div>
      </div>
      <input type="file" id="brickFileInput" accept=".brick,application/zip" multiple style="display:none" onchange="onBrickFilesChosen(this)">
      <div class="hint">已装 ${installed.length} / 共 ${items.length} 块 · 可升级 ${upgradable.length} 块 · 安装/卸载后重启生效</div>
      ${err ? `<div class="err-text">${esc(err)}</div>` : ""}
      <div id="marketList">
        ${items.map(b => `
          <div class="item-card">
            <div class="head">
              <span class="name">${esc(b.name)}</span>
              ${b.installed ? `<span class="tag on">${b.installed_via === "offline" ? "已装（离线）" : "已装"}</span>` : `<span class="tag off">未装</span>`}
              ${b.category ? `<span class="tag">${esc(b.category)}</span>` : ""}
              <span class="tag">v${esc(b.version || "?")}</span>
              ${b.installed && b.installed_version && b.version && b.installed_version !== b.version ? `<span class="tag warn">可升级 ${esc(b.installed_version)}→${esc(b.version)}</span>` : ""}
            </div>
            ${b.summary ? `<div class="desc">${esc(b.summary)}</div>` : ""}
            <div class="row" style="margin-top:8px">
              ${b.installed
                ? `<button class="btn sm danger" onclick="marketUninstall('${esc(b.id)}')">卸载</button>`
                : `<button class="btn sm primary" onclick="marketInstall('${esc(b.id)}')">安装</button>`}
              ${b.installed && b.installed_version && b.version && b.installed_version !== b.version
                ? `<button class="btn sm" onclick="marketUpgrade('${esc(b.id)}')">升级</button>` : ""}
              <button class="btn sm" onclick="marketReview('${esc(b.id)}')">详情</button>
            </div>
          </div>`).join("") || '<div class="empty">市场为空或未连接</div>'}
      </div>
    </div>`;
  const dz = $("brickDropzone");
  if (dz) {
    dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
    dz.addEventListener("drop", async e => {
      e.preventDefault(); dz.classList.remove("drag");
      const paths = brickPathsFromList(e.dataTransfer.files);
      if (!paths.length) { alert("请拖入 .brick 文件"); return; }
      await marketImportPreview(paths);
    });
  }
}
/* ---- 离线导入 .brick（预览 → 确认弹窗 → 安装 → 结果反馈） ---- */
let importPending = [];
function brickPathsFromList(fileList) {
  const out = [];
  for (const f of (fileList || [])) {
    const p = f.path || f.webkitRelativePath || "";
    if (p && p.toLowerCase().endsWith(".brick")) out.push(p);
  }
  return out;
}
function pickBrickFiles() { const inp = $("brickFileInput"); if (inp) inp.click(); }
async function onBrickFilesChosen(input) {
  const paths = brickPathsFromList(input.files);
  if (!paths.length) { alert("请选择 .brick 积木包文件（可多选）"); input.value = ""; return; }
  input.value = "";
  await marketImportPreview(paths);
}
async function marketImportPreview(paths) {
  let d;
  try { d = await ipc("skill_library_import_preview", { files: paths }); }
  catch (e) { alert("解析积木包失败：" + e.message); return; }
  if (!d.ok) { alert("解析积木包失败：" + (d.error || "")); return; }
  const items = (d.items || []).filter(i => i.path);
  if (!items.length) { alert("所选文件中没有可导入的积木"); return; }
  importPending = items;
  showImportConfirm();
}
function showImportConfirm() {
  const okItems = importPending.filter(i => i.ok);
  const badItems = importPending.filter(i => !i.ok);
  const rows = okItems.map(b => `
    <div class="import-item">
      <div class="i-head">
        <strong>${esc(b.name || b.id || "未命名")}</strong>
        <span class="tag">v${esc(b.version || "?")}</span>
        ${b.author ? `<span class="tag">${esc(b.author)}</span>` : ""}
        ${(b.packed_by && b.packed_by !== "brickery") ? `<span class="tag warn">来源未知</span>` : `<span class="tag on">官方打包</span>`}
      </div>
      ${b.summary ? `<div class="i-desc">${esc(b.summary)}</div>` : ""}
    </div>`).join("");
  const badRows = badItems.map(b => `
    <div class="import-item err">
      <div class="i-head"><strong>${esc((b.path || "").split("/").pop() || "文件")}</strong><span class="tag off">校验失败</span></div>
      <div class="i-desc">${esc(b.error || "无法解析")}</div>
    </div>`).join("");
  showImportModal(`
    <h3>导入积木</h3>
    <div class="muted" style="margin-bottom:10px">将安装以下 ${okItems.length} 块积木${badItems.length ? `（另有 ${badItems.length} 个文件校验失败，不会安装）` : ""}：</div>
    ${rows || '<div class="empty">没有可安装的积木</div>'}
    ${badRows}
    <div class="modal-foot">
      <button class="btn" onclick="closeImportModal()">取消</button>
      ${okItems.length ? `<button class="btn primary" onclick="marketImportDo()">安装</button>` : `<button class="btn primary" onclick="closeImportModal()">关闭</button>`}
    </div>
  `);
}
async function marketImportDo() {
  const okPaths = [...new Set(importPending.filter(i => i.ok).map(i => i.path))];
  closeImportModal();
  if (!okPaths.length) return;
  let d;
  try { d = await ipc("skill_library_import", { files: okPaths }); }
  catch (e) { showImportResult([], "导入失败：" + e.message); return; }
  showImportResult((d && d.items) || [], !d.ok ? (d.error || "导入失败") : "");
  renderMarket();
}
function showImportResult(items, topErr) {
  const rows = (items || []).map(r => r.ok
    ? `<div class="import-item ok"><div class="i-head"><strong>${esc(r.name || r.id || "积木")}</strong><span class="tag on">已安装</span>${r.version ? `<span class="tag">v${esc(r.version)}</span>` : ""}</div></div>`
    : `<div class="import-item err"><div class="i-head"><strong>${esc(r.name || r.id || (r.path || "").split("/").pop() || "积木")}</strong><span class="tag off">失败</span></div><div class="i-desc">${esc(r.error || "导入失败")}</div></div>`).join("");
  showImportModal(`
    <h3>导入结果</h3>
    ${topErr ? `<div class="err-text" style="margin-bottom:8px">${esc(topErr)}</div>` : ""}
    ${rows || '<div class="empty">未返回结果</div>'}
    <div class="modal-foot"><button class="btn primary" onclick="closeImportModal()">完成</button></div>
  `);
}
function showImportModal(html) {
  let m = $("importModal");
  if (!m) {
    m = document.createElement("div");
    m.id = "importModal";
    m.className = "modal-mask";
    m.style.display = "none";
    m.innerHTML = '<div class="modal-box" id="importModalBox" style="width:520px"></div>';
    m.addEventListener("click", e => { if (e.target === m) closeImportModal(); });
    document.body.appendChild(m);
  }
  $("importModalBox").innerHTML = html;
  m.style.display = "flex";
}
function closeImportModal() { const m = $("importModal"); if (m) m.style.display = "none"; }
async function marketInstall(id) {
  if (!confirm("安装积木 " + id + "？安装后重启生效。")) return;
  try {
    const d = await ipc("skill_library_install", { id });
    if (!d.ok) { alert("安装失败：" + (d.error || "")); return; }
    alert("已安装 " + id + "，重启后生效");
    renderMarket();
  } catch (e) { alert("安装失败：" + e.message); }
}
async function marketUninstall(id) {
  if (!confirm("卸载积木 " + id + "？卸载后重启生效。")) return;
  try {
    const d = await ipc("skill_library_uninstall", { id });
    if (!d.ok) { alert("卸载失败：" + (d.error || "")); return; }
    alert("已卸载 " + id + "，重启后生效");
    renderMarket();
  } catch (e) { alert("卸载失败：" + e.message); }
}
async function marketUpgrade(id) {
  try {
    const d = await ipc("skill_library_upgrade", { id });
    if (!d.ok) { alert("升级失败：" + (d.error || "")); return; }
    alert("已升级 " + id + (d.to ? " → v" + d.to : "") + "，重启后生效");
    renderMarket();
  } catch (e) { alert("升级失败：" + e.message); }
}
async function marketReview(id) {
  try {
    const d = await ipc("skill_library_review", { id });
    if (!d.ok) { alert("获取详情失败：" + (d.error || "")); return; }
    const s = d.skill || {};
    alert("【" + (s.name || id) + "】v" + (s.version || "?") + "\\n" + (s.description || s.summary || "（无描述）"));
  } catch (e) { alert("获取详情失败：" + e.message); }
}

/* ================= 记忆柜 ================= */
async function renderCabinet() {
  $("content").innerHTML = '<div class="empty">加载中...</div>';
  let drawers = [];
  try { const d = await ipc("drawer_list", {}); drawers = d.items || []; } catch (e) {}
  $("content").innerHTML = `
    <div class="card">
      <div class="row between">
        <h3 style="margin:0">记忆柜 · 项目抽屉</h3>
        <button class="btn sm primary" onclick="newDrawer()">＋ 新建抽屉</button>
      </div>
      <div class="hint">共 ${drawers.length} 个抽屉</div>
      <div id="drawerList">${drawers.map(d => `
        <div class="list-item">
          <div>
            <div class="title">${esc(d.title || "未命名")}</div>
            <div class="sub">${esc(d.drawer_id || d.id || "")} · ${fmtTime(d.created_at)}</div>
          </div>
          <div class="row">
            <button class="btn sm" onclick="openDrawer('${esc(d.drawer_id || d.id)}')">打开</button>
            <button class="btn sm danger" onclick="deleteDrawer('${esc(d.drawer_id || d.id)}')">删除</button>
          </div>
        </div>`).join("") || '<div class="empty">暂无抽屉</div>'}
      </div>
    </div>`;
}
async function newDrawer() {
  const title = prompt("抽屉标题：");
  if (title == null) return;
  try { await ipc("drawer_create", { title: title.trim() || "未命名项目" }); renderCabinet(); }
  catch (e) { alert("创建失败：" + e.message); }
}
async function deleteDrawer(id) {
  if (!confirm("删除抽屉 " + id + "？")) return;
  try { await ipc("drawer_delete", { drawer_id: id }); renderCabinet(); }
  catch (e) { alert("删除失败：" + e.message); }
}
async function openDrawer(id) {
  let d = null;
  try { const r = await ipc("drawer_get", { drawer_id: id }); d = r.drawer; } catch (e) {}
  if (!d) { alert("抽屉不存在"); return; }
  const nodes = d.nodes || [], edges = d.edges || [];
  $("content").innerHTML = `
    <div class="card">
      <div class="row between">
        <h3 style="margin:0">${esc(d.title || "未命名")} · 工作台</h3>
        <button class="btn sm" onclick="renderCabinet()">← 返回文件柜</button>
      </div>
      <div class="hint">${esc(d.drawer_id || d.id || "")} · 节点 ${nodes.length} · 边 ${edges.length} · kit ${(d.kit || []).length ? esc((d.kit || []).join(", ")) : "未预配"}</div>
      <div class="row wrap" style="margin-bottom:10px">
        <button class="btn sm" onclick="addNode('${esc(d.drawer_id || d.id)}')">＋ 节点</button>
        <button class="btn sm" onclick="addEdge('${esc(d.drawer_id || d.id)}')">＋ 边</button>
        <button class="btn sm" onclick="syncRecordbook('${esc(d.drawer_id || d.id)}')">同步记录本</button>
        <button class="btn sm" onclick="editKit('${esc(d.drawer_id || d.id)}')">kit 预配</button>
      </div>
      <div id="graphCanvas"></div>
      <div class="grid2" style="margin-top:14px">
        <div class="card">
          <h3>节点</h3>
          ${nodes.map(n => `
            <div class="list-item">
              <div><div class="title">${esc(n.label || n.node_id || n.id)}</div><div class="sub">${esc(n.type || "")} · ${esc(n.node_id || n.id || "")}</div></div>
              <div class="row">
                <button class="btn sm" onclick="editNode('${esc(n.node_id || n.id)}')">编辑</button>
                <button class="btn sm" onclick="viewNode('${esc(n.node_id || n.id)}')">解读</button>
                <button class="btn sm danger" onclick="delNode('${esc(n.node_id || n.id)}')">删</button>
              </div>
            </div>`).join("") || '<div class="empty">暂无节点</div>'}
        </div>
        <div class="card">
          <h3>记录本（R/S/P 三段）</h3>
          <div id="recordbookEdit"></div>
        </div>
      </div>
      <div class="card" style="margin-top:14px">
        <h3>项目聊天</h3>
        <div id="drawerChatBox"></div>
      </div>
    </div>`;
  drawGraph(nodes, edges);
  renderRecordbook(d);
  renderDrawerChat(d);
}
async function addNode(did) {
  const label = prompt("节点标签："); if (label == null) return;
  const type = prompt("节点类型（goal/decision/risk/task/resource/rule/status/progress）：") || "task";
  const content = prompt("节点内容（可留空）：") || "";
  try { await ipc("node_add", { drawer_id: did, type, label, content }); openDrawer(did); }
  catch (e) { alert("添加失败：" + e.message); }
}
async function addEdge(did) {
  const source = prompt("源节点 id："); if (source == null) return;
  const target = prompt("目标节点 id："); if (target == null) return;
  const relation = prompt("关系（可留空）：") || "";
  try { await ipc("edge_add", { drawer_id: did, source, target, relation }); openDrawer(did); }
  catch (e) { alert("添加失败：" + e.message); }
}
async function delNode(nid) {
  if (!confirm("删除节点 " + nid + "？")) return;
  try { await ipc("node_delete", { node_id: nid }); renderCabinet(); }
  catch (e) { alert("删除失败：" + e.message); }
}
async function syncRecordbook(did) {
  try { await ipc("recordbook_sync", { drawer_id: did }); openDrawer(did); }
  catch (e) { alert("同步失败：" + e.message); }
}
async function viewNode(nid) {
  try {
    const d = await ipc("explain_node", { node_id: nid });
    const r = d.result || {};
    if (r.degraded) { alert("（无引擎，展示原文）\\n" + (r.raw || "").slice(0, 800)); }
    else { alert("节点解读：\\n" + (r.explanation || "").slice(0, 800)); }
  } catch (e) { alert("解读失败：" + e.message); }
}

/* ---- E2 节点详情编辑 ---- */
let editingNode = null;
async function editNode(nid) {
  let node = null;
  try {
    const d = await ipc("drawer_list", {});
    const drawers = d.items || [];
    for (const dr of drawers) {
      const g = await ipc("drawer_get", { drawer_id: dr.drawer_id || dr.id });
      const nd = (g.drawer.nodes || []).find(n => (n.node_id || n.id) === nid);
      if (nd) { node = nd; break; }
    }
  } catch (e) {}
  if (!node) { alert("节点不存在"); return; }
  editingNode = nid;
  const box = $("recordbookEdit");
  const panel = document.createElement("div");
  panel.className = "card";
  panel.style.marginTop = "10px";
  panel.innerHTML = `
    <h3>编辑节点</h3>
    <div class="field"><label>标题</label><input id="enLabel" value="${esc(node.label || "")}"></div>
    <div class="field"><label>类型</label>
      <select id="enType">${["goal","decision","risk","task","resource","rule","status","progress"].map(t => `<option value="${t}" ${t === node.type ? "selected" : ""}>${t}</option>`).join("")}</select>
    </div>
    <div class="field"><label>内容</label><textarea id="enContent" rows="4">${esc(node.content || "")}</textarea></div>
    <div class="row">
      <button class="btn primary" onclick="saveNode()">保存</button>
      <button class="btn ghost" onclick="cancelEditNode()">取消</button>
    </div>`;
  box.appendChild(panel);
}
async function saveNode() {
  if (!editingNode) return;
  try {
    await ipc("node_update", { node_id: editingNode, label: $("enLabel").value.trim(), content: $("enContent").value, type: $("enType").value });
    editingNode = null;
    const d = await ipc("drawer_list", {});
    const drawers = d.items || [];
    for (const dr of drawers) {
      const g = await ipc("drawer_get", { drawer_id: dr.drawer_id || dr.id });
      if ((g.drawer.nodes || []).some(n => (n.node_id || n.id) === editingNode)) { openDrawer(dr.drawer_id || dr.id); return; }
    }
    renderCabinet();
  } catch (e) { alert("保存失败：" + e.message); }
}
function cancelEditNode() {
  editingNode = null;
  const box = $("recordbookEdit");
  if (box) box.querySelectorAll(".card").forEach(c => c.remove());
}

/* ---- E4 记录本 R/S/P 三段编辑 ---- */
function renderRecordbook(d) {
  const box = $("recordbookEdit");
  if (!box) return;
  const nodes = d.nodes || [];
  const secs = [["R", "规则（Rules）"], ["S", "现状（Status）"], ["P", "进度（Progress）"]];
  const anchors = {};
  for (const n of nodes) {
    if (n.type === "anchor" && /::[RSP]$/.test(n.node_id || n.id)) anchors[(n.node_id || n.id).slice(-1)] = n;
  }
  box.innerHTML = secs.map(([code, title]) => {
    const a = anchors[code];
    return `
      <div class="field">
        <label>${title}</label>
        <textarea id="rb_${code}" rows="3" placeholder="（待补充）">${esc(a ? (a.content || "") : "")}</textarea>
      </div>`;
  }).join("") + `
    <div class="row">
      <button class="btn primary" onclick="saveRecordbook('${esc(d.drawer_id || d.id)}')">保存记录本</button>
      <button class="btn ghost" onclick="syncRecordbook('${esc(d.drawer_id || d.id)}')">从节点重生成</button>
    </div>`;
}
async function saveRecordbook(did) {
  try {
    for (const code of ["R", "S", "P"]) {
      const nid = did + "::" + code;
      await ipc("node_update", { node_id: nid, content: ($("rb_" + code) ? $("rb_" + code).value : "") });
    }
    await ipc("recordbook_sync", { drawer_id: did });
    openDrawer(did);
  } catch (e) { alert("保存失败：" + e.message); }
}

/* ---- E5 项目独立聊天 ---- */
let drawerChatHistory = [];
function renderDrawerChat(d) {
  const box = $("drawerChatBox");
  if (!box) return;
  drawerChatHistory = [];
  box.innerHTML = `
    <div id="dcMsgs" class="pre" style="max-height:220px;overflow:auto;margin-bottom:8px">（项目聊天，反馈留在工作台内）</div>
    <div class="row">
      <input id="dcInput" placeholder="输入项目消息，Enter 发送">
      <button class="btn primary" onclick="sendDrawerChat('${esc(d.drawer_id || d.id)}')">发送</button>
    </div>`;
  const inp = $("dcInput");
  inp.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); sendDrawerChat(d.drawer_id || d.id); } });
}
async function sendDrawerChat(did) {
  const inp = $("dcInput");
  const msg = inp ? inp.value.trim() : "";
  if (!msg) return;
  const box = $("dcMsgs");
  drawerChatHistory.push({ role: "user", text: msg });
  if (box) box.textContent = drawerChatHistory.map(m => (m.role === "user" ? "你：" : "Brickery：") + m.text).join("\\n");
  if (inp) inp.value = "";
  try {
    const r = await ipc("drawer_chat", { drawer_id: did, message: msg });
    drawerChatHistory.push({ role: "assistant", text: r.reply || "" });
    if (box) box.textContent = drawerChatHistory.map(m => (m.role === "user" ? "你：" : "Brickery：") + m.text).join("\\n");
  } catch (e) {
    drawerChatHistory.push({ role: "assistant", text: "（项目聊天失败：" + e.message + "）" });
    if (box) box.textContent = drawerChatHistory.map(m => (m.role === "user" ? "你：" : "Brickery：") + m.text).join("\\n");
  }
}

/* ---- E6 抽屉 kit 预配 ---- */
async function editKit(did) {
  let d = null;
  try { const r = await ipc("drawer_get", { drawer_id: did }); d = r.drawer; } catch (e) {}
  if (!d) { alert("抽屉不存在"); return; }
  const skills = [];
  try { const s = await ipc("skill_list", {}); skills.push(...(s.items || []).map(x => x.id || x.name)); } catch (e) {}
  const tools = [];
  try { const t = await ipc("tool_list", {}); tools.push(...(t.items || []).map(x => x.id || x.name)); } catch (e) {}
  const cur = d.kit || [];
  const opts = [...new Set([...skills, ...tools])];
  $("content").innerHTML = `
    <div class="card">
      <div class="row between">
        <h3 style="margin:0">${esc(d.title || "未命名")} · kit 预配</h3>
        <button class="btn sm" onclick="openDrawer('${esc(did)}')">← 返回工作台</button>
      </div>
      <div class="hint">勾选进项目预载的技能/工具（省 token），保存后写入抽屉 kit</div>
      <div class="grid2" style="margin-top:10px">
        ${opts.map(o => `
          <label class="list-item" style="cursor:pointer">
            <input type="checkbox" value="${esc(o)}" ${cur.includes(o) ? "checked" : ""}> ${esc(o)}
          </label>`).join("") || '<div class="empty">无可用技能/工具</div>'}
      </div>
      <div class="row" style="margin-top:12px">
        <button class="btn primary" onclick="saveKit('${esc(did)}')">保存 kit</button>
      </div>
    </div>`;
}
async function saveKit(did) {
  const checked = [...document.querySelectorAll('#content input[type="checkbox"]:checked')].map(i => i.value);
  try { await ipc("drawer_update", { drawer_id: did, kit: checked }); openDrawer(did); }
  catch (e) { alert("保存失败：" + e.message); }
}

/* ================= 记忆 ================= */
let memoryTab = "recall";
async function renderMemory() {
  $("content").innerHTML = `
    <div class="tabs">
      <div class="tab ${memoryTab === "recall" ? "active" : ""}" onclick="setMemoryTab('recall')">对话影</div>
      <div class="tab ${memoryTab === "portrait" ? "active" : ""}" onclick="setMemoryTab('portrait')">用户画像</div>
      <div class="tab ${memoryTab === "core" ? "active" : ""}" onclick="setMemoryTab('core')">固定核</div>
      <div class="tab ${memoryTab === "evolve" ? "active" : ""}" onclick="setMemoryTab('evolve')">自进化</div>
      <div class="tab ${memoryTab === "suggestions" ? "active" : ""}" onclick="setMemoryTab('suggestions')">主动建议</div>
      <div class="tab ${memoryTab === "files" ? "active" : ""}" onclick="setMemoryTab('files')">文件检索</div>
    </div>
    <div id="memoryBody"></div>`;
  renderMemoryTab();
}
function setMemoryTab(t) { memoryTab = t; renderMemory(); }
async function renderMemoryTab() {
  const body = $("memoryBody");
  if (memoryTab === "recall") {
    body.innerHTML = `
      <div class="card">
        <h3>对话影检索</h3>
        <div class="row"><input id="recallQ" style="flex:1;background:#0d1117;border:1px solid var(--line);color:var(--ink);padding:8px 10px;border-radius:4px;font-family:inherit;font-size:12px" placeholder="输入检索词"><button class="btn primary" onclick="doRecall()">检索</button></div>
        <div id="recallOut" style="margin-top:10px"></div>
      </div>`;
  } else if (memoryTab === "portrait") {
    body.innerHTML = '<div class="card"><h3>用户画像</h3><div id="portraitOut"></div></div>';
    loadPortrait();
  } else if (memoryTab === "core") {
    body.innerHTML = `
      <div class="card">
        <h3>固定核</h3>
        <div class="muted" style="margin-bottom:8px">每次对话都会注入给 agent 的长期记忆。手动槽可编辑；智能槽由归纳自动生成，可删除纠错。</div>
        <div id="coreCand" style="margin-bottom:10px"></div>
        <div id="coreManual"></div>
        <div id="coreSmart"></div>
      </div>`;
    loadCore();
  } else if (memoryTab === "evolve") {
    body.innerHTML = `
      <div class="card">
        <h3>自进化</h3>
        <div class="muted" style="margin-bottom:8px">候选积木由成功任务轨迹自动蒸馏，确认后激活；激活积木随使用反馈强化 / 剪枝（成功 +0.1，失败 -0.15，退役不删数据）。</div>
        <h4 style="margin:6px 0 4px">待确认候选</h4>
        <div id="evolveCand" style="margin-bottom:12px"><div class="empty">加载中...</div></div>
        <h4 style="margin:6px 0 4px">已激活积木统计</h4>
        <div id="evolveStats"><div class="empty">加载中...</div></div>
      </div>`;
    loadEvolve();
  } else if (memoryTab === "suggestions") {
    body.innerHTML = `
      <div class="card">
        <h3>主动建议</h3>
        <div class="row"><input id="sugCtx" style="flex:1;background:#0d1117;border:1px solid var(--line);color:var(--ink);padding:8px 10px;border-radius:4px;font-family:inherit;font-size:12px" placeholder="输入上下文（可留空）"><button class="btn primary" onclick="doSuggest()">生成建议</button></div>
        <div id="sugOut" style="margin-top:10px"></div>
      </div>`;
  } else {
    body.innerHTML = `
      <div class="card">
        <h3>文件检索</h3>
        <div class="row"><input id="fileQ" style="flex:1;background:#0d1117;border:1px solid var(--line);color:var(--ink);padding:8px 10px;border-radius:4px;font-family:inherit;font-size:12px" placeholder="输入关键词"><button class="btn primary" onclick="doFileSearch()">搜索</button></div>
        <div id="fileOut" style="margin-top:10px"></div>
      </div>`;
  }
}
async function doRecall() {
  const q = $("recallQ").value.trim(); if (!q) return;
  const out = $("recallOut"); out.innerHTML = '<div class="muted">检索中...</div>';
  try {
    const d = await ipc("recall", { query: q, limit: 10 });
    const items = d.items || [];
    out.innerHTML = items.length ? items.map(it => `
      <div class="list-item"><div><div class="title">${esc(it.text || it.content || "")}</div><div class="sub">${esc(it.project || "")} · ${fmtTime(it.ts || it.created_at)}</div></div></div>`).join("") : '<div class="empty">无结果</div>';
  } catch (e) { out.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function loadPortrait() {
  const out = $("portraitOut");
  try {
    const d = await ipc("portrait", {});
    const items = d.items || [];
    out.innerHTML = items.length ? items.map(it => `
      <div class="list-item">
        <div><div class="title">${esc(it.attribute || "")}</div><div class="sub">${esc(it.value || "")}${it.confidence ? " · 置信 " + it.confidence : ""}</div></div>
        <button class="btn sm" onclick="editPortrait('${esc(it.attribute || "")}')">编辑</button>
      </div>`).join("") : '<div class="empty">暂无画像数据</div>';
  } catch (e) { out.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function editPortrait(attr) {
  const val = prompt("编辑「" + attr + "」的值：");
  if (val == null) return;
  try { await ipc("portrait_update", { attribute: attr, value: val }); loadPortrait(); }
  catch (e) { alert("更新失败：" + e.message); }
}
async function doSuggest() {
  const ctx = $("sugCtx").value.trim();
  const out = $("sugOut"); out.innerHTML = '<div class="muted">生成中...</div>';
  try {
    const d = await ipc("suggestions", { context: ctx, limit: 5 });
    const items = d.items || [];
    out.innerHTML = items.length ? items.map(it => `
      <div class="list-item"><div><div class="title">${esc(it.text || it.content || "")}</div><div class="sub">${esc(it.reason || "")}</div></div></div>`).join("") : '<div class="empty">暂无建议</div>';
  } catch (e) { out.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function doFileSearch() {
  const q = $("fileQ").value.trim(); if (!q) return;
  const out = $("fileOut"); out.innerHTML = '<div class="muted">搜索中...</div>';
  try {
    const d = await ipc("memory_search", { query: q, limit: 10 });
    const items = d.items || [];
    out.innerHTML = items.length ? items.map(it => `
      <div class="list-item"><div><div class="title">${esc(it.title || it.doc_id || "")}</div><div class="sub">${esc(it.path || "")}</div></div></div>`).join("") : '<div class="empty">无结果</div>';
  } catch (e) { out.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function loadCore() {
  const candEl = $("coreCand"), manEl = $("coreManual"), smEl = $("coreSmart");
  if (!manEl) return;
  try {
    const [c, s, cand] = await Promise.all([
      ipc("core_get", {}), ipc("core_smart_get", {}), ipc("core_candidates", {})
    ]);
    const manual = c.core || {};
    const entries = Object.entries(manual);
    manEl.innerHTML = '<h4>手动槽</h4>' + (entries.length ? entries.map(([k, v]) => `
      <div class="list-item">
        <div><div class="title">${esc(k)}</div><div class="sub">${esc(v)}</div></div>
        <button class="btn sm" onclick="editCore('${esc(k)}')">编辑</button>
      </div>`).join("") : '<div class="empty">暂无手动槽，点击下方按钮添加</div>') + `
      <div style="margin-top:10px"><button class="btn primary" style="width:100%;padding:9px 12px;font-size:14px" onclick="addCore()">＋ 添加手动槽</button></div>`;
    const sm = s.items || [];
    smEl.innerHTML = '<h4>智能槽（自动归纳）</h4>' + (sm.length ? sm.map(it => `
      <div class="list-item">
        <div><div class="title">${esc(it.label)}</div><div class="sub">${esc(it.value)} · 置信 ${(it.confidence || 0).toFixed(2)} · 命中 ${it.hit_count || 0}</div></div>
        <button class="btn sm danger" onclick="delSmart('${esc(it.label)}')">删除</button>
      </div>`).join("") : '<div class="empty">暂无智能槽</div>');
    const pend = cand.items || [];
    candEl.innerHTML = pend.length ? '<h4>待确认候选</h4>' + pend.map(it => `
      <div class="list-item">
        <div><div class="title">${esc(it.label)}</div><div class="sub">${esc(it.value)} · 置信 ${(it.confidence || 0).toFixed(2)}</div></div>
        <div class="row" style="gap:6px;flex-wrap:nowrap">
          <button class="btn sm primary" onclick="resolveCand(${it.id})">写入固定核</button>
          <button class="btn sm" onclick="dismissCand(${it.id})">忽略</button>
        </div>
      </div>`).join("") : '';
  } catch (e) { manEl.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function editCore(attr) {
  const val = await appPrompt("编辑「" + attr + "」的值（留空删除）", "输入新的值…", "");
  if (val == null) return;
  try { await ipc("core_set", { items: [{ attribute: attr, value: val }] }); loadCore(); }
  catch (e) { await appAlert("更新失败：" + e.message); }
}
async function addCore() {
  const attr = await appPrompt("新槽名称（如：我的目标）", "例如：我的目标");
  if (!attr) return;
  const val = await appPrompt("「" + attr + "」的内容", "例如：三个月内发布 Brickery v1.0");
  if (val == null) return;
  try { await ipc("core_set", { items: [{ attribute: attr, value: val }] }); loadCore(); }
  catch (e) { await appAlert("添加失败：" + e.message); }
}
async function delSmart(label) {
  if (!await appConfirm("删除智能槽", "删除「" + label + "」？删除后该归纳不再注入。", "删除", "取消")) return;
  try { await ipc("core_smart_delete", { label: label }); loadCore(); }
  catch (e) { await appAlert("删除失败：" + e.message); }
}
async function resolveCand(id) {
  try { await ipc("core_candidate_resolve", { id: id }); loadCore(); }
  catch (e) { await appAlert("写入失败：" + e.message); }
}
async function dismissCand(id) {
  try { await ipc("core_candidate_dismiss", { id: id }); loadCore(); }
  catch (e) { alert("操作失败：" + e.message); }
}

/* ================= 自进化 ================= */
const EVOLVE_STATUS_CLS = { active: "", degraded: "err-text", retired: "muted" };
async function loadEvolve() {
  const candEl = $("evolveCand"), stEl = $("evolveStats");
  if (!candEl) return;
  try {
    const [cand, stats] = await Promise.all([
      ipc("evolve_candidates", {}), ipc("evolve_refine_stats", {})
    ]);
    const pend = cand.items || [];
    candEl.innerHTML = pend.length ? pend.map(it => `
      <div class="list-item">
        <div><div class="title">${esc(it.name)}</div><div class="sub">${esc(it.summary)} · 触发 ${(it.trigger || []).join("、")} · 置信 ${(it.confidence || 0).toFixed(2)}</div></div>
        <div class="row" style="gap:6px;flex-wrap:nowrap">
          <button class="btn sm primary" onclick="evolveConfirm(${it.id})">确认激活</button>
          <button class="btn sm" onclick="evolveReject(${it.id})">拒绝</button>
        </div>
      </div>`).join("") : '<div class="empty">暂无待确认候选</div>';
    const rows = stats.items || [];
    stEl.innerHTML = rows.length ? rows.map(it => `
      <div class="list-item">
        <div><div class="title">${esc(it.brick_name)}</div><div class="sub">使用 ${it.usage_count} · 成功 ${it.success_count} · 连成 ${it.consecutive_success} · 连败 ${it.consecutive_fail} · 置信 ${it.confidence.toFixed(2)}</div></div>
        <div class="${EVOLVE_STATUS_CLS[it.status] || ""}" style="font-size:11px">${esc(it.status)}</div>
      </div>`).join("") : '<div class="empty">暂无已激活积木，确认候选后此处显示使用统计</div>';
  } catch (e) {
    candEl.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>';
  }
}
async function evolveConfirm(id) {
  try { await ipc("evolve_confirm", { id: id }); loadEvolve(); }
  catch (e) { alert("激活失败：" + e.message); }
}
async function evolveReject(id) {
  try { await ipc("evolve_reject", { id: id }); loadEvolve(); }
  catch (e) { alert("操作失败：" + e.message); }
}

/* ================= 设置 ================= */
let cfgData = null;   // config_get 缓存：tab 切换后未渲染字段仍可取旧值
let apiProfiles = [], apiActiveId = "";

async function renderSettings() {
  $("content").innerHTML = '<div class="empty">加载中...</div>';
  let cfg = null;
  try { cfg = await ipc("config_get", {}); } catch (e) { $("content").innerHTML = '<div class="err-text">加载配置失败：' + esc(e.message) + '</div>'; return; }
  cfgData = cfg;
  apiProfiles = (cfg.profiles || []).filter(p => p && p.id);
  apiActiveId = cfg.active_profile_id || "";
  $("content").innerHTML = `
    <div class="settings-wrap">
      <div class="settings-layout">
        <div class="settings-nav">
          <button data-tab="general" onclick="showSettingsTab('general')"><span class="nav-ico">&#9881;</span>通用</button>
          <button data-tab="model" onclick="showSettingsTab('model')"><span class="nav-ico">&#9633;</span>模型</button>
          <button data-tab="memory" onclick="showSettingsTab('memory')"><span class="nav-ico">&#9670;</span>记忆</button>
          <button data-tab="data" onclick="showSettingsTab('data')"><span class="nav-ico">&#9675;</span>数据与备份</button>
          <button data-tab="agent" onclick="showSettingsTab('agent')"><span class="nav-ico">&#9650;</span>Agent 配置</button>
          <button data-tab="about" onclick="showSettingsTab('about')"><span class="nav-ico">&#8505;</span>关于</button>
        </div>
        <div class="settings-body" id="settingsBody"><div class="empty">加载中...</div></div>
      </div>
    </div>
    <div id="apiModal" class="modal-mask" style="display:none" onclick="if(event.target===this)closeApiModal()">
      <div class="modal-box">
        <h3 id="apiModalTitle">新建 API 预设</h3>
        <div class="field"><label>厂商</label>
          <select id="amVendor" onchange="onVendorChange()">
            <option value="">自定义</option>
            <optgroup label="国内直连">
              <option value="volc">火山方舟</option>
              <option value="deepseek">DeepSeek</option>
              <option value="qwen">通义千问</option>
              <option value="zhipu">智谱</option>
            </optgroup>
            <optgroup label="国外需代理">
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="gemini">Gemini</option>
            </optgroup>
          </select>
        </div>
        <div class="field"><label>预设名称</label><input id="amName" placeholder="如 DeepSeek"></div>
        <div class="field"><label>API 端点（api_url）</label><input id="amUrl" placeholder="https://api.openai.com/v1"></div>
        <div class="field"><label>模型名（api_model）</label><input id="amModel" placeholder="gpt-4o"></div>
        <div class="field"><label>API Key</label><input id="amKey" type="password" placeholder="sk-..."></div>
        <div class="field"><label>请求超时（秒）</label><input id="amTimeout" type="number" min="5" value="60"></div>
        <div class="row" style="justify-content:flex-end">
          <button class="btn" onclick="closeApiModal()">取消</button>
          <button class="btn primary" onclick="saveApiModal()">保存</button>
        </div>
      </div>
    </div>`;
  showSettingsTab("general");
}

function showSettingsTab(k) {
  document.querySelectorAll(".settings-nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === k));
  const body = $("settingsBody"); if (!body) return;
  if (k === "general") renderTabGeneral();
  else if (k === "model") renderTabModel();
  else if (k === "memory") renderTabMemory();
  else if (k === "data") renderTabData();
  else if (k === "agent") renderTabAgent();
  else if (k === "about") renderTabAbout();
}

/* ---- 通用 ---- */
function renderTabGeneral() {
  const cfg = cfgData || {}; const eng = cfg.engine || {};
  $("settingsBody").innerHTML = `
    <div class="section-card">
      <h3>引擎状态</h3>
      <div class="engine-status">
        <div class="status-row"><span class="dot" id="stLocalDot"></span><span id="stLocalText">本地模型：检测中...</span></div>
        <div class="status-row"><span class="dot" id="stApiDot"></span><span id="stApiText">网络 API：检测中...</span></div>
      </div>
      <div class="row"><button class="btn sm" onclick="renderEngineStatus()">刷新状态</button><span class="muted">只做本地探测，不发任何试探请求</span></div>
    </div>
    <div class="section-card">
      <h3>执行模式</h3>
      <div class="field"><label>模式</label>
        <select id="cfgMode">
          <option value="normal" ${cfg.mode === "normal" ? "selected" : ""}>工作模式（写操作逐次确认）</option>
          <option value="plan" ${cfg.mode === "plan" ? "selected" : ""}>计划模式（只读思考，不碰文件/命令）</option>
          <option value="accept_edits" ${cfg.mode === "accept_edits" ? "selected" : ""}>可信会话（中风险写操作自动批准）</option>
        </select>
      </div>
      <div class="row"><button class="btn sm primary" onclick="setExecMode()">切换模式</button><span class="muted">切换即生效并持久化</span></div>
    </div>
    <div class="section-card">
      <h3>通用</h3>
      <div class="field"><label>请求超时（秒）</label><input id="cfgTimeout" type="number" min="5" value="${esc(cfg.timeout || 60)}" style="max-width:120px"></div>
      <div class="row">
        <label class="switch"><input type="checkbox" id="cfgTools" ${cfg.tools_enabled ? "checked" : ""}><span class="slider"></span></label><span class="muted">启用工具</span>
        <label class="switch" style="margin-left:12px"><input type="checkbox" id="cfgSkills" ${cfg.skills_enabled ? "checked" : ""}><span class="slider"></span></label><span class="muted">启用技能</span>
        <button class="btn primary" style="margin-left:auto" onclick="saveConfig()">保存配置</button>
      </div>
    </div>
    <div class="section-card">
      <h3>红线说明</h3>
      <div class="hint">删除 / 覆盖 / 格式化等破坏性操作前，Agent 必须逐次确认；计划模式（PLAN）下绝不执行任何写操作。所有高风险命令在执行前都会完整展示并等待你的批准。</div>
    </div>`;
  renderEngineStatus();
}

/* ---- 模型 ---- */
const VENDOR_TEMPLATES = {
  volc:      { name: "火山方舟", url: "https://ark.cn-beijing.volces.com/api/v3", model: "deepseek-v3" },
  deepseek:  { name: "DeepSeek", url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  qwen:      { name: "通义千问", url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  zhipu:     { name: "智谱", url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash" },
  openai:    { name: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4o" },
  anthropic: { name: "Anthropic", url: "https://api.anthropic.com/v1", model: "claude-sonnet-4" },
  gemini:    { name: "Gemini", url: "https://generativelanguage.googleapis.com/v1beta", model: "gemini-2.0-flash" },
};
function vendorLabel(v) {
  return ({ "": "全部", volc: "火山方舟", deepseek: "DeepSeek", qwen: "通义千问", zhipu: "智谱", openai: "OpenAI", anthropic: "Anthropic", gemini: "Gemini" })[v] || "自定义";
}
function vendorOf(p) {
  const u = (p.api_url || "").toLowerCase();
  if (u.includes("ark.cn-beijing")) return "volc";
  if (u.includes("deepseek")) return "deepseek";
  if (u.includes("dashscope")) return "qwen";
  if (u.includes("bigmodel")) return "zhipu";
  if (u.includes("openai")) return "openai";
  if (u.includes("anthropic")) return "anthropic";
  if (u.includes("generativelanguage")) return "gemini";
  return "";
}
let apiVendorFilter = "";
function filterVendor(el, v) {
  apiVendorFilter = v;
  document.querySelectorAll(".vendor-tags .vt").forEach(t => t.classList.toggle("active", t.dataset.v === v));
  renderApiCards();
}
function renderTabModel() {
  const cfg = cfgData || {}; const eng = cfg.engine || {};
  const backend = eng.backend === "local" ? "local" : "api";
  $("settingsBody").innerHTML = `
    <div class="section-card">
      <h3>后端选择</h3>
      <div class="row" style="gap:10px">
        <button class="btn backend-btn ${backend === "local" ? "primary" : ""}" id="btnBackendLocal" onclick="setBackend('local')">本地 GGUF</button>
        <button class="btn backend-btn ${backend === "api" ? "primary" : ""}" id="btnBackendApi" onclick="setBackend('api')">网络 API</button>
      </div>
      <select id="cfgBackend" style="display:none">
        <option value="api" ${eng.backend === "api" ? "selected" : ""}>网络 API</option>
        <option value="local" ${eng.backend === "local" ? "selected" : ""}>本地 GGUF</option>
      </select>
      <div class="hint" style="margin-top:8px">本地 GGUF 为默认内嵌引擎；网络 API 仅在你主动填写端点时连接，且只连你指定的地址。</div>
    </div>
    <div class="section-card" id="secLocal">
      <h3>本地模型</h3>
      <div class="field"><label>本地模型路径</label>
        <div class="row"><input id="cfgLocal" value="${esc(eng.local_model || "")}" placeholder="模型路径"><button class="btn sm" onclick="pickLocalModel()">选择</button></div>
      </div>
      <div class="field"><label>模型目录</label>
        <div class="hint">${esc(cfg.models_root || "")}</div>
        <div id="modelList"><div class="muted">加载中...</div></div>
      </div>
    </div>
    <div class="section-card" id="secApi">
      <h3>网络 API 预设</h3>
      <div class="sec-sub">每个 API 一张卡片，可编辑/删除；「默认」为当前生效预设。保存即生效，无需再点底部按钮。</div>
      <div class="vendor-tags">
        ${["", "volc", "deepseek", "qwen", "zhipu", "openai", "anthropic", "gemini"].map(v => `<span class="vt ${v === apiVendorFilter ? "active" : ""}" data-v="${v}" onclick="filterVendor(this, '${v}')">${vendorLabel(v)}</span>`).join("")}
      </div>
      <div id="apiCards"></div>
      <div class="row" style="margin-top:10px">
        <button class="btn sm primary" onclick="openApiModal()">＋ 新建 API</button>
        <button class="btn sm" onclick="openApiModal('coding')">＋ 新建 Coding Plan</button>
      </div>
    </div>`;
  renderApiCards();
  loadModels();
  syncBackendSections();
}
function setBackend(v) {
  const sel = $("cfgBackend");
  if (sel) sel.value = v;
  const lb = $("btnBackendLocal"), ab = $("btnBackendApi");
  if (lb) lb.classList.toggle("primary", v === "local");
  if (ab) ab.classList.toggle("primary", v === "api");
  syncBackendSections();
}
function syncBackendSections() {
  const sel = $("cfgBackend");
  if (!sel) return;
  const v = sel.value;
  const sl = $("secLocal"), sa = $("secApi");
  if (sl) sl.style.display = v === "local" ? "" : "none";
  if (sa) sa.style.display = v === "api" ? "" : "none";
}
function pickLocalModel() {
  const v = prompt("输入本地 GGUF 模型路径：", $("cfgLocal").value || "");
  if (v != null) $("cfgLocal").value = v.trim();
}
async function loadModels() {
  const box = $("modelList"); if (!box) return;
  try {
    const d = await ipc("models_list", {});
    const installed = d.installed || [];
    box.innerHTML = installed.length ? installed.map(m => `
      <div class="list-item"><div><div class="title">${esc(m.name || m.id || "")}</div><div class="sub">${esc(m.path || "")}</div></div></div>`).join("") : '<div class="empty">未安装本地模型</div>';
  } catch (e) { box.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}

/* ---- 记忆 ---- */
function renderTabMemory() {
  const cfg = cfgData || {}; const ny = cfg.nightly || {};
  $("settingsBody").innerHTML = `
    <div class="section-card">
      <h3>夜间空闲整理</h3>
      <div class="hint">空闲时自动整理记忆柜、生成画像与索引；随配置一起保存。</div>
      <div class="row" style="margin-top:6px">
        <label class="switch"><input type="checkbox" id="cfgNightly" ${ny.enabled ? "checked" : ""}><span class="slider"></span></label><span class="muted">启用夜间整理</span>
      </div>
      <div class="row" style="margin-top:8px">
        <label class="switch"><input type="checkbox" id="cfgNightlyLocal" ${ny.use_local_model ? "checked" : ""}><span class="slider"></span></label><span class="muted">整理使用本地模型</span>
      </div>
    </div>
    <div class="section-card">
      <h3>开场回顾</h3>
      <div class="hint">新会话开始时自动带入上次记忆摘要（open_session_context）。</div>
      <div class="row" style="margin-top:6px">
        <label class="switch"><input type="checkbox" id="cfgOpenCtx" ${cfg.open_session_context ? "checked" : ""}><span class="slider"></span></label><span class="muted">启用开场回顾</span>
      </div>
    </div>
    <div class="row"><button class="btn primary" onclick="saveConfig()">保存配置</button></div>`;
}

/* ---- 数据与备份 ---- */
function renderTabData() {
  const cfg = cfgData || {};
  $("settingsBody").innerHTML = `
    <div class="section-card">
      <h3>数据目录</h3>
      <div class="field"><label>备份目录（backup_dir）</label>
        <div class="row">
          <input id="cfgBackup" value="${esc(cfg.backup_dir || "")}" style="flex:1">
          <button class="btn sm" onclick="openFolder('backup')">打开</button>
          <button class="btn sm" onclick="pickFolder('cfgBackup')">更改</button>
        </div>
      </div>
      <div class="field"><label>产出目录（output_dir）</label>
        <div class="row">
          <input id="cfgOutput" value="${esc(cfg.output_dir || "")}" style="flex:1">
          <button class="btn sm" onclick="openFolder('output')">打开</button>
          <button class="btn sm" onclick="pickFolder('cfgOutput')">更改</button>
        </div>
      </div>
      <div class="row" style="margin-top:6px">
        <button class="btn sm primary" onclick="saveConfig()">保存目录</button>
        <button class="btn sm" id="btnBackupDefault" onclick="runBackupDefault()">一键备份</button>
        <button class="btn sm" onclick="renderBackupList()">查看备份列表</button>
        <span class="muted">数据目录：${esc(cfg.home || "")}</span>
      </div>
      <div id="backupList" style="margin-top:10px"></div>
    </div>`;
}

/* ---- Agent 配置（只读） ---- */
async function renderTabAgent() {
  $("settingsBody").innerHTML = '<div class="section-card"><h3>Agent 配置</h3><div id="agentCfgOut" class="muted">加载中...</div></div>';
  try {
    const d = await ipc("agent_get", {});
    const out = $("agentCfgOut");
    if (!d || d.error) { out.innerHTML = '<div class="err-text">' + esc((d && d.error) || "读取失败") + '</div>'; return; }
    out.innerHTML = '<pre style="background:#0d1117;border:1px solid var(--line);color:var(--ink);padding:12px;border-radius:6px;font-size:11px;line-height:1.6;overflow:auto;max-height:60vh;white-space:pre-wrap">' + esc(JSON.stringify(d.agent, null, 2)) + '</pre>';
  } catch (e) {
    const out = $("agentCfgOut"); if (out) out.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>';
  }
}

/* ---- 关于 ---- */
function renderTabAbout() {
  const cfg = cfgData || {};
  $("settingsBody").innerHTML = `
    <div class="section-card">
      <h3>关于</h3>
      <div class="row"><span class="muted">Shadeling Agent · 由 Brickery 积木平台产出</span></div>
      <div class="row" style="margin-top:8px">
        <button class="btn sm" onclick="reRunSetup()">重新运行首次引导</button>
        <span class="muted" id="aboutHome">${esc(cfg.home || "")}</span>
      </div>
    </div>`;
}
function pickFolder(inputId) {
  const v = prompt("输入目录路径：", $(inputId).value || "");
  if (v != null) $(inputId).value = v.trim();
}
async function reRunSetup() {
  if (!confirm("重新运行首次引导？当前配置不会丢失，引导页会重新打开。")) return;
  location.href = "http://127.0.0.1:18766/";
}
function setBackend(v) {
  const sel = $("cfgBackend");
  if (sel) sel.value = v;
  const lb = $("btnBackendLocal"), ab = $("btnBackendApi");
  if (lb) lb.classList.toggle("primary", v === "local");
  if (ab) ab.classList.toggle("primary", v === "api");
  syncBackendSections();
}
function syncBackendSections() {
  const sel = $("cfgBackend");
  if (!sel) return;
  const v = sel.value;
  const sl = $("secLocal"), sa = $("secApi");
  if (sl) sl.style.display = v === "local" ? "" : "none";
  if (sa) sa.style.display = v === "api" ? "" : "none";
}
function pickFolder(inputId) {
  const v = prompt("输入目录路径：", $(inputId).value || "");
  if (v != null) $(inputId).value = v.trim();
}
async function reRunSetup() {
  if (!confirm("重新运行首次引导？当前配置不会丢失，引导页会重新打开。")) return;
  location.href = "http://127.0.0.1:18766/";
}
function renderApiCards() {
  const box = $("apiCards"); if (!box) return;
  const list = apiVendorFilter ? apiProfiles.filter(p => vendorOf(p) === apiVendorFilter) : apiProfiles;
  box.innerHTML = list.map(p => `
    <div class="list-item" style="border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin-bottom:8px">
      <div>
        <div class="title">${esc(p.name || p.id)} ${p.id === apiActiveId ? '<span class="tag installed">默认</span>' : ""}</div>
        <div class="sub">${esc(p.api_url || "")} · ${esc(p.api_model || "")}${p.backend === "local" ? " · 本地" : ""}</div>
      </div>
      <div class="row">
        <button class="btn sm" onclick="openApiModal('${esc(p.id)}')">编辑</button>
        <button class="btn sm" onclick="delApiCard('${esc(p.id)}')">删</button>
        ${p.id !== apiActiveId ? `<button class="btn sm" onclick="setActiveApi('${esc(p.id)}')">设为默认</button>` : ""}
      </div>
    </div>`).join("") || (apiVendorFilter ? '<div class="empty">该厂商下暂无预设</div>' : '<div class="empty">暂无 API 预设，点「＋ 新建 API」添加</div>');
}
let apiModalEditId = null;
function openApiModal(idOrKind) {
  apiModalEditId = null;
  const m = $("apiModal"); if (!m) return;
  $("apiModalTitle").textContent = "新建 API 预设";
  $("amVendor").value = ""; $("amName").value = ""; $("amUrl").value = ""; $("amModel").value = ""; $("amKey").value = ""; $("amTimeout").value = "60";
  if (idOrKind === "coding") {
    $("apiModalTitle").textContent = "新建 Coding Plan";
    $("amName").value = "我的 Coding Plan";
  } else if (typeof idOrKind === "string" && idOrKind) {
    const p = apiProfiles.find(x => x.id === idOrKind); if (!p) return;
    apiModalEditId = idOrKind;
    $("apiModalTitle").textContent = "编辑 API 预设";
    $("amVendor").value = vendorOf(p);
    $("amName").value = p.name || ""; $("amUrl").value = p.api_url || ""; $("amModel").value = p.api_model || "";
    $("amKey").value = p.api_key || ""; $("amTimeout").value = p.timeout || 60;
  }
  m.style.display = "flex";
}
function onVendorChange() {
  const v = $("amVendor").value;
  const t = VENDOR_TEMPLATES[v];
  if (!t) return;
  if (!apiModalEditId) { $("amName").value = t.name; $("amUrl").value = t.url; $("amModel").value = t.model; }
  else { $("amUrl").value = t.url; $("amModel").value = t.model; }
}
function closeApiModal() { const m = $("apiModal"); if (m) m.style.display = "none"; }
function saveApiModal() {
  const name = $("amName").value.trim();
  if (!name) { alert("请填写预设名称"); return; }
  const url = $("amUrl").value.trim();
  const model = $("amModel").value.trim();
  const key = $("amKey").value.trim();
  const timeout = parseInt($("amTimeout").value, 10) || 60;
  if (apiModalEditId) {
    const p = apiProfiles.find(x => x.id === apiModalEditId); if (!p) return;
    p.name = name; p.api_url = url; p.api_model = model; p.api_name = name;
    if (key) p.api_key = key;
    p.timeout = timeout;
  } else {
    const id = "p" + Date.now().toString(36);
    apiProfiles.push({ id, name, backend: "api", api_url: url, api_key: key, api_model: model, api_name: name, timeout });
    if (!apiActiveId) apiActiveId = id;
  }
  closeApiModal();
  renderApiCards();
  saveApiProfiles();
}
function delApiCard(id) {
  if (!confirm("删除 API 预设「" + (apiProfiles.find(x => x.id === id) || {}).name + "」？")) return;
  apiProfiles = apiProfiles.filter(x => x.id !== id);
  if (apiActiveId === id) apiActiveId = apiProfiles.length ? apiProfiles[0].id : "";
  renderApiCards();
  saveApiProfiles();
}
function setActiveApi(id) { apiActiveId = id; renderApiCards(); saveApiProfiles(); }
async function saveApiProfiles() {
  try {
    await ipc("config_set", { profiles: apiProfiles, active_profile_id: apiActiveId });
  } catch (e) { alert("预设保存失败：" + e.message); }
}
async function renderEngineStatus() {
  const lDot = $("stLocalDot"), lTxt = $("stLocalText"), aDot = $("stApiDot"), aTxt = $("stApiText");
  if (!lDot) return;
  try {
    const d = await ipc("status", {});
    const eng = d.engine || {};
    const ok = (el, txt, good) => { el.className = "dot " + (good ? "ok" : "bad"); txt.textContent = txt.textContent.split("：")[0] + "：" + (good ? "可用" : "不可用"); };
    if (eng.backend === "local") {
      ok(lDot, lTxt, !!eng.local_available);
      lTxt.textContent = "本地模型：" + (eng.local_available ? "可用" : "不可用") + (eng.local_model ? "（" + eng.local_model + "）" : "");
      aTxt.textContent = "网络 API：未绑定";
      aDot.className = "dot";
    } else {
      ok(aDot, aTxt, !!eng.network_configured);
      aTxt.textContent = "网络 API：" + (eng.network_configured ? "已绑定" : "未绑定") + (eng.api_name ? "（" + eng.api_name + "）" : "");
      lTxt.textContent = "本地模型：未启用";
      lDot.className = "dot";
    }
  } catch (e) { lTxt.textContent = "引擎状态获取失败"; alert("引擎状态获取失败：" + e.message); }
}
async function setExecMode() {
  const mode = $("cfgMode").value;
  const label = { normal: "工作模式", plan: "计划模式", accept_edits: "可信会话" }[mode] || mode;
  try { await ipc("set_mode", { mode }); alert("执行模式已切换为：" + label); }
  catch (e) { alert("切换失败：" + e.message); }
}
async function openFolder(kind) {
  try { await ipc("open_folder", { kind }); } catch (e) { alert("打开失败：" + e.message); }
}
async function openFolderByPath(path) {
  try { await ipc("open_folder", { path }); } catch (e) { alert("打开失败：" + e.message); }
}
async function runBackupDefault() {
  const btn = $("btnBackupDefault");
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "备份中..."; }
  try {
    const r = await ipc("backup_default", {});
    alert("一键备份完成：\\n" + (r.dest || r.detail || "已保存到默认备份目录"));
  } catch (e) { alert("备份失败：" + e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = old; } }
}
async function renderBackupList() {
  const box = $("backupList"); if (!box) return;
  box.innerHTML = '<div class="muted">加载中...</div>';
  try {
    const r = await ipc("backup_list", {});
    const items = r.items || [];
    box.innerHTML = items.length
      ? items.map(b => `
        <div class="list-item" style="border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin-bottom:8px">
          <div><div class="title">${esc(b.name)}</div><div class="sub">${esc(b.path)} · ${b.items} 项</div></div>
          <div class="row">
            <button class="btn sm" onclick="openFolderByPath('${esc(b.path)}')">打开</button>
            <button class="btn sm" onclick="restoreBackup('${esc(b.path)}')">恢复</button>
          </div>
        </div>`).join("")
      : '<div class="empty">暂无备份，点「一键备份」创建</div>';
  } catch (e) { box.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function restoreBackup(path) {
  if (!confirm("从备份恢复将覆盖当前数据（会话/记忆/配置），确定继续？")) return;
  try { await ipc("backup_restore", { src_dir: path }); alert("恢复完成"); }
  catch (e) { alert("恢复失败：" + e.message); }
}
async function saveConfig() {
  const has = (id) => !!$(id);
  const engine = cfgData.engine || {};
  const ny = cfgData.nightly || {};
  const params = {
    backend: has("cfgBackend") ? $("cfgBackend").value : (engine.backend || "local"),
    local_model: has("cfgLocal") ? $("cfgLocal").value.trim() : (engine.local_model || ""),
    backup_dir: has("cfgBackup") ? $("cfgBackup").value.trim() : (cfgData.backup_dir || ""),
    output_dir: has("cfgOutput") ? $("cfgOutput").value.trim() : (cfgData.output_dir || ""),
    tools_enabled: has("cfgTools") ? $("cfgTools").checked : !!cfgData.tools_enabled,
    skills_enabled: has("cfgSkills") ? $("cfgSkills").checked : !!cfgData.skills_enabled,
    request_timeout: has("cfgTimeout") ? (parseInt($("cfgTimeout").value, 10) || 60) : (cfgData.timeout || 60),
    profiles: apiProfiles,
    active_profile_id: apiActiveId,
    open_session_context: has("cfgOpenCtx") ? $("cfgOpenCtx").checked : !!cfgData.open_session_context,
    nightly: {
      enabled: has("cfgNightly") ? $("cfgNightly").checked : !!ny.enabled,
      use_local_model: has("cfgNightlyLocal") ? $("cfgNightlyLocal").checked : !!ny.use_local_model,
    },
  };
  try {
    await ipc("config_set", params);
    cfgData = Object.assign({}, cfgData, params);
    alert("配置已保存");
    loadStatus();
  } catch (e) { alert("保存失败：" + e.message); }
}

/* ================= 医生 ================= */
async function renderDoctor() {
  $("content").innerHTML = `
    <div class="card">
      <h3>系统自检</h3>
      <div class="hint">运行环境 / 依赖 / 模型 / 连通性 / 磁盘 全量检查</div>
      <div class="row">
        <button class="btn primary" onclick="runDoctor()">开始自检</button>
        <button class="btn" onclick="runHealth()">健康状态</button>
      </div>
      <div id="doctorOut" style="margin-top:12px"></div>
    </div>`;
}
async function runDoctor() {
  const out = $("doctorOut"); out.innerHTML = '<div class="muted">自检中（网络探测最长约 15s）...</div>';
  try {
    const d = await ipc("doctor", {});
    const checks = d.checks || [];
    out.innerHTML = '<div class="row" style="margin-bottom:10px"><span class="' + (d.all_ok ? "ok-text" : "err-text") + '" style="font-weight:700">' + (d.all_ok ? "全部通过" : "存在异常") + '</span></div>' +
      checks.map(c => `
        <div class="check-item">
          <span class="st ${c.ok ? "ok-text" : "err-text"}">${c.ok ? "通过" : "异常"}</span>
          <div><div class="nm">${esc(c.name)}</div><div class="dt">${esc(c.detail || "")}</div></div>
        </div>`).join("");
  } catch (e) { out.innerHTML = '<div class="err-text">自检失败：' + esc(e.message) + '</div>'; }
}
async function runHealth() {
  const out = $("doctorOut");
  try {
    const d = await ipc("health", {});
    out.innerHTML = '<div class="pre">' + esc(JSON.stringify(d, null, 2)) + '</div>';
  } catch (e) { out.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}

/* ================= 定时任务 ================= */
async function renderTasks() {
  $("content").innerHTML = `
    <div class="grid2">
      <div class="card">
        <h3>提交任务</h3>
        <div class="field"><label>任务指令</label><textarea id="taskPrompt" placeholder="输入后台任务指令"></textarea></div>
        <div class="field"><label>项目命名空间（可选）</label><input id="taskProject" placeholder="project"></div>
        <button class="btn primary" onclick="submitTask()">提交</button>
      </div>
      <div class="card">
        <h3>任务列表</h3>
        <div class="hint"><button class="btn sm" onclick="renderTasks()">刷新</button></div>
        <div id="taskList"><div class="muted">加载中...</div></div>
      </div>
    </div>`;
  loadTasks();
}
async function submitTask() {
  const prompt = $("taskPrompt").value.trim();
  if (!prompt) { alert("请输入任务指令"); return; }
  try {
    const d = await ipc("task_submit", { prompt, project: $("taskProject").value.trim() });
    alert("任务已提交：task_id=" + d.task_id);
    $("taskPrompt").value = ""; loadTasks();
  } catch (e) { alert("提交失败：" + e.message); }
}
async function loadTasks() {
  const box = $("taskList"); if (!box) return;
  try {
    const d = await ipc("task_list", {});
    const items = d.items || [];
    box.innerHTML = items.length ? items.map(t => `
      <div class="list-item">
        <div>
          <div class="title">${esc(t.prompt || "")}</div>
          <div class="sub">${esc(t.id || "")} · <span class="${t.status === "done" ? "ok-text" : t.status === "failed" ? "err-text" : ""}">${esc(t.status || "")}</span> · ${fmtTime(t.created_at)}</div>
          ${t.result ? `<div class="sub">${esc(t.result.slice(0, 120))}</div>` : ""}
        </div>
        ${t.status === "running" || t.status === "pending" ? `<button class="btn sm danger" onclick="cancelTask('${esc(t.id)}')">取消</button>` : ""}
      </div>`).join("") : '<div class="empty">暂无任务</div>';
  } catch (e) { box.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function cancelTask(id) {
  try { await ipc("task_cancel", { task_id: id }); loadTasks(); }
  catch (e) { alert("取消失败：" + e.message); }
}

/* ================= 保险库 ================= */
let vaultType = "";
async function renderVault() {
  $("content").innerHTML = `
    <div class="card">
      <div class="row between">
        <h3 style="margin:0">保险库</h3>
        <div class="row">
          <select id="vaultType" style="background:#0d1117;border:1px solid var(--line);color:var(--ink);padding:5px 8px;border-radius:4px;font-size:12px" onchange="vaultType=this.value;loadVault()">
            <option value="">全部类型</option>
            <option value="note">笔记</option>
            <option value="image">图片</option>
            <option value="webpage">网页</option>
            <option value="file">文件</option>
          </select>
          <button class="btn sm" onclick="vaultSnapshot()">网页快照</button>
          <button class="btn sm" onclick="vaultOcr()">OCR 入库</button>
          <button class="btn sm primary" onclick="renderVault()">刷新</button>
        </div>
      </div>
      <div id="vaultList"><div class="muted">加载中...</div></div>
    </div>`;
  loadVault();
}
async function loadVault() {
  const box = $("vaultList"); if (!box) return;
  try {
    const d = await ipc("vault_list", { type: vaultType, top_k: 100 });
    const items = d.items || [];
    box.innerHTML = items.length ? items.map(it => `
      <div class="list-item">
        <div>
          <div class="title">${esc(it.title || it.id || "")}</div>
          <div class="sub">${esc(it.type || "")} · ${esc(it.id || "")} · ${fmtTime(it.created_at || it.ts)}</div>
          ${it.fields && it.fields.excerpt ? `<div class="sub">${esc(String(it.fields.excerpt).slice(0, 100))}</div>` : ""}
        </div>
        <div class="row">
          <button class="btn sm" onclick="vaultDetail('${esc(it.id)}')">详情</button>
          <button class="btn sm danger" onclick="vaultDelete('${esc(it.id)}')">删除</button>
        </div>
      </div>`).join("") : '<div class="empty">保险库为空</div>';
  } catch (e) { box.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function vaultDetail(id) {
  try {
    const d = await ipc("vault_detail", { id });
    const it = d.item;
    alert("标题：" + (it.title || "") + "\\n类型：" + (it.type || "") + "\\n路径：" + (it.file_path || "") + "\\n\\n内容：\\n" + (it.content || it.fields && it.fields.excerpt || "").slice(0, 800));
  } catch (e) { alert("读取失败：" + e.message); }
}
async function vaultDelete(id) {
  if (!confirm("删除资产 " + id + "？")) return;
  try { await ipc("vault_delete", { id }); loadVault(); }
  catch (e) { alert("删除失败：" + e.message); }
}
async function vaultSnapshot() {
  const url = prompt("输入网页 URL："); if (!url) return;
  try { const d = await ipc("vault_snapshot", { url }); alert("已快照入库：" + (d.item && d.item.id || "")); loadVault(); }
  catch (e) { alert("快照失败：" + e.message); }
}
async function vaultOcr() {
  const fp = prompt("输入图片文件路径："); if (!fp) return;
  const text = prompt("输入 OCR 识别文本："); if (text == null) return;
  try { await ipc("vault_ocr", { file_path: fp, text }); alert("OCR 文本已入库"); loadVault(); }
  catch (e) { alert("入库失败：" + e.message); }
}

/* ================= 工作台图谱（文件柜内） ================= */
const NODE_COLORS = {
  goal: "#3fb950", decision: "#d29922", risk: "#f85149", task: "#58a6ff",
  resource: "#bc8cff", rule: "#3fb950", status: "#39c5cf", progress: "#ff7a18",
  anchor: "#8b949e",
};
function drawGraph(nodes, edges) {
  const canvas = $("graphCanvas"); if (!canvas) return;
  const W = canvas.clientWidth, H = 360;
  const pos = {};
  nodes.forEach((n, i) => {
    const angle = (i / Math.max(nodes.length, 1)) * Math.PI * 2 - Math.PI / 2;
    const r = Math.min(W, H) * 0.32;
    pos[n.node_id || n.id] = { x: W / 2 + r * Math.cos(angle), y: H / 2 + r * Math.sin(angle) };
  });
  let html = "";
  for (const e of edges) {
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const ang = Math.atan2(dy, dx) * 180 / Math.PI;
    html += '<div class="g-edge" style="left:' + a.x + 'px;top:' + a.y + 'px;width:' + len + 'px;transform:rotate(' + ang + 'deg)"><span class="rel">' + esc(e.relation || "") + '</span></div>';
  }
  for (const n of nodes) {
    const nid2 = n.node_id || n.id;
    const p = pos[nid2] || { x: W / 2, y: H / 2 };
    const color = NODE_COLORS[n.type] || "#8b949e";
    html += '<div class="g-node" style="left:' + (p.x - 60) + 'px;top:' + (p.y - 14) + 'px;border-color:' + color + '" title="' + esc(nid2) + '" onclick="editNode(\\'' + esc(nid2) + '\\')">' + esc(n.label || nid2) + '</div>';
  }
  canvas.innerHTML = html;
}

/* ================= 备份恢复 ================= */
async function renderBackup() {
  $("content").innerHTML = `
    <div class="grid2">
      <div class="card">
        <h3>备份</h3>
        <div class="hint">仅含用户数据（会话/记忆/配置/文件柜），不含模型权重</div>
        <div class="row wrap">
          <button class="btn primary" onclick="backupDefault()">一键备份到默认目录</button>
          <button class="btn" onclick="backupExport()">导出到指定目录</button>
        </div>
        <div id="backupMsg" style="margin-top:10px"></div>
      </div>
      <div class="card">
        <h3>恢复</h3>
        <div class="hint">从备份目录恢复数据（按顶层条目覆盖）</div>
        <div class="row">
          <input id="restoreDir" style="flex:1;background:#0d1117;border:1px solid var(--line);color:var(--ink);padding:8px 10px;border-radius:4px;font-family:inherit;font-size:12px" placeholder="备份目录路径">
          <button class="btn danger" onclick="backupRestore()">恢复</button>
        </div>
      </div>
    </div>
    <div class="card">
      <h3>备份列表</h3>
      <div id="backupList"><div class="muted">加载中...</div></div>
    </div>`;
  loadBackupList();
}
async function backupDefault() {
  const msg = $("backupMsg"); msg.innerHTML = '<div class="muted">备份中...</div>';
  try { const d = await ipc("backup_default", {}); msg.innerHTML = '<div class="ok-text">' + esc(d.detail || "备份完成") + '</div>'; loadBackupList(); }
  catch (e) { msg.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function backupExport() {
  const dest = prompt("导出目标目录："); if (!dest) return;
  const msg = $("backupMsg"); msg.innerHTML = '<div class="muted">备份中...</div>';
  try { const d = await ipc("backup_export", { dest_dir: dest }); msg.innerHTML = '<div class="ok-text">' + esc(d.detail || "备份完成") + '</div>'; loadBackupList(); }
  catch (e) { msg.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}
async function backupRestore() {
  const src = $("restoreDir").value.trim();
  if (!src) { alert("请输入备份目录路径"); return; }
  if (!confirm("从 " + src + " 恢复数据？将覆盖当前数据目录。")) return;
  try { const d = await ipc("backup_restore", { src_dir: src }); alert(d.message || "恢复完成"); }
  catch (e) { alert("恢复失败：" + e.message); }
}
async function loadBackupList() {
  const box = $("backupList"); if (!box) return;
  try {
    const d = await ipc("backup_list", {});
    const items = d.items || [];
    box.innerHTML = '<div class="hint">默认备份目录：' + esc(d.backup_dir || "") + '</div>' +
      (items.length ? items.map(b => `
        <div class="list-item"><div><div class="title">${esc(b.name)}</div><div class="sub">${b.items} 项 · ${fmtTime(b.created)}</div></div><div class="muted">${esc(b.path)}</div></div>`).join("") : '<div class="empty">暂无备份</div>');
  } catch (e) { box.innerHTML = '<div class="err-text">' + esc(e.message) + '</div>'; }
}

/* ================= 规则 ================= */
async function renderRules() {
  $("content").innerHTML = '<div class="empty">加载中...</div>';
  let rules = [];
  try { const d = await ipc("rules_list", {}); rules = d.rules || []; } catch (e) {}
  $("content").innerHTML = `
    <div class="grid2">
      <div class="card">
        <h3>添加规则</h3>
        <div class="field"><label>规则内容</label><textarea id="ruleText" placeholder="输入持久规则"></textarea></div>
        <div class="row">
          <button class="btn primary" onclick="addRule()">添加</button>
          <button class="btn" onclick="reloadRules()">重载规则</button>
        </div>
      </div>
      <div class="card">
        <h3>规则列表</h3>
        <div class="hint">共 ${rules.length} 条</div>
        <div id="ruleList">${rules.map((r, i) => `
          <div class="list-item">
            <div class="title" style="flex:1">${esc(r)}</div>
            <button class="btn sm danger" onclick="removeRule(${i})">删除</button>
          </div>`).join("") || '<div class="empty">暂无规则</div>'}
        </div>
      </div>
    </div>`;
}
async function addRule() {
  const rule = $("ruleText").value.trim();
  if (!rule) { alert("请输入规则内容"); return; }
  try { await ipc("rules_add", { rule }); $("ruleText").value = ""; renderRules(); }
  catch (e) { alert("添加失败：" + e.message); }
}
async function removeRule(idx) {
  try { await ipc("rules_remove", { index: idx }); renderRules(); }
  catch (e) { alert("删除失败：" + e.message); }
}
async function reloadRules() {
  try { await ipc("rules_reload", {}); alert("规则已重载"); renderRules(); }
  catch (e) { alert("重载失败：" + e.message); }
}

/* ================= 连接器 ================= */
async function renderConnectors() {
  $("content").innerHTML = `
    <div class="grid2">
      <div class="card">
        <h3>飞书连接器</h3>
        <div class="hint">填写 app_id / app_secret，保存后重启生效；首个对 bot 说话的账号自动绑定</div>
        <div class="field"><label>App ID</label><input id="fsAppId" placeholder="cli_xxx"></div>
        <div class="field"><label>App Secret</label><input id="fsSecret" type="password" placeholder="app_secret"></div>
        <button class="btn primary" onclick="setupFeishu()">保存飞书配置</button>
      </div>
      <div class="card">
        <h3>Telegram 连接器</h3>
        <div class="hint">填写 bot_token（由 @BotFather 创建 bot 获得），保存后重启生效</div>
        <div class="field"><label>Bot Token</label><input id="tgToken" type="password" placeholder="123456:ABC-DEF..."></div>
        <button class="btn primary" onclick="setupTelegram()">保存 Telegram 配置</button>
      </div>
    </div>`;
}
async function setupFeishu() {
  const app_id = $("fsAppId").value.trim(), app_secret = $("fsSecret").value.trim();
  if (!app_id || !app_secret) { alert("请填写 app_id 与 app_secret"); return; }
  try { const d = await ipc("feishu_setup", { app_id, app_secret }); alert(d.message || "飞书已配置"); }
  catch (e) { alert("配置失败：" + e.message); }
}
async function setupTelegram() {
  const bot_token = $("tgToken").value.trim();
  if (!bot_token) { alert("请填写 bot_token"); return; }
  try { const d = await ipc("telegram_setup", { bot_token }); alert(d.message || "Telegram 已配置"); }
  catch (e) { alert("配置失败：" + e.message); }
}

/* ================= 渲染器注册 ================= */
const renderers = {
  chat: renderChat,
  skills: renderSkills,
  market: renderMarket,
  cabinet: renderCabinet,
  memory: renderMemory,
  settings: renderSettings,
  doctor: renderDoctor,
  tasks: renderTasks,
  vault: renderVault,
  backup: renderBackup,
  rules: renderRules,
  connectors: renderConnectors,
};

/* ================= 初始化 ================= */
buildNav();
switchSection("chat");
loadStatus();
setInterval(loadStatus, 15000);
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
        if ctype == "application/json":
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        else:
            body = obj if isinstance(obj, bytes) else str(obj).encode("utf-8")
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
        elif self.path == "/api/status":
            self._status()
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        data = self._read_json()
        if self.path == "/api/ipc":
            self._ipc(data)
        elif self.path == "/api/chat":
            self._chat(data)
        elif self.path == "/api/messages/delete":
            self._delete_messages(data)
        else:
            self._send(404, {"ok": False, "error": "not found"})

    # ----- 通用 IPC 桥接 -----
    def _ipc(self, data: Dict) -> None:
        method = data.get("method", "")
        params = data.get("params") or {}
        if not method or method not in IPC_ALLOWED_METHODS:
            self._send(403, {"ok": False, "error": f"method 不在白名单：{method}"})
            return
        if data.get("stream"):
            self._ipc_stream_response(method, params)
            return
        try:
            result = _ipc_call(method, params)
            self._send(200, {"ok": True, "data": result})
        except Exception as e:  # noqa: BLE001
            self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _ipc_stream_response(self, method: str, params: Dict) -> None:
        """流式桥接：把 IPC 的 delta/done 帧转发为 SSE。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for frame in _ipc_stream(method, params):
                line = "data: " + json.dumps(frame, ensure_ascii=False) + "\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
                if frame.get("type") == "done":
                    break
        except Exception as e:  # noqa: BLE001
            try:
                err = {"type": "error", "error": f"{type(e).__name__}: {e}"}
                self.wfile.write(("data: " + json.dumps(err, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except OSError:
                pass

    def _status(self) -> None:
        try:
            data = _ipc_call("status", {})
            self._send(200, {"ok": True, "data": data})
        except Exception as e:  # noqa: BLE001
            self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _delete_messages(self, data: Dict) -> None:
        """本地删除会话内指定消息（直接操作 SessionStore 的 SQLite）。"""
        sid = data.get("session_id", "")
        ids = data.get("ids") or []
        if not sid or not ids:
            self._send(400, {"ok": False, "error": "缺少 session_id 或 ids"})
            return
        cfg = _load()
        db = cfg.home / "sessions.db"
        if not db.exists():
            self._send(200, {"ok": False, "error": "会话库不存在"})
            return
        try:
            conn = sqlite3.connect(str(db))
            try:
                placeholders = ",".join("?" * len(ids))
                cur = conn.execute(
                    f"DELETE FROM messages WHERE session_id=? AND id IN ({placeholders})",
                    [sid] + list(ids))
                conn.commit()
                self._send(200, {"ok": True, "deleted": cur.rowcount})
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    # ----- 兼容旧接口：非流式聊天 -----
    def _chat(self, data: Dict) -> None:
        messages = data.get("messages") or []
        if not messages:
            self._send(400, {"ok": False, "error": "缺少 messages"})
            return
        cfg = _load()
        if not _engine_configured(cfg):
            self._send(200, {"ok": False, "error": "引擎未配置", "guide": True})
            return
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
    print(f"随朴桌面 Agent 界面：http://{HOST}:{PORT}")
    serve(daemon=False)
