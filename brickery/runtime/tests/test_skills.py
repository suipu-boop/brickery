"""§3 技能注册与筛选单测。"""
from brickery.runtime.skills import Skill, SkillRegistry
from .base import RuntimeTestCase


class TestSkills(RuntimeTestCase):
    def _reg(self):
        r = SkillRegistry()
        r.register_many([
            Skill("code", trigger=["代码", "编程", "python"], content="代码提示"),
            Skill("write", trigger=["写作", "文章", "论文"], content="写作提示"),
            Skill("off", trigger=["代码"], disabled=True, content="应被禁用"),
        ])
        return r

    def test_match_relevant(self):
        r = self._reg()
        hits = r.match("写一段 Python 代码")
        names = [s.name for s in hits]
        self.assertIn("code", names)
        self.assertNotIn("write", names)

    def test_disabled_not_matched(self):
        r = self._reg()
        hits = r.match("代码")
        names = [s.name for s in hits]
        self.assertIn("code", names)
        self.assertNotIn("off", names)  # disabled 即便 trigger 匹配也不命中

    def test_no_match_when_empty(self):
        self.assertEqual(SkillRegistry().match("任何"), [])
