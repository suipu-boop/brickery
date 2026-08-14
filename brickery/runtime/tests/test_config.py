"""§6 配置管理单测（B1 纯数据层版）。

注：TestProfileApiKeyPreservation（依赖 runtime.ipc，B5 服务层）待 B5 迁入后补回。
"""
from brickery.runtime.config import Config, EngineConfig, load_config
from .base import RuntimeTestCase


class TestConfig(RuntimeTestCase):
    def test_default_engine_is_api(self):
        # §4 决策：API 为主、本地 GGUF 为备选（随朴 2026-08-06 冻结）
        cfg = Config(home=self.home, models_root=self.models)
        self.assertEqual(cfg.engine.backend, "api")
        self.assertEqual(cfg.home, self.home)
        self.assertEqual(cfg.models_root, self.models)

    def test_save_and_reload(self):
        cfg = Config(home=self.home, models_root=self.models,
                     engine=EngineConfig(backend="api",
                                         api_url="https://user.example/v1"))
        cfg.save()
        reloaded = load_config(home=self.home, models_root=self.models)
        self.assertEqual(reloaded.engine.backend, "api")
        self.assertEqual(reloaded.engine.api_url, "https://user.example/v1")

    def test_corrupt_config_falls_back(self):
        # 写入非法 JSON，load 应告警并回退安全默认，不崩溃
        (self.home / "config.json").write_text("{bad json", encoding="utf-8")
        cfg = load_config(home=self.home, models_root=self.models)
        self.assertEqual(cfg.engine.backend, "api")

    def test_user_api_accepted_explicitly(self):
        cfg = Config(home=self.home, models_root=self.models,
                     engine=EngineConfig(backend="api",
                                         api_url="https://user.example/x"))
        cfg.save()
        r = load_config(home=self.home, models_root=self.models)
        self.assertEqual(r.engine.api_url, "https://user.example/x")
