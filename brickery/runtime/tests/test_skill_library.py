"""§3.x 在线技能市场客户端单测（file:// fixture 全链路，无需真服务器）。

覆盖：索引拉取、安装(provenance+落盘)、幂等、卸载、升级、校验拒绝、不可达优雅失败。
"""
import json
import tempfile
from pathlib import Path
from unittest import TestCase

from brickery.runtime.skill_library import (
    SkillLibrary, validate_skill_package, SkillPackageError, split_version)
from brickery.runtime.skills import SkillRegistry

REPO_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "skill_repo"
REPO_URL = "file://" + str(REPO_DIR)


class TestFixtureRepo(TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="brickery_mkt_"))

    def _lib(self, url=REPO_URL):
        return SkillLibrary(url, self.home)

    def test_fetch_index_ok(self):
        idx, err = self._lib().fetch_index()
        self.assertIsNone(err, err)
        self.assertEqual(idx["schema"], "shadeling-skill-repo/v1")
        # 现状 5 个策展技能（pdf-extractor / meeting-minutes /
        # code-reviewer / document-writer / high-config-doc）。新增须同步更新此处断言。
        self.assertEqual(len(idx["skills"]), 5)
        ids = {s["id"] for s in idx["skills"]}
        self.assertIn("document-writer", ids)
        self.assertIn("high-config-doc", ids)

    def test_list_entries_with_installed_marker(self):
        lib = self._lib()
        reg = SkillRegistry()
        lib.install("pdf-extractor", reg)
        entries, err = lib.list_entries(reg)
        self.assertIsNone(err, err)
        by_id = {e.id: e for e in entries}
        self.assertEqual(by_id["pdf-extractor"].installed_version, "1.0.0")
        self.assertIsNone(by_id["code-reviewer"].installed_version)

    def test_install_writes_provenance_and_persists(self):
        lib = self._lib()
        reg = SkillRegistry()
        skill, err = lib.install("pdf-extractor", reg)
        self.assertIsNone(err, err)
        self.assertIsNotNone(skill)
        self.assertEqual(skill.source, "pdf-extractor")
        self.assertTrue(skill.installed_at)  # 安装时间戳已写
        self.assertEqual(skill.version, "1.0.0")
        # 落盘后可重新载入并保留 provenance
        reg2 = SkillRegistry()
        n = reg2.load(self.home / "skills.json")
        self.assertEqual(n, 1)
        loaded = reg2.get("PDF 提取器")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.source, "pdf-extractor")
        self.assertEqual(loaded.summary, "从 PDF 提取正文并生成结构化摘要")

    def test_install_idempotent_no_upgrade(self):
        lib = self._lib()
        reg = SkillRegistry()
        _, err1 = lib.install("pdf-extractor", reg)
        self.assertIsNone(err1)
        skill, err2 = lib.install("pdf-extractor", reg)  # 同版本再装
        self.assertIsNone(skill)
        self.assertIn("无需升级", err2)

    def test_uninstall(self):
        lib = self._lib()
        reg = SkillRegistry()
        lib.install("meeting-minutes", reg)
        self.assertTrue(any(s.source == "meeting-minutes" for s in reg.all()))
        ok, err = lib.uninstall("meeting-minutes", reg)
        self.assertTrue(ok, err)
        self.assertFalse(any(s.source == "meeting-minutes" for s in reg.all()))
        # 落盘也移除
        reg2 = SkillRegistry(); reg2.load(self.home / "skills.json")
        self.assertFalse(any(s.source == "meeting-minutes" for s in reg2.all()))

    def test_upgrade_forces_reinstall(self):
        lib = self._lib()
        reg = SkillRegistry()
        lib.install("code-reviewer", reg)
        # 人为把本地版本降级，模拟「远程有新版」
        local = reg.get("代码审查")
        local.version = "0.5.0"
        reg.save(self.home / "skills.json")
        skill, err = lib.upgrade("code-reviewer", reg)
        self.assertIsNone(err, err)
        self.assertEqual(skill.version, "0.9.0")

    def test_validate_rejects_missing_name(self):
        with self.assertRaises(SkillPackageError):
            validate_skill_package({"trigger": ["x"], "content": "c"})

    def test_validate_rejects_oversize_content(self):
        with self.assertRaises(SkillPackageError):
            validate_skill_package({"name": "big", "trigger": ["x"],
                                    "content": "x" * 50001})

    def test_validate_rejects_path_traversal(self):
        with self.assertRaises(SkillPackageError):
            validate_skill_package({"name": "evil", "trigger": ["x"],
                                    "content": "c", "files": ["../escape.json"]})

    def test_repo_unreachable_graceful(self):
        lib = SkillLibrary("file:///nonexistent/path/repo", self.home)
        entries, err = lib.list_entries(SkillRegistry())
        self.assertIsNone(entries)
        self.assertIsNotNone(err)  # 不可达返回错误，不崩溃

    def test_empty_repo_url_error(self):
        lib = SkillLibrary("", self.home)
        entries, err = lib.list_entries(SkillRegistry())
        self.assertIsNone(entries)
        self.assertIn("未配置", err)

    def test_split_version(self):
        self.assertTrue(split_version("1.2.3") > split_version("1.2.0"))
        self.assertTrue(split_version("2.0.0") > split_version("1.9.9"))

    def test_review_returns_full_package_without_installing(self):
        lib = self._lib()
        reg = SkillRegistry()
        skill, err = lib.review("pdf-extractor", reg)
        self.assertIsNone(err, err)
        self.assertIsNotNone(skill)
        self.assertIn("PDF", skill.content)
        # 审阅不应写入本地（provenance 为空）
        self.assertEqual(skill.source, "")
        self.assertEqual(len(reg.all()), 0)
        self.assertFalse((self.home / "skills.json").exists())


