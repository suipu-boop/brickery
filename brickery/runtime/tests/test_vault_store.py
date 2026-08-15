"""VaultStore 回归测试：路径同源推导 + sync_skills 全量同步 + 基础增/查/提醒。

覆盖 2026-08-12 修的两个真实 bug：
- 路径分裂：VAULT_DIR 必须与 ipc 同源（经 BRICKERY_HOME 推导），否则 UI 与 agent 各写各库。
- sync_skills 的 return 缩进错误：只同步首个技能，其余丢失。
"""
import os
import tempfile
import time
import unittest

from brickery.runtime.vault_store import VaultStore, _resolve_vault_dir


class TestVaultStore(unittest.TestCase):
    def setUp(self):
        self._orig_home = os.environ.get("BRICKERY_HOME")
        self.tmp = tempfile.mkdtemp()
        os.environ["BRICKERY_HOME"] = self.tmp

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("BRICKERY_HOME", None)
        else:
            os.environ["BRICKERY_HOME"] = self._orig_home

    def test_resolve_dir_under_BRICKERY_HOME(self):
        # 路径同源：必须落在 BRICKERY_HOME/vault，与 ipc._vault() 一致
        d = _resolve_vault_dir()
        self.assertTrue(str(d).startswith(self.tmp),
                        f"Vault 目录应在 BRICKERY_HOME 下，实际 {d}")
        self.assertTrue(str(d).endswith("vault"), f"应为 vault 子目录，实际 {d}")

    def test_sync_skills_syncs_all(self):
        # Bug A 回归：必须同步全部技能，而非只首个
        s = VaultStore(root=os.path.join(self.tmp, "vault"))
        skills = [{"id": f"s{i}", "name": f"技能{i}", "version": "1.0",
                   "category": "x", "enabled": True} for i in range(3)]
        n = s.sync_skills(skills)
        self.assertEqual(n, 3, "sync_skills 应同步全部技能")
        self.assertEqual(len(s.list(type="skill_snapshot")), 3)

    def test_add_query_upcoming(self):
        s = VaultStore(root=os.path.join(self.tmp, "vault"))
        future = time.strftime("%Y-%m-%d", time.localtime(time.time() + 5 * 86400))
        s.add({"type": "document", "title": "医师证",
               "fields": {"doc_type": "资格证", "valid_to": future}})
        self.assertEqual(len(s.query("医师")), 1, "query 应按关键词命中")
        self.assertEqual(len(s.upcoming(30)), 1, "upcoming 应召回临期资产")

    def test_upcoming_excludes_far_future(self):
        s = VaultStore(root=os.path.join(self.tmp, "vault"))
        far = time.strftime("%Y-%m-%d", time.localtime(time.time() + 400 * 86400))
        s.add({"type": "document", "title": "护照",
               "fields": {"doc_type": "护照", "valid_to": far}})
        self.assertEqual(len(s.upcoming(30)), 0, "远超窗口的到期不应召回")


if __name__ == "__main__":
    unittest.main()
