"""Step4 固化：chat_ui 前端「积木 UI 通用链路」静态契约断言。

前端为 chat_ui.py 内嵌的 <script>…</script> JS 块。本测试做两类检查：
  1) 静态节点断言：动态分区 / 视图引擎 / 通用按钮卡 / $form 约定等“声明即接入”
     关键结构存在，防重构误删通用链路（Step3 曾一次性做 18 项断言，本文件固化为
     可重复测试）；
  2) 语法校验：提取 JS 块跑 `node --check`（node 缺失则跳过）。
纯静态读取源文件，不启动 HTTP server、不需要后端。
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CHAT_UI = Path(__file__).resolve().parents[1] / "chat_ui.py"  # runtime/tests -> runtime


def _js_block() -> str:
    lines = CHAT_UI.read_text(encoding="utf-8").splitlines()
    i0 = next(i for i, l in enumerate(lines) if "<script>" in l)
    i1 = next(i for i in range(i0 + 1, len(lines)) if "</script>" in lines[i])
    return "\n".join(lines[i0 + 1:i1])


class UiBrickContractTest(unittest.TestCase):
    js = _js_block()

    # —— 动态分区（views 快照驱动，启用注册/禁用卸载移除）——
    def test_dynamic_nav_zone_present(self):
        self.assertIn("function loadDynamicViews", self.js)
        self.assertIn("skill_views", self.js)          # 视图快照来源
        self.assertIn("nav-dyn-group", self.js)        # 动态分区容器
        self.assertIn("工具 / 工作台", self.js)         # 分区名

    def test_view_engine_route_and_fallback(self):
        # renderGenericView 是通用路由：走引擎，失败回退静态容器
        self.assertIn("function renderGenericView", self.js)
        self.assertIn("renderDynamicViewEngine", self.js)
        self.assertIn("renderGenericStaticShell", self.js)

    def test_generic_button_card_link(self):
        # 通用按钮卡：skill 列表 + 静态容器 + 统一回调，全部读 s.buttons 动态渲染
        self.assertIn("brick-btn", self.js)
        self.assertIn("invokeSkillButton", self.js)
        self.assertIn("s.buttons", self.js)

    # —— 可交互视图引擎（运行期 schema：form/actions/preview）——
    def test_view_engine_form_action_preview(self):
        self.assertIn("veRunAction", self.js)
        self.assertIn("veRenderPreview", self.js)
        self.assertIn("$form", self.js)                # 表单聚合占位符
        self.assertIn("data-ve-path", self.js)         # 递归 schema 路径
        self.assertIn("veAddRow", self.js)             # list 控件增行

    # —— 白名单单一来源（Step4）：不许手工硬编码前缀，须从 skill_library 导入 ——
    def test_prefix_single_source_no_manual_tuple(self):
        self.assertNotIn('UI_DYNAMIC_METHOD_PREFIXES = ("ppt_", "demo_")', self.js)
        src = CHAT_UI.read_text(encoding="utf-8")
        self.assertIn(
            "from .skill_library import UI_ACTION_PREFIXES as UI_DYNAMIC_METHOD_PREFIXES",
            src)


@unittest.skipUnless(shutil.which("node"), "node 不可用，跳过 JS 语法校验")
class JsSyntaxTest(unittest.TestCase):
    def test_js_block_passes_node_check(self):
        js = UiBrickContractTest.js
        self.assertTrue(js.strip())
        with tempfile.NamedTemporaryFile("w", suffix=".js",
                                         delete=False, encoding="utf-8") as f:
            f.write(js)
            tmp = f.name
        try:
            r = subprocess.run(["node", "--check", tmp],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            Path(tmp).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
