"""§4.3 本地模型目录与一键下载（clean room，纯自研）。

职责：
- 维护一份**精选 GGUF 目录**（按内存分档，含通用 / coding 档），供 Swift UI 展示与推荐。
- 读取本机配置（物理内存、芯片），给出「本地能否跑 / 推荐哪个 / 弱机建议用网络」的判断。
- 枚举已安装的 GGUF（~/shadeling-runtime/models/gguf/*.gguf）。
- 后台下载管理器：把 GGUF 拉到模型目录，分块 + 进度回调 + 超时，默认走 **hf-mirror.com**
  （中国可直连的 HF 镜像；原生 huggingface.co 在中国大陆不可达，需代理）。镜像地址可由
  环境变量 BRICKERY_HF_MIRROR 覆盖。

红线：
- 绝不硬编码任何**推理**外部地址；这里只涉及「模型权重文件下载」，与推理后端无关。
- 下载只读用户主动触发的 model_id，不静默联网。
- 不引入第三方依赖：用标准库 urllib 实现。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ----------------------------------------------------------------------------
# 1. 精选 GGUF 目录（按物理内存分档；size_gb 为 Q4 估算，仅供推荐参考）
# ----------------------------------------------------------------------------
# repo/file 取自 HF 社区常见 GGUF 仓库（TheBloke / Qwen / unsloth 等）。
# 文件名采用社区惯例（q4_K_M.gguf），若某仓库实际命名不同，下载会 404，UI 会如实报错。
# 镜像默认 hf-mirror.com，URL 形如 {mirror}/{repo}/resolve/{branch}/{file}。

GGUF_MODELS: List[Dict] = [
    # 全部条目均经 hf-mirror.com 实测 resolve 返回 200（2026-08-10 复验）。
    # 注：Qwen3 系列 GGUF 在 hf-mirror / ModelScope 均无可用源（resolve 实测 404），
    #     故本表未列；Qwen3.5 系列 GGUF 已进镜像缓存（unsloth / lmstudio-community 实测 200）。
    #     用户若本机已有 Qwen3-*.gguf，自动发现仍会认到，不影响使用。
    # priority 越高越优先推荐；source 可覆盖单模型下载源(modelscope / hf-mirror)。
    # sha256 留空时跳过哈希校验（仅做 GGUF 魔数 + 体积校验）。
    {
        "id": "qwen3.5-4b-q4",
        "name": "Qwen3.5-4B Instruct (Q4_K_M)",
        "repo": "unsloth/Qwen3.5-4B-GGUF",
        "file": "Qwen3.5-4B-Q4_K_M.gguf",
        "branch": "main",
        "size_gb": 2.5,
        "ram_min_gb": 4,
        "coding": False,
        "priority": 12,
        "sha256": "",
        "desc": "阿里 Qwen3.5（2025）4B 指令模型，中文母语级，推理/编码/工具调用强；社区 GGUF 已进国内镜像，可一键下载。4B 档中综合最优，本地影子记忆/兜底首选。",
    },
    {
        "id": "gemma-3-4b-q4",
        "name": "Gemma 3 4B Instruct (Q4_K_M)",
        "repo": "unsloth/gemma-3-4b-it-GGUF",
        "file": "gemma-3-4b-it-Q4_K_M.gguf",
        "branch": "main",
        "size_gb": 4.0,
        "ram_min_gb": 6,
        "coding": False,
        "priority": 10,
        "sha256": "",
        "desc": "Google Gemma 3（2025）4B 指令模型，140+ 语言（含中文），4B 档还带视觉；体积小效果好，16GB 机器首选通用模型。",
    },
    {
        "id": "phi-4-mini-q4",
        "name": "Phi-4-mini 3.8B Instruct (Q4_K_M)",
        "repo": "unsloth/Phi-4-mini-instruct-GGUF",
        "file": "Phi-4-mini-instruct-Q4_K_M.gguf",
        "branch": "main",
        "size_gb": 2.5,
        "ram_min_gb": 4,
        "coding": False,
        "priority": 9,
        "sha256": "",
        "desc": "微软 Phi-4-mini（2025，MIT）3.8B，推理/数学/代码强，128K 上下文，支持 function calling；小体积高智商。",
    },
    {
        "id": "llama-3.1-8b-q4",
        "name": "Llama-3.1-8B-Instruct (Q4_K_M)",
        "repo": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "branch": "main",
        "size_gb": 4.9,
        "ram_min_gb": 8,
        "coding": False,
        "priority": 8,
        "sha256": "",
        "desc": "Meta Llama 3.1 8B（2024）旗舰，英文与通用能力出色，社区生态最广。",
    },
    {
        "id": "qwen2.5-coder-7b-q4",
        "name": "Qwen2.5-Coder-7B (Q4_K_M)",
        "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "file": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "branch": "main",
        "size_gb": 4.8,
        "ram_min_gb": 8,
        "coding": True,
        "priority": 6,
        "sha256": "",
        "desc": "阿里 Qwen2.5-Coder 7B（2024）代码模型，补全/单测/改写强。",
    },
    {
        "id": "qwen2.5-coder-3b-q4",
        "name": "Qwen2.5-Coder-3B (Q4_K_M)",
        "repo": "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        "file": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        "branch": "main",
        "size_gb": 2.2,
        "ram_min_gb": 6,
        "coding": True,
        "priority": 5,
        "sha256": "",
        "desc": "阿里 Qwen2.5-Coder 3B（2024）轻量代码模型，低配机也能跑。",
    },
]

# 低于此内存（GB）直接建议用网络模型，不推荐本地
WEAK_RAM_THRESHOLD_GB = 8


# ----------------------------------------------------------------------------
# 2. 机器配置探测（仅本地读取，不联网、不上报）
# ----------------------------------------------------------------------------
def detect_ram_gb() -> float:
    """返回物理内存（GB，浮点）。macOS 用 sysctl hw.memsize；Linux 读 /proc/meminfo。"""
    try:
        if os.uname().sysname == "Darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            bytes_ = int(out.stdout.strip())
            return round(bytes_ / (1024 ** 3), 1)
        # Linux
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 ** 2), 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return 0.0


def detect_chip() -> str:
    """返回芯片/CPU 描述（macOS 用 machdep.cpu.brand_string；Linux 读 /proc/cpuinfo）。"""
    try:
        if os.uname().sysname == "Darwin":
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5,
            )
            return out.stdout.strip() or "Apple Silicon"
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "未知"


# ----------------------------------------------------------------------------
# 3. 推荐逻辑
# ----------------------------------------------------------------------------
def list_installed(models_root: Path) -> List[Dict]:
    """枚举已安装 GGUF：返回 [{name, path, size_gb}]。"""
    gguf_dir = Path(models_root) / "gguf"
    out: List[Dict] = []
    if gguf_dir.is_dir():
        for p in sorted(gguf_dir.glob("*.gguf")):
            try:
                size_gb = round(p.stat().st_size / (1024 ** 3), 2)
            except OSError:
                size_gb = 0.0
            out.append({"name": p.name, "path": str(p), "size_gb": size_gb})
    return out


def recommend_for_ram(ram_gb: float, coding: bool = False,
                      installed: Optional[List[Dict]] = None) -> Dict:
    """根据内存 + 是否要 coding + 已装模型，给出推荐。

    返回：
    {
      "ram_gb": float,
      "weak": bool,                       # 内存偏弱，建议用网络
      "backend_suggestion": "local"|"api",
      "local": [ {catalog entry...} ],     # 适合本机内存的 GGUF（已按 ram 过滤）
      "installed_match": [name...],        # 已装且符合档位的
      "note": str,
    }
    """
    installed = installed or []
    installed_names = {x["name"] for x in installed}
    weak = ram_gb > 0 and ram_gb < WEAK_RAM_THRESHOLD_GB

    candidates = [m for m in GGUF_MODELS if m["ram_min_gb"] <= (ram_gb or 999)]
    if coding:
        candidates = [m for m in candidates if m.get("coding")]
    # 按 priority 降序（新架构/综合更优的优先），同档按内存需求升序（更省内存优先）
    candidates.sort(key=lambda m: (-m.get("priority", 0), m["ram_min_gb"]))

    installed_match = [m["name"] for m in candidates if m["name"] in installed_names]

    if weak:
        return {
            "ram_gb": ram_gb,
            "weak": True,
            "backend_suggestion": "api",
            "local": [],
            "installed_match": installed_match,
            "note": (f"本机物理内存约 {ram_gb:.1f}GB，偏小。"
                     "本地大模型会**严重拖慢甚至跑不动**：模型权重 + 上下文 + 系统开销会吃满内存，"
                     "导致推理卡顿、Mac 风扇狂转、甚至崩溃。"
                     "强烈建议以**网络 API 为主**（国内 DeepSeek / 通义 / 智谱等均可直连，质量最高、零内存负担），"
                     "本地 GGUF 仅作为**离线 / 兜底**，且只选最小档（3B 及以下）。"),
        }
    # 根据推荐的最大模型参数量给出短板说明
    max_params = max((m["ram_min_gb"] for m in candidates), default=0)
    if max_params <= 6:
        limitation = (
            "【本地模式的体验权衡】本地 3B~4B 级模型仅适合简单对话和基本记忆整理，"
            "复杂推理、长文写作、代码生成能力**明显弱于网络大模型**，"
            "且本地推理速度慢、占内存、设备发热。"
            "若追求回复质量与流畅体验，请切换到「网络 API」；"
            "本地模式适合数据不出本机、离线、或作为兜底。"
        )
    elif max_params <= 8:
        limitation = (
            "【本地模式的体验权衡】本地 7B 级模型日常对话够用，"
            "但复杂推理、多步规划、专业知识深度不及网络大模型（如 DeepSeek-V3 / GPT-4o），"
            "且本地推理会占用较多内存、设备易发热。"
            "对回答质量要求高的场景建议切换到「网络 API」。"
        )
    else:
        limitation = (
            "【本地模式的体验权衡】本地 14B 级模型质量较好但推理偏慢、占内存大，"
            "极端复杂任务仍不及顶级网络模型。对延迟敏感或需最强能力时建议切换到「网络 API」。"
        )
    return {
        "ram_gb": ram_gb,
        "weak": False,
        "backend_suggestion": "api",
        "local": candidates[:5],
        "installed_match": installed_match,
        "note": (f"本机物理内存约 {ram_gb:.1f}GB。首版推荐以**网络 API 为主**"
                 f"（国内 DeepSeek / 通义 / 智谱等可直连，质量最高、零本地负担）；"
                 f"本地 GGUF 作为离线/隐私/兜底，本机可跑以下："
                 f"（已安装：{', '.join(installed_match) or '无'}）。"
                 "点「下载」即拉取权重到模型目录。\n\n" + limitation),
    }


# ----------------------------------------------------------------------------
# 4. 下载管理器（后台线程 + 进度轮询）
# ----------------------------------------------------------------------------
@dataclass
class _DownloadState:
    state: str = "idle"          # idle | downloading | paused | done | error | cancelled
    bytes_done: int = 0
    bytes_total: int = 0
    path: str = ""
    error: str = ""
    stop_ev: threading.Event = field(default_factory=threading.Event)   # 取消
    pause_ev: threading.Event = field(default_factory=threading.Event)  # 暂停
    part_path: str = ""          # .part 暂存路径（暂停时保留，续传复用）


_active: Dict[str, _DownloadState] = {}
_lock = threading.Lock()


def _mirror_base() -> str:
    return os.environ.get("BRICKERY_HF_MIRROR", "https://hf-mirror.com").rstrip("/")


def _source_base(entry: Dict) -> str:
    """按单模型 source 字段选下载基址；默认走 hf-mirror（国内稳）。"""
    src = entry.get("source", "hf-mirror")
    if src == "modelscope":
        return "https://modelscope.cn/models"
    return _mirror_base()


def _model_entry(model_id: str) -> Optional[Dict]:
    return next((m for m in GGUF_MODELS if m["id"] == model_id), None)


def _build_url(entry: Dict) -> str:
    base = _source_base(entry)
    return f"{base}/{entry['repo']}/resolve/{entry.get('branch', 'main')}/{entry['file']}"


def start_download(model_id: str, models_root: Path, resume: bool = False) -> Dict:
    """启动/续传后台下载。返回 {ok, error}。

    - resume=False：全新开始（清 .part、bytes 归零）。
    - resume=True ：仅当存在 paused 态 + .part 文件时续传（HTTP Range），否则降级为全新开始。
    暂停/取消通过 _DownloadState 的 pause_ev / stop_ev 事件通知下载线程在分块边界退出。
    """
    entry = _model_entry(model_id)
    if not entry:
        return {"ok": False, "error": f"未知模型 id：{model_id}"}
    gguf_dir = Path(models_root) / "gguf"
    gguf_dir.mkdir(parents=True, exist_ok=True)
    dest = gguf_dir / entry["file"]
    part = gguf_dir / (entry["file"] + ".part")

    with _lock:
        st = _active.get(model_id)
        if st is None:
            st = _DownloadState()
            _active[model_id] = st
        if st.state == "downloading":
            return {"ok": True}
        do_resume = resume and st.state == "paused" and part.exists()
        if not do_resume:
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            st.bytes_done = 0
            st.bytes_total = 0
            st.error = ""
        st.state = "downloading"
        st.stop_ev.clear()
        st.pause_ev.clear()
        st.part_path = str(part)

    def _run():
        st = _active[model_id]
        # do_resume 在闭包外已判定并定稿，用局部变量捕获
        dest_p = Path(models_root) / "gguf" / entry["file"]
        part_p = Path(st.part_path) if st.part_path else (dest_p.parent / (dest_p.name + ".part"))
        resume_at = st.bytes_done if do_resume else 0
        try:
            url = _build_url(entry)
            headers = {"User-Agent": "Brickery"}
            if resume_at > 0:
                headers["Range"] = f"bytes={resume_at}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                # 206=支持续传；200=服务器忽略 Range（从头下，需重置）
                if resume_at > 0 and resp.status == 200:
                    resume_at = 0
                    try:
                        if part_p.exists():
                            part_p.unlink()
                    except OSError:
                        pass
                if resume_at > 0 and resp.status == 206:
                    remaining = int(resp.headers.get("Content-Length", 0) or 0)
                    st.bytes_total = resume_at + remaining
                else:
                    st.bytes_total = int(resp.headers.get("Content-Length", 0) or 0)
                mode = "ab" if resume_at > 0 else "wb"
                with open(part_p, mode) as f:
                    while True:
                        if st.stop_ev.is_set() or st.pause_ev.is_set():
                            break
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        st.bytes_done += len(chunk)
            # 收尾：按退出标志分流
            if st.stop_ev.is_set():
                try:
                    if part_p.exists():
                        part_p.unlink()
                except OSError:
                    pass
                st.state = "cancelled"
                return
            if st.pause_ev.is_set():
                # 暂停：保留 .part，等待续传
                st.state = "paused"
                return
            # 完整完成：完整性校验
            size = part_p.stat().st_size
            if size == 0:
                raise IOError("下载完成但文件为空，可能镜像 404 或返回错误页。")
            with open(part_p, "rb") as f:
                magic = f.read(4)
            if magic != b"GGUF":
                try:
                    part_p.unlink()
                except OSError:
                    pass
                raise IOError("文件头不是 GGUF 魔数，可能镜像返回了错误页，已删除。")
            if st.bytes_total and size != st.bytes_total:
                try:
                    part_p.unlink()
                except OSError:
                    pass
                raise IOError(
                    f"体积不符（期望 {st.bytes_total}，实际 {size}），可能截断，已删除。")
            sha = entry.get("sha256")
            if sha:
                import hashlib
                h = hashlib.sha256()
                with open(part_p, "rb") as f:
                    for blk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(blk)
                if h.hexdigest().lower() != sha.lower():
                    try:
                        part_p.unlink()
                    except OSError:
                        pass
                    raise IOError("sha256 校验失败，文件可能已被篡改，已删除。")
            part_p.rename(dest_p)
            st.path = str(dest_p)
            st.state = "done"
        except Exception as e:  # noqa: BLE001
            st.state = "error"
            st.error = str(e)
            try:
                if part_p.exists():
                    part_p.unlink()
            except OSError:
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True}


def pause_download(model_id: str) -> Dict:
    """暂停进行中的下载（分块边界退出，保留 .part 供续传）。"""
    with _lock:
        st = _active.get(model_id)
        if st is None or st.state != "downloading":
            return {"ok": False, "error": "没有进行中的下载"}
        st.pause_ev.set()
    return {"ok": True}


def cancel_download(model_id: str) -> Dict:
    """取消下载并删除已下载的 .part 暂存。"""
    with _lock:
        st = _active.get(model_id)
        if st is None:
            return {"ok": False, "error": "没有进行中的下载"}
        st.stop_ev.set()
    return {"ok": True}


def resume_download(model_id: str, models_root: Path) -> Dict:
    """从暂停点续传（HTTP Range）。"""
    return start_download(model_id, models_root, resume=True)


def delete_model_file(name: str, models_root: Path) -> Dict:
    """删除已安装模型文件（按文件名）。带路径穿越防护。"""
    gguf_dir = Path(models_root) / "gguf"
    try:
        gguf_dir.mkdir(parents=True, exist_ok=True)
        target = (gguf_dir / name).resolve()
        root = gguf_dir.resolve()
        # 必须落在 gguf_dir 内、且以 .gguf 结尾，防穿越
        if not str(target).startswith(str(root) + os.sep) or not name.endswith(".gguf"):
            return {"ok": False, "error": "非法模型名或路径"}
        if target.exists() and target.is_file():
            target.unlink()
            return {"ok": True}
        return {"ok": False, "error": f"未找到模型文件：{name}"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def download_status(model_id: str) -> Dict:
    with _lock:
        st = _active.get(model_id)
        if st is None:
            return {"state": "idle", "bytes_done": 0, "bytes_total": 0,
                    "path": "", "error": ""}
        return {
            "state": st.state,
            "bytes_done": st.bytes_done,
            "bytes_total": st.bytes_total,
            "path": st.path,
            "error": st.error,
        }
