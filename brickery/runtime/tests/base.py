"""B1 纯数据层单测公共基：隔离运行时目录，避免污染真实 ~/.brickery。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class RuntimeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="brickery_test_"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.models = self.tmp / "models"
        self.models.mkdir()
        # 隔离 BRICKERY_HOME，确保 memory 子系统也落到临时目录
        self._orig_home = os.environ.get("BRICKERY_HOME")
        self._orig_models = os.environ.get("BRICKERY_MODELS")
        os.environ["BRICKERY_HOME"] = str(self.home)
        os.environ["BRICKERY_MODELS"] = str(self.models)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._orig_home is None:
            os.environ.pop("BRICKERY_HOME", None)
        else:
            os.environ["BRICKERY_HOME"] = self._orig_home
        if self._orig_models is None:
            os.environ.pop("BRICKERY_MODELS", None)
        else:
            os.environ["BRICKERY_MODELS"] = self._orig_models
