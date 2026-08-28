"""chat_ui IPC 白名单与积木动态前缀放行判定（纯逻辑，不启动 HTTP server）。

修复背景：Step1 最小链路实测时 demo_button 未进 IPC_ALLOWED_METHODS，
POST /api/ipc {"method":"demo_button"} 被 403 拒绝；本测试锁定
_is_method_allowed 的“静态白名单 ∪ 受控动态前缀（仅非流式）”语义，
防止动态前缀回归漏放 / 误放危险方法。
"""
import unittest

from brickery.runtime import chat_ui as ui


class MethodAllowTest(unittest.TestCase):
    def test_static_whitelist_allowed(self):
        for m in ("chat", "skill_list", "skill_views", "doctor"):
            self.assertTrue(ui._is_method_allowed(m), m)
        # 静态白名单方法流式/非流式均放行
        self.assertTrue(ui._is_method_allowed("skill_views", stream=True))
        self.assertTrue(ui._is_method_allowed("skill_views", stream=False))

    def test_dynamic_prefix_allowed_non_stream(self):
        # Step1 最小链路缺口：demo_button 须进入放行判定
        self.assertTrue(ui._is_method_allowed("demo_button"))
        self.assertTrue(ui._is_method_allowed("ppt_generate"))
        self.assertTrue(ui._is_method_allowed("ppt_open_studio"))

    def test_dynamic_prefix_blocked_on_stream(self):
        # 流式通道仅限静态白名单，动态前缀不许走 SSE
        self.assertFalse(ui._is_method_allowed("demo_button", stream=True))
        self.assertFalse(ui._is_method_allowed("ppt_generate", stream=True))

    def test_dangerous_methods_rejected(self):
        # 非受控前缀、不在静态白名单的危险方法一律拒绝
        for m in ("system_restart", "file_delete", "backup_export_system",
                  "exec", "__init__", ""):
            self.assertFalse(ui._is_method_allowed(m), m)
            self.assertFalse(ui._is_method_allowed(m, stream=True), m)

    def test_stream_flag_keeps_static_boundary(self):
        # 动态前缀仅在非流式放行，是刻意不匹配前缀的白名单外的最后一道闸
        self.assertTrue(ui._is_method_allowed("demo_button", stream=False))
        self.assertFalse(ui._is_method_allowed("demo_button", stream=True))

    def test_backup_restore_buttons_allowed(self):
        # 遗留待办第 2 项处置：backup-restore 内置积木 5 按钮全部保留并放行。
        # 根因：按钮字段由 legacy {handler,params} 迁移到 Step1 协议 {action,args}；
        # 方法名本体（backup_*/task_submit）一直在 IPC_ALLOWED_METHODS 静态白名单，
        # 属“保留-静态白名单放行”路径，不可误删、也不新增 backup_ 动态前缀。
        for m in ("backup_default", "backup_export", "backup_restore",
                  "backup_list", "task_submit"):
            self.assertTrue(ui._is_method_allowed(m), m)
            # 静态白名单方法流式通道同样放行（语义与 test_static_whitelist_allowed 一致）
            self.assertTrue(ui._is_method_allowed(m, stream=True), m)
        # skill_views 是按钮卡的传输通道，必须放行（前端靠它拿 buttons）
        self.assertTrue(ui._is_method_allowed("skill_views"))

    def test_legacy_backup_names_not_allowed(self):
        # 任务假设名从未在代码中注册过（git 全历史 + 全盘检索为空），
        # 也无 backup_scheduled 之外的按钮 id：它们不参与任何放行，锁死防复活。
        # 注意 backup_scheduled 只是按钮 id（其 action 为 task_submit），
        # 本身不作为 IPC method 放行。
        for m in ("backup_state", "backup_skills", "restore_state",
                  "restore_scheduled", "backup_scheduled"):
            self.assertFalse(ui._is_method_allowed(m), m)
            self.assertFalse(ui._is_method_allowed(m, stream=True), m)


if __name__ == "__main__":
    unittest.main()
