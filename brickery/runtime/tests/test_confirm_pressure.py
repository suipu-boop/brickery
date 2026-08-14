"""confirm 忙循环压力行为冒烟测试（P16 配套真进程验证）。

背景：0.3.35 真机「说话没反应」，根因是前端 confirm 轮询在无待确认项时
收到空响应后无 sleep 立即重发，每个请求新建 socket 连 IPC 端口，
毫秒级轰炸导致 TIME_WAIT 堆积耗尽本地临时端口 → [Errno 49]。

修复（P16）：后端 next_pending 无未取项时也阻塞到超时（Condition），
杜绝「立即返回空 → 前端立即重发」的忙循环失控。

本测试模拟前端 confirm 忙循环的**真实行为**：起真实后端进程，用多个线程
高频「connect → confirm_next → 断开」，断言：
- 后端进程全程存活不崩。
- confirm 请求在忙循环期间**不大量报错**（后端能正常处理空响应）。
- 忙循环期间新增的 IPC TIME_WAIT 连接数**相对可控**（不成千上万堆积）。

注意：本测试**不依赖 chat**（chat 会触发本地 GGUF 加载干扰，且非本测试目标）。
聚焦「confirm 忙循环不烧端口」这一 P16 主张。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path


def _random_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_backend(repo_root: Path, port: int, home: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["BRICKERY_HOME"] = str(home)
    env["PYTHONPATH"] = str(repo_root)
    env["SHADELING_SKIP_CONNECTORS"] = "1"
    return subprocess.Popen(
        [sys.executable, "-m", "brickery.runtime.ipc", "--port", str(port), "--home", str(home)],
        cwd=str(repo_root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _wait_ready(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0) as s:
                s.settimeout(2.0)
                payload = json.dumps({"req_id": 1, "method": "health", "params": {}}) + "\n"
                s.sendall(payload.encode("utf-8"))
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                if json.loads(buf.decode("utf-8")).get("ok"):
                    return True
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        time.sleep(0.3)
    return False


def _ipc_request(host: str, port: int, method: str, params: dict = None,
                 timeout: float = 5.0) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        payload = json.dumps({"req_id": 1, "method": method,
                              "params": params or {}}) + "\n"
        s.sendall(payload.encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode("utf-8"))


def _tw_to_port(target_port: int) -> int:
    """统计到指定端口的 TIME_WAIT 连接数（netstat 解析，拿不到返回 -1）。"""
    try:
        out = subprocess.run(["/usr/sbin/netstat", "-an"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return -1
    n = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != "tcp4" or parts[5] != "TIME_WAIT":
            continue
        try:
            if int(parts[4].rsplit(":", 1)[-1]) == target_port:
                n += 1
        except ValueError:
            continue
    return n


class ConfirmBusyLoopPressureTest(unittest.TestCase):
    """真进程：模拟前端 confirm 忙循环，验证后端不崩、confirm 不大量报错、端口不失控。"""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent.parent.parent
        self.home = Path(f"/tmp/brickery_confirm_pressure_{os.getpid()}")
        self.home.mkdir(parents=True, exist_ok=True)
        # 最小配置：api 指向不可达端口（本测试不依赖 chat 成功，避免 GGUF 加载干扰）
        config = {
            "backend": "api",
            "api_url": "http://127.0.0.1:1/v1",
            "api_key": "sk-test-pressure",
            "api_model": "test-model",
            "profiles": [{
                "id": "default", "name": "默认",
                "api_url": "http://127.0.0.1:1/v1",
                "api_key": "sk-test-pressure", "api_model": "test-model",
            }],
            "active_profile_id": "default",
        }
        (self.home / "config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8")
        self.backend_port = _random_port()
        self.proc = _start_backend(self.repo_root, self.backend_port, self.home)
        self.addCleanup(self._kill_proc)

    def _kill_proc(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def test_confirm_busy_loop_does_not_kill_backend(self):
        """模拟前端 confirm 忙循环：多线程高频 confirm_next，后端不崩、confirm 不大量报错。"""
        self.assertTrue(_wait_ready("127.0.0.1", self.backend_port, timeout=15),
                        "后端未就绪")

        stop = threading.Event()
        confirm_errcount = [0]

        def confirm_worker(wait_s: float):
            while not stop.is_set():
                try:
                    resp = _ipc_request(
                        "127.0.0.1", self.backend_port, "confirm_next",
                        {"wait": wait_s}, timeout=3.0)
                    if not resp.get("ok"):
                        confirm_errcount[0] += 1
                except Exception:
                    confirm_errcount[0] += 1

        # 忙循环前基线 TIME_WAIT（到本 ipc 端口）
        base_tw = _tw_to_port(self.backend_port)

        workers = [threading.Thread(target=confirm_worker, args=(0.05,))
                   for _ in range(3)]
        for w in workers:
            w.start()
        try:
            time.sleep(1.0)  # 忙循环轰炸 1 秒
        finally:
            stop.set()
            for w in workers:
                w.join(timeout=5)

        # 后端必须存活
        self.assertIsNone(self.proc.poll(),
                          f"忙循环后后端退出: {self.proc.poll()}")

        # confirm 请求不应大量报错（后端能正常处理空响应）
        self.assertLess(confirm_errcount[0], 5,
                        f"confirm 忙循环 {confirm_errcount[0]} 次 IPC 错误（后端应能处理空响应）")

        # 忙循环 1s 后到本 ipc 端口的 TIME_WAIT 应相对可控（不失控堆积）
        # 修复前：无 sleep 忙循环 + 立即返回会导致毫秒级新建连接，TIME_WAIT 上千。
        # 修复后：next_pending 阻塞到 wait 超时，连接频率被压低，TIME_WAIT 应远低于失控阈值。
        after_tw = _tw_to_port(self.backend_port)
        growth = max(0, after_tw - max(0, base_tw))
        self.assertLess(growth, 500,
                        f"忙循环 1s 后到 IPC 端口 TIME_WAIT 增长 {growth}（应受控，<500）")

        # 后端在忙循环后仍能响应 health（未被打垮）
        self.assertTrue(_ipc_request("127.0.0.1", self.backend_port, "health",
                                     timeout=5).get("ok"),
                        "忙循环后 health 无响应（后端被拖垮）")


if __name__ == "__main__":
    unittest.main()