class TestUiContract(TestCase):
    """Step1：积木 UI 注册扩展（buttons / views）契约翻译与 D7 控权校验。

    非法**单条**丢弃并告警、绝不整包报错——保持对既有积木的向后兼容。
    """

    def _pkg(self, **ui):
        pkg = {"name": "ui-test", "trigger": ["ui"], "content": "c"}
        pkg.update(ui)
        return validate_skill_package(pkg)

    def test_valid_package_keeps_buttons_and_views(self):
        s = self._pkg(
            buttons=[
                {"label": "运行演示", "action": "demo_button", "args": {"kind": "echo"}},
                {"label": "查看结果", "action": "ppt_preview", "view": "ppt_studio"},
            ],
            views=[
                {"nav_title": "PPT 工作台", "view_id": "ppt_studio", "handler": "ppt_enter", "icon": "work"},
                {"nav_title": "二级视图", "view_id": "ppt_studio_2", "handler": "ppt_enter_2"},
            ],
        )
        self.assertEqual([b["action"] for b in s.buttons], ["demo_button", "ppt_preview"])
        self.assertEqual(s.buttons[0]["args"], {"kind": "echo"})
        self.assertEqual(s.buttons[1]["view"], "ppt_studio")
        self.assertEqual(s.views[0]["nav_title"], "PPT 工作台")
        self.assertEqual(s.views[0]["handler"], "ppt_enter")
        # icon 缺省回退默认图标
        self.assertEqual(s.views[1]["icon"], "▣")

    def test_missing_ui_fields_yield_empty_lists(self):
        s = self._pkg()
        self.assertEqual(s.buttons, [])
        self.assertEqual(s.views, [])

    def test_oversight_handler_rejected(self):
        # 越权 action / handler：非受限前缀（system_/file_/backup_/daemon_ 等）一律丢弃
        s = self._pkg(
            buttons=[
                {"label": "重启", "action": "system_restart"},
                {"label": "删库", "action": "file_delete"},
                {"label": "正常", "action": "demo_button"},
            ],
            views=[{"nav_title": "系统", "view_id": "sys", "handler": "system_exec"}],
        )
        self.assertEqual([b["action"] for b in s.buttons], ["demo_button"])
        self.assertEqual(s.views, [])

    def test_platform_admin_methods_blocked(self):
        # 前缀命中但属平台管理员级方法（市场安装/卸载/导入）同样拒绝
        s = self._pkg(
            buttons=[{"label": "装市场", "action": "skill_library_install"},
                     {"label": "导包", "action": "skill_library_import"}],
            views=[{"nav_title": "市场", "view_id": "mkt", "handler": "skill_library_install"}],
        )
        self.assertEqual(s.buttons, [])
        self.assertEqual(s.views, [])

    def test_bad_directive_shape_is_dropped(self):
        # 非法命名（大写 / 连字符 / 空 label / 空 action / 非 dict args）单条丢弃
        s = self._pkg(
            buttons=[
                {"label": "X", "action": "DemoBtn"},      # 大写开头
                {"label": "X", "action": "ppt-preview"},  # 连字符
                {"label": "", "action": "demo_button"},   # 空 label
                {"label": "tag-arg", "action": "demo_button", "args": "not-dict"},
            ],
            views=[
                {"nav_title": "a", "view_id": "Bad-Id", "handler": "ppt_enter"},
                {"nav_title": "", "view_id": "ok_id", "handler": "ppt_enter"},
            ],
        )
        self.assertEqual(s.buttons, [{"label": "tag-arg", "action": "demo_button"}])
        self.assertEqual(s.views, [])

    def test_non_list_ui_fields_safe(self):
        s = self._pkg(
            buttons={"label": "x", "action": "demo_button"},
            views="not-a-list",
        )
        self.assertEqual(s.buttons, [])
        self.assertEqual(s.views, [])


if __name__ == "__main__":
    import unittest
    unittest.main()
