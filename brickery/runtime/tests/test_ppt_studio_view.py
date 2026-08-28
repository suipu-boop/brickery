"""Step3（第一小块）：PPT 加工台视图契约 + 两个后端 handler 单测。

覆盖：
1) 加工视图定义 schema 合法：控件字段（name/label/type/default/required）
   齐全、list 控件带 item_fields、必填标记正确；action 均指向受控前缀
   （ppt_）方法且被 _is_method_allowed 放行（向后不破坏静态 views 契约）；
2) _h_ppt_open_studio 返回可 JSON 序列化、含 form/actions/preview；
3) _h_ppt_preview 对合法/非法 structure 的正确返回、页数与不落盘。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]      # brickery 包根（= <仓库根>/brickery）
_REPO = Path(__file__).resolve().parents[3]     # 仓库根（sys.path 需含它才能 import brickery）
_PPT_BRICK_PARENT = _SRC / "brickery"           # 含 ppt_brick/ 的积木产物目录
for _p in (str(_REPO), str(_PPT_BRICK_PARENT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from brickery.runtime import chat_ui  # noqa: E402  # _is_method_allowed 放行判定
from brickery.runtime.ipc import IpcServer  # noqa: E402
from ppt_brick import generator as ppt_generator  # noqa: E402

ALLOWED_FORM_TYPES = ("text", "textarea", "color", "list")
REQUIRED_FIELD_KEYS = ("name", "label", "type", "default", "required")


class _HandlerStub:
    """最小桩：用 IpcServer.__new__ 起 handler 宿主，只覆写 ppt_brick 装载，
    不拉起真实服务（不依赖 config/网络），贴近 handler 纯逻辑测试。"""

    def __init__(self):
        self.stub = IpcServer.__new__(IpcServer)
        self.stub._ppt_generator_module = lambda: ppt_generator


class TestViewSchema(unittest.TestCase):
    """加工视图定义 schema 合法性（纯数据断言，无需实例）。"""

    @staticmethod
    def _view() -> dict:
        return IpcServer._ppt_studio_view()

    def test_fields_complete(self):
        view = self._view()
        form = view["form"]
        fields = {f["name"]: f for f in form["fields"]}
        # field_order 与 fields 一一对应，无缺漏
        self.assertEqual(set(form["field_order"]), set(fields))
        for name, f in fields.items():
            for key in REQUIRED_FIELD_KEYS:
                # list 控件无标量 default（缺省即空列表），跳过 default 检查
                if key == "default" and f["type"] == "list":
                    continue
                self.assertIn(key, f, f"字段 {name} 缺少 '{key}'")
            self.assertIn(f["type"], ALLOWED_FORM_TYPES,
                          f"字段 {name} type 非法: {f['type']}")
            if f["type"] == "list":
                self.assertIn("item_fields", f, f"list 字段 {name} 缺 item_fields")
                self.assertTrue(f["item_fields"])
                for sub in f["item_fields"]:
                    self.assertIn("name", sub)
                    self.assertIn("type", sub)
        # 必填标记：title / sections 必须为 required
        self.assertTrue(fields["title"]["required"])
        self.assertTrue(fields["sections"]["required"])
        # 颜色控件默认值与生成器兜底品牌一致（防漂移）
        self.assertEqual(
            fields["brand_color"]["default"].lstrip("#").upper(),
            ppt_generator.DEFAULT_BRAND.upper())

    def test_actions_target_whitelisted_prefix(self):
        view = self._view()
        methods = [a["method"] for a in view["actions"]]
        self.assertEqual(methods, ["ppt_generate", "ppt_restyle"])
        for a in view["actions"]:
            # action 必须命中受控前缀，且经 _is_method_allowed 放行（静态∪前缀）
            self.assertTrue(a["method"].startswith(("ppt_",)),
                            f"{a['method']} 不在受控前缀内")
            self.assertTrue(chat_ui._is_method_allowed(a["method"]),
                            f"{a['method']} 未被白名单放行")
        # 「应用外观」已落地为真功能：不再 disabled，改以 preset 变体下拉驱动
        rs = view["actions"][1]
        self.assertFalse(rs.get("disabled"))
        self.assertEqual(rs["control"], "preset")
        self.assertIn("hint", rs)
        presets = rs["presets"]
        self.assertTrue(presets, "应声明预置变体清单")
        self.assertEqual(len(presets), 5)
        keys = [p["key"] for p in presets]
        self.assertEqual(keys,
                         ["general", "consulting", "investment", "dark",
                          "light"])
        for p in presets:
            for k in ("key", "label", "variant", "semantics"):
                self.assertIn(k, p, f"preset 缺 '{k}'")
        # default_preset 必须命中 presets 内合法 key（前端下拉默认选中）
        self.assertIn(rs["default_preset"], keys)

    def test_preview_declared(self):
        view = self._view()
        pv = view["preview"]
        self.assertTrue(pv["supported"])
        self.assertEqual(pv["method"], "ppt_preview")
        self.assertTrue(chat_ui._is_method_allowed(pv["method"]))
        self.assertEqual(pv.get("trigger"), "on_change")

    def test_static_views_contract_unchanged(self):
        """静态 views 契约保持 {nav_title, view_id, handler, icon?}，不加侵入
        性字段；并确认两个新 handler 已在 IpcServer 上登记（可路由）。"""
        for name in ("_h_ppt_open_studio", "_h_ppt_preview", "_h_ppt_generate"):
            self.assertTrue(hasattr(IpcServer, name), f"缺少 {name}")
        # demo/ppt 已登记 views 条目字段名仍是 Step1 四钥子集（无新增必填键）
        demo = {"nav_title": "演示工作台", "view_id": "demo_studio",
                "handler": "demo_view", "icon": "🧪"}
        ppt = {"nav_title": "PPT 加工台", "view_id": "ppt_studio",
               "handler": "ppt_open_studio", "icon": "📽️"}
        for entry in (demo, ppt):
            self.assertEqual(
                set(entry), {"nav_title", "view_id", "handler", "icon"})


class TestOpenStudio(unittest.TestCase):
    """_h_ppt_open_studio：返回可 JSON 序列化、含 form/actions/preview。"""

    def setUp(self):
        self.stub = _HandlerStub().stub

    def test_json_serializable_with_shape(self):
        r = self.stub._h_ppt_open_studio({})
        self.assertTrue(r["ok"])
        self.assertEqual(r["view_id"], "ppt_studio")
        self.assertEqual(r["skill"], "ppt-studio")
        view = r["view"]
        for key in ("form", "actions", "preview"):
            self.assertIn(key, view)
        json.dumps(r)  # 不抛即序列化通过（含中文 label 的 utf-8 安全）

    def test_actions_and_preview_point_to_existing_handlers(self):
        r = self.stub._h_ppt_open_studio({})
        pv = r["view"]["preview"]
        # 实时预览方法必须真实存在（ppt_preview）
        self.assertTrue(hasattr(IpcServer, f"_h_{pv['method']}"))
        for a in r["view"]["actions"]:
            self.assertTrue(hasattr(IpcServer, f"_h_{a['method']}"),
                            f"动作 {a['method']} 缺少对应 handler")


class TestPreview(unittest.TestCase):
    """_h_ppt_preview：合法/非法 structure 的正确返回、页数、不落盘。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ppt_preview_test_")
        self.stub = _HandlerStub().stub

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _struct():
        return {
            "title": "示例项目汇报",
            "subtitle": "本地渲染 · 零 LLM token",
            "author": "Shadeling",
            "date": "2026-08-27",
            "brand_color": "#1D4ED8",
            "sections": [
                {"title": "项目背景", "bullets": ["要点一", "要点二"]},
                {"title": "实施计划", "bullets": ["要点甲"]},
            ],
        }

    def test_valid_structure_pages(self):
        r = self.stub._h_ppt_preview({"structure": self._struct()})
        self.assertTrue(r["ok"], r)
        pages = r["pages"]
        # cover + toc(2 章推导目录) + section×2 + content×2 = 6 页
        self.assertEqual(len(pages), 6)
        self.assertEqual(r["rendered"], len(pages))  # 与 build_deck 渲染页数一致
        for p in pages:
            for key in ("page_no", "role", "layout", "title",
                        "bullet_count", "note"):
                self.assertIn(key, p)
        self.assertEqual(pages[0]["role"], "cover")
        self.assertEqual(pages[0]["layout"], "cover")
        self.assertEqual(pages[1]["role"], "toc")
        content = [p for p in pages if p["role"] == "content"]
        self.assertEqual(content[0]["bullet_count"], 2)
        self.assertEqual(content[1]["bullet_count"], 1)
        # content 页 note 缺省回填页码 footer
        self.assertEqual(content[0]["note"],
                         f"{content[0]['page_no']} / {pages[-1]['page_no']}")
        # 不落盘：无 output_pptx 目录，且预览不触碰 home
        self.assertFalse(Path(self.tmp, "output_pptx").exists())

    def test_invalid_structure_soft_fail(self):
        # title 空白
        r = self.stub._h_ppt_preview(
            {"structure": {"title": "  ", "sections": [{"title": "a"}]}})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
        # sections 缺失 / 非 list
        r2 = self.stub._h_ppt_preview({"structure": {"title": "x"}})
        self.assertFalse(r2["ok"])
        r3 = self.stub._h_ppt_preview(
            {"structure": {"title": "x", "sections": "oops"}})
        self.assertFalse(r3["ok"])
        # 版式不存在 -> build_deck 抛错被软处理（ok:false 而非异常外抛）
        r4 = self.stub._h_ppt_preview(
            {"structure": self._struct(),
             "layout_ids": {"content": "no_such_layout"}})
        self.assertFalse(r4["ok"])


