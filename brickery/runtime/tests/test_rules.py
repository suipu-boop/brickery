"""P2 持久规则单测：rules.json + SHADERULES.md 加载、空/损坏安全降级。"""
import json
import unittest
from pathlib import Path

from brickery.runtime.rules import load_rules


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(__file__).resolve().parent / "_tmp_rules"
        if self.home.exists():
            for f in self.home.glob("*"):
                f.unlink()
        self.home.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.home.exists():
            shutil.rmtree(self.home, ignore_errors=True)

    def test_rules_json(self):
        (self.home / "rules.json").write_text(
            json.dumps({"rules": ["始终用中文回复", "  ", "不编造出处"]}),
            encoding="utf-8")
        rs = load_rules(self.home)
        self.assertEqual(rs, ["始终用中文回复", "不编造出处"])

    def test_shaderules_md(self):
        (self.home / "SHADERULES.md").write_text(
            "# 我的规则\n- 回复简短\n- 用 Markdown 列表\n普通行不抽\n",
            encoding="utf-8")
        rs = load_rules(self.home)
        self.assertIn("回复简短", rs)
        self.assertIn("用 Markdown 列表", rs)
        self.assertIn("普通行不抽", rs)

    def test_both_sources_merged(self):
        (self.home / "rules.json").write_text(
            json.dumps({"rules": ["来自json"]}), encoding="utf-8")
        (self.home / "SHADERULES.md").write_text(
            "- 来自md\n", encoding="utf-8")
        rs = load_rules(self.home)
        self.assertEqual(rs, ["来自json", "来自md"])

    def test_empty_when_none(self):
        self.assertEqual(load_rules(self.home), [])

    def test_malformed_json_safe(self):
        (self.home / "rules.json").write_text("{not valid json", encoding="utf-8")
        self.assertEqual(load_rules(self.home), [])


if __name__ == "__main__":
    unittest.main()
