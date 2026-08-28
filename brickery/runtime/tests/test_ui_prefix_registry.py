"""Step4 固化：积木 UI 方法前缀「注册表单一事实源」测试。

背景：Step1 起「声明层 D7 白名单」（skill_library.UI_ACTION_PREFIXES = 4 前缀）与
「IPC 放行层」（chat_ui.UI_DYNAMIC_METHOD_PREFIXES）为两份手工维护副本，曾只有
ppt_/demo_ 放行——skill_/tool_ 前缀积木声明校验通过但按钮调用会 403。
Step4 把 IPC 放行层改为从声明层导入（单一事实源），本测试锁定：
  1) chat_ui 与 skill_library 的前缀常量同源（防双副本漂移）；
  2) 注册表内每个前缀的积木动作均被 IPC 放行（= 新积木声明即接入，零额外代码）；
  3) D7 控权 / 静态白名单 / 危险方法 / 流式边界语义不回退。
"""
import unittest

from brickery.runtime import chat_ui as ui
from brickery.runtime import skill_library as sl


class PrefixSingleSourceTest(unittest.TestCase):
    def test_ipc_layer_follows_declaration_layer(self):
        # 单一事实源：IPC 放行前缀必须等于声明层 D7 白名单，防双副本漂移
        self.assertTrue(ui.UI_DYNAMIC_METHOD_PREFIXES)
        self.assertEqual(ui.UI_DYNAMIC_METHOD_PREFIXES, sl.UI_ACTION_PREFIXES)
        # 声明层必须仍覆盖 Step1 起的能力域 + 验证前缀
        for p in ("ppt_", "skill_", "tool_", "demo_"):
            self.assertIn(p, sl.UI_ACTION_PREFIXES)

    def test_every_registered_prefix_brick_allowed_non_stream(self):
        # 注册表内每个前缀：新积木声明该前缀 action 即被 IPC 放行（零额外代码）
        for p in sl.UI_ACTION_PREFIXES:
            m = p + "example_action"
            self.assertTrue(ui._is_method_allowed(m), m)
            # 流式仍拒：积木交互不允许挂 SSE 长连接
            self.assertFalse(ui._is_method_allowed(m, stream=True), m)

    def test_skill_tool_prefix_brick_declaration_passes(self):
        # 实证：声明 skill_/tool_ 前缀按钮，声明层保留且 IPC 放行
        kept = sl._normalize_ui_buttons(
            [{"label": "扫描", "action": "skill_scan"},
             {"label": "跑批", "action": "tool_run"}],
            "t-brick")
        self.assertEqual([b["action"] for b in kept], ["skill_scan", "tool_run"])
        for a in ("skill_scan", "tool_run"):
            self.assertTrue(ui._is_method_allowed(a), a)

    def test_admin_methods_still_rejected_in_declaration(self):
        # D7 控权不回退：虽在注册表内但属平台管理员级的方法，声明层必须丢弃
        kept = sl._normalize_ui_buttons(
            [{"label": "越权安装", "action": "skill_library_install"},
             {"label": "越权开关", "action": "tool_toggle"}],
            "t-brick")
        self.assertEqual(kept, [])

    def test_existing_boundaries_unchanged(self):
        # 既有边界不回退：动态前缀 / 危险方法 / 静态白名单语义不变
        self.assertTrue(ui._is_method_allowed("demo_button"))
        self.assertTrue(ui._is_method_allowed("ppt_generate"))
        self.assertFalse(ui._is_method_allowed("demo_button", stream=True))
        self.assertFalse(ui._is_method_allowed("ppt_generate", stream=True))
        for m in ("system_restart", "file_delete", "backup_export_system", "exec", ""):
            self.assertFalse(ui._is_method_allowed(m), m)
        # backup-* / task_submit 属静态白名单，既有处置不回退
        for m in ("backup_restore", "backup_list", "task_submit"):
            self.assertTrue(ui._is_method_allowed(m), m)


if __name__ == "__main__":
    unittest.main()