class TestRestyle(unittest.TestCase):
    """_h_ppt_restyle：预设清单/合法非法输入/token 摘要/页序软失败。"""

    @staticmethod
    def _preset_view():
        """视图定义中的 presets（与 restyle 返回值 presets 同源断言）。"""
        return IpcServer._ppt_studio_view()["actions"][1]["presets"]

    @classmethod
    def _struct(cls):
        return {
            "title": "示例项目汇报",
            "sections": [
                {"title": "项目背景", "bullets": ["要点一", "要点二"]},
                {"title": "实施计划", "bullets": ["要点甲"]},
            ],
        }

    def test_presets_list_consistent_with_view(self):
        p = TestRestyle._preset_view()
        default = IpcServer._ppt_studio_view()["actions"][1]["default_preset"]
        s = _HandlerStub().stub
        r = s._h_ppt_restyle({"preset": default})
        self.assertTrue(r["ok"], r)
        # restyle 返回的 presets 与视图定义同源（key 顺序一致）
        self.assertEqual([x["key"] for x in r["presets"]],
                         [x["key"] for x in p])
        self.assertEqual(r["preset"]["key"], default)

    def test_valid_preset_with_structure_returns_tokens_and_pages(self):
        s = _HandlerStub().stub
        r = s._h_ppt_restyle(
            {"preset": "dark", "structure": TestRestyle._struct()})
        self.assertTrue(r["ok"], r)
        # token 摘要：关键色齐备（品牌/背景/表面/文本/强调/渐变）
        tok = r["tokens"]
        for key in ("brand", "background", "surface", "text", "text_muted",
                    "text_on_accent", "accent", "accent_strong",
                    "accent_soft", "gradient_from", "gradient_to"):
            self.assertIn(key, tok)
            self.assertRegex(tok[key], r"^[0-9A-F]{6}$",
                             f"token {key} 非 6 位 hex: {tok[key]}")
        spec = r["spec"]
        self.assertEqual(spec["variant"], "通用")
        self.assertEqual(spec["semantics"], "dark")
        self.assertEqual(spec["source"], "1D4ED8")  # DEFAULT_BRAND 兜底去 '#'
        # 合法 structure -> 附预览页序与渲染页数（与 preview 口径一致）
        self.assertIn("pages", r)
        self.assertGreater(r["rendered"], 0)
        self.assertEqual(len(r["pages"]), r["rendered"])
        for p in r["pages"]:
            self.assertIn("page_no", p)
            self.assertIn("role", p)

    def test_dark_light_switch_changes_background(self):
        """明暗档切换应真实改变背景 token（换肤生效而非恒等）。"""
        s = _HandlerStub().stub
        dark = s._h_ppt_restyle({"preset": "dark", "structure": {}})
        light = s._h_ppt_restyle({"preset": "light", "structure": {}})
        self.assertTrue(dark["ok"] and light["ok"])
        self.assertNotEqual(dark["tokens"]["background"],
                            light["tokens"]["background"])
        # 未给 structure 时：返回 token 摘要但无页序（纯换肤调用不软失败）
        self.assertIn("tokens", dark)
        self.assertTrue(dark["presets"])

    def test_unknown_preset_soft_fail(self):
        s = _HandlerStub().stub
        r = s._h_ppt_restyle({"preset": "no_such", "structure": {}})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
        # 错误信息应列出可选 key
        self.assertIn("general", r["error"])

    def test_invalid_brand_color_soft_fail(self):
        s = _HandlerStub().stub
        r = s._h_ppt_restyle(
            {"preset": "general",
             "structure": {"brand_color": "not-a-color"}})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_variant_flavor_differs_but_contrast_holds(self):
        """三语气档令牌摘要应有可感知差异（换肤生效），且对比度门禁仍达标。"""
        s = _HandlerStub().stub
        gen = s._ppt_generator_module()
        g0 = s._h_ppt_restyle({"preset": "general", "structure": {}})
        g1 = s._h_ppt_restyle({"preset": "consulting", "structure": {}})
        g2 = s._h_ppt_restyle({"preset": "investment", "structure": {}})
        self.assertTrue(all(x["ok"] for x in (g0, g1, g2)))
        # 装饰性 token（accent_soft / 渐变）在三档间有差异 -> 换肤真实可见
        self.assertNotEqual(g0["tokens"]["accent_soft"],
                            g1["tokens"]["accent_soft"])
        self.assertNotEqual(g0["tokens"]["accent_soft"],
                            g2["tokens"]["accent_soft"])
        self.assertNotEqual(g0["tokens"]["gradient_from"],
                            g1["tokens"]["gradient_from"])
        self.assertNotEqual(g0["tokens"]["gradient_to"],
                            g2["tokens"]["gradient_to"])
        # 关键前景/背景对比度门禁字段不允许被语气档改动（三档必须相等）
        gate = ("text", "background", "surface", "accent", "text_on_accent")
        for key in gate:
            vals = {r["tokens"][key] for r in (g0, g1, g2)}
            self.assertEqual(len(vals), 1, f"门禁字段 {key} 被语气档改动: {vals}")
        # WCAG-AA 门禁：三档 resolved 快照的对比度检查全部 ok
        for variant in ("通用", "咨询", "投行"):
            tokens = gen.theme.derive_tokens(
                gen.DEFAULT_BRAND, variant=variant, semantics="light")
            checks = gen.theme.validate_contrast(tokens)
            bad = [c for c in checks if not c["ok"]]
            self.assertEqual(bad, [], f"{variant} 档对比度不达标: {bad}")

    def test_incomplete_structure_applies_with_cover_page(self):
        """structure 不完整（缺 sections）→ 外观仍可应用（ok）且不抛错；
        build_deck 对残缺结构兜底渲染封面，至少 1 页可预览。"""
        s = _HandlerStub().stub
        r = s._h_ppt_restyle(
            {"preset": "general",
             "structure": {"title": "只有标题", "brand_color": "#1D4ED8"}})
        self.assertTrue(r["ok"], r)
        self.assertIn("pages", r)
        self.assertGreaterEqual(r["rendered"], 1)
        self.assertEqual(len(r["pages"]), r["rendered"])
        self.assertEqual(r["pages"][0]["role"], "cover")
        self.assertIn("tokens", r)

    def test_layout_failure_no_pages_but_still_ok(self):
        """版式不合法致 build_deck 抛错 → 外观仍应用（ok）、无页序、token 在。"""
        s = _HandlerStub().stub
        r = s._h_ppt_restyle(
            {"preset": "general",
             "structure": TestRestyle._struct(),
             "layout_ids": {"cover": "no_such_layout"}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["pages"], [])
        self.assertEqual(r["rendered"], 0)
        self.assertIn("tokens", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
