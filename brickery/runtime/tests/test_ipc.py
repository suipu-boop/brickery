"""阶段四 IPC 集成测试（clean room）。

启动真实 IPC server（仅 127.0.0.1），经 socket 发 JSON 请求断言行为正确；
并验证"零默认外连"红线：无引擎时 chat 返回友好提示、不触网。
"""
from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from pathlib import Path

from .base import RuntimeTestCase
from brickery.runtime.ipc import IpcServer
from unittest.mock import MagicMock


class _FakeEngine:
    def complete(self, prompt: str, **kw) -> str:
        return f"FAKE::{prompt[:12]}"


def _client(port: int, method: str, params: dict, req_id: int = 1) -> dict:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        s.sendall((json.dumps({"req_id": req_id, "method": method,
                                "params": params}) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        line, _ = buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))
    finally:
        s.close()


class IpcIntegrationTest(RuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.srv = IpcServer(host="127.0.0.1", port=0,
                             home=self.home, models_root=self.models,
                             local_engine=_FakeEngine())
        self.srv.start()
        # 等待端口就绪
        for _ in range(50):
            if self.srv.port:
                break
            time.sleep(0.02)

    def tearDown(self):
        self.srv.stop()
        super().tearDown()

    def test_health(self):
        r = _client(self.srv.port, "health", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["status"], "ok")

    def test_core_set_and_get(self):
        # 「认识我们」步骤：写固定核手动槽 → 可读回
        r = _client(self.srv.port, "core_set", {
            "items": [
                {"attribute": "assistant_name", "value": "巴扎黑"},
                {"attribute": "user_name", "value": "随朴"},
                {"attribute": "user_profile", "value": "临床医生 + 医学高校教师"},
            ]})
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["written"], 3)
        g = _client(self.srv.port, "core_get", {})
        self.assertTrue(g["ok"])
        core = g["data"]["core"]
        self.assertEqual(core.get("assistant_name"), "巴扎黑")
        self.assertEqual(core.get("user_name"), "随朴")
        self.assertEqual(core.get("user_profile"), "临床医生 + 医学高校教师")

    def test_core_set_empty_deletes(self):
        _client(self.srv.port, "core_set", {
            "items": [{"attribute": "assistant_name", "value": "临时名"}]})
        # 空值删除
        _client(self.srv.port, "core_set", {
            "items": [{"attribute": "assistant_name", "value": ""}]})
        g = _client(self.srv.port, "core_get", {})
        self.assertIsNone(g["data"]["core"].get("assistant_name"))

    def test_chat_with_injected_engine(self):
        r = _client(self.srv.port, "chat",
                    {"message": "你好小影子", "project": "test"})
        self.assertTrue(r["ok"])
        self.assertIn("FAKE::", r["data"]["reply"])            # 注入的 fake engine 已生效
        self.assertIn("本地资产 Vault", r["data"]["reply"])    # L1 能力提示已注入 system prompt
        # 存档应可见（记忆子系统接 IPC 工作）
        rc = _client(self.srv.port, "recall", {"query": "你好"})
        self.assertTrue(rc["ok"])
        self.assertGreaterEqual(len(rc["data"]["items"]), 1)

    def test_config_set_api_requires_url(self):
        r = _client(self.srv.port, "config_set", {"backend": "api"})
        self.assertFalse(r["ok"])
        self.assertIn("api_url", r["error"])

    def test_config_set_local_ok_and_persisted(self):
        r = _client(self.srv.port, "config_set",
                    {"backend": "local", "local_model": "x.gguf"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["backend"], "local")
        g = _client(self.srv.port, "config_get", {})
        self.assertEqual(g["data"]["engine"]["local_model"], "x.gguf")

    def test_config_set_open_session_context_persisted(self):
        # 关闭新会话开场上下文开关 → 应真正落盘（此前 _h_config_set 漏接此键，
        # Swift 端 toggle 编得过但状态存不进 config.json，重启即被读回默认 true）。
        r = _client(self.srv.port, "config_set",
                    {"backend": "local", "local_model": "x.gguf",
                     "open_session_context": False})
        self.assertTrue(r["ok"])
        self.assertFalse(self.srv.config.open_session_context)
        cfg_path = self.home / "config.json"
        self.assertTrue(cfg_path.exists())
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertFalse(saved["open_session_context"])

    def test_chat_no_engine_friendly(self):
        # 不注入引擎、构造一个无 GGUF 的 server，应返回友好"无可用后端"且不触网
        srv2 = IpcServer(host="127.0.0.1", port=0,
                         home=self.home, models_root=self.models,
                         build_real_engines=True)
        srv2.start()
        time.sleep(0.1)
        try:
            r = _client(srv2.port, "chat", {"message": "hi"})
            self.assertTrue(r["ok"])
            self.assertIn("没有可用的推理后端", r["data"]["reply"])
        finally:
            srv2.stop()

    def test_doctor_returns_checks(self):
        r = _client(self.srv.port, "doctor", {})
        self.assertTrue(r["ok"])
        self.assertIn("checks", r["data"])
        self.assertIsInstance(r["data"]["checks"], list)
        self.assertGreaterEqual(len(r["data"]["checks"]), 1)

    def test_daemon_lifecycle(self):
        st = _client(self.srv.port, "daemon_status", {})
        self.assertFalse(st["data"]["running"])
        s2 = _client(self.srv.port, "daemon_start", {})
        self.assertTrue(s2["data"]["running"])
        st2 = _client(self.srv.port, "daemon_status", {})
        self.assertTrue(st2["data"]["running"])
        sp = _client(self.srv.port, "daemon_stop", {})
        self.assertFalse(sp["data"]["running"])


class OpenContextTriggerTest(RuntimeTestCase):
    """新会话开场上下文触发逻辑（不启动 socket，直接测 _h_chat 分支）。"""

    def _srv(self):
        srv = IpcServer(host="127.0.0.1", port=0, home=self.home,
                        models_root=self.models, local_engine=_FakeEngine())
        srv.config = MagicMock()
        srv.config.open_session_context = True
        srv.memory = MagicMock()
        srv.memory.open_session_context.return_value = "近期上下文X"
        srv.sessions = MagicMock()
        srv.sessions.ensure.return_value = "sid_new"
        srv.sessions.history.return_value = []
        fake = MagicMock()
        fake.run.return_value = "回复"
        fake.last_tools = []
        fake.last_skills = []
        srv._new_loop = MagicMock(return_value=fake)
        return srv, fake

    def test_new_session_triggers_open_context(self):
        srv, fake = self._srv()
        r = srv._h_chat({"message": "你好", "project": "t"})
        self.assertEqual(r["reply"], "回复")
        self.assertTrue(srv.memory.open_session_context.called)
        self.assertEqual(
            fake.run.call_args.kwargs.get("open_context_text"), "近期上下文X")

    def test_existing_session_skips_open_context(self):
        srv, fake = self._srv()
        r = srv._h_chat({"message": "你好", "session_id": "sid_old", "project": "t"})
        self.assertFalse(srv.memory.open_session_context.called)
        self.assertIsNone(
            fake.run.call_args.kwargs.get("open_context_text"))


if __name__ == "__main__":
    unittest.main()
