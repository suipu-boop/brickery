import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from brickery.runtime import model_catalog


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # /good/resolve/main/file.gguf -> 200 + body
        # 其余 -> 404
        if self.path.endswith("/good/resolve/main/file.gguf"):
            body = b"GGUF-FAKE-WEIGHTS"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):  # 静默
        pass


class TestModelCatalog(unittest.TestCase):
    def test_recommend_weak_machine_suggests_api(self):
        rec = model_catalog.recommend_for_ram(4.0)
        self.assertTrue(rec["weak"])
        self.assertEqual(rec["backend_suggestion"], "api")
        self.assertEqual(rec["local"], [])

    def test_recommend_16gb_api_primary_with_local_candidates(self):
        # §4 决策：即便本机内存够跑本地，也**首推 API**（本地仅作备选）。
        rec = model_catalog.recommend_for_ram(16.0)
        self.assertFalse(rec["weak"])
        self.assertEqual(rec["backend_suggestion"], "api")
        self.assertTrue(len(rec["local"]) > 0)
        # 16GB 候选应含新架构旗舰 gemma-3-4b-q4；首位推荐为 qwen3.5-4b-q4（priority 最高）
        ids = [m["id"] for m in rec["local"]]
        self.assertIn("gemma-3-4b-q4", ids)
        self.assertEqual(rec["local"][0]["id"], "qwen3.5-4b-q4")

    def test_recommend_coding_filters(self):
        rec = model_catalog.recommend_for_ram(16.0, coding=True)
        ids = [m["id"] for m in rec["local"]]
        self.assertTrue(all("coder" in i for i in ids))
        self.assertIn("qwen2.5-coder-7b-q4", ids)

    def test_list_installed(self):
        d = Path(__file__).parent / "_tmpeg"
        gguf = d / "gguf"
        gguf.mkdir(parents=True, exist_ok=True)
        f = gguf / "mymodel.gguf"
        f.write_text("x" * 1024)
        try:
            installed = model_catalog.list_installed(d)
            self.assertEqual(len(installed), 1)
            self.assertEqual(installed[0]["name"], "mymodel.gguf")
        finally:
            f.unlink()
            gguf.rmdir()
            d.rmdir()

    def test_detect_ram_returns_positive(self):
        ram = model_catalog.detect_ram_gb()
        self.assertIsInstance(ram, float)
        self.assertGreater(ram, 0)


class TestModelDownload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _entry(self, mid):
        return {"id": mid, "repo": "good", "file": "file.gguf", "branch": "main"}

    def test_download_success(self):
        base = f"http://127.0.0.1:{self.port}"
        os.environ["SHADELING_HF_MIRROR"] = base
        orig = model_catalog._model_entry
        model_catalog._model_entry = self._entry
        tmp = Path(__file__).parent / "_tmpdl"
        try:
            r = model_catalog.start_download("test", tmp)
            self.assertTrue(r["ok"])
            for _ in range(100):
                s = model_catalog.download_status("test")
                if s["state"] in ("done", "error"):
                    break
                time.sleep(0.02)
            s = model_catalog.download_status("test")
            self.assertEqual(s["state"], "done")
            self.assertTrue((tmp / "gguf" / "file.gguf").exists())
        finally:
            model_catalog._model_entry = orig
            if (tmp / "gguf" / "file.gguf").exists():
                (tmp / "gguf" / "file.gguf").unlink()
            if (tmp / "gguf").exists():
                (tmp / "gguf").rmdir()
            if tmp.exists():
                tmp.rmdir()
            os.environ.pop("SHADELING_HF_MIRROR", None)

    def test_download_404_error(self):
        base = f"http://127.0.0.1:{self.port}"
        os.environ["SHADELING_HF_MIRROR"] = base
        # 指向不存在的路径 -> 404
        orig = model_catalog._model_entry
        model_catalog._model_entry = lambda mid: {"id": mid, "repo": "missing",
                                                  "file": "nope.gguf", "branch": "main"}
        tmp = Path(__file__).parent / "_tmpdl2"
        try:
            r = model_catalog.start_download("test", tmp)
            self.assertTrue(r["ok"])
            for _ in range(100):
                s = model_catalog.download_status("test")
                if s["state"] in ("done", "error"):
                    break
                time.sleep(0.02)
            s = model_catalog.download_status("test")
            self.assertEqual(s["state"], "error")
        finally:
            model_catalog._model_entry = orig
            os.environ.pop("SHADELING_HF_MIRROR", None)


if __name__ == "__main__":
    unittest.main()
