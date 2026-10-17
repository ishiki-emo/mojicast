"""新UI／旧UIの出し分け（ui_variant）

- 旧UIを選ぶと、ui/legacy/ にある画面はそちらを同じURLで配ること。
- 旧UIに無いファイル（共有の fx.js など）は新旧共通の ui/ から配ること。
- 新UI（既定）では ui/legacy/ を見ないこと。
"""
import os
import unittest
from unittest import mock

import app_server

UI = os.path.join(app_server.BASE, "ui")


class UiFileTests(unittest.TestCase):
    def _resolve(self, variant, name):
        cfg = dict(app_server.DEFAULT_CONFIG, ui_variant=variant)
        with mock.patch.object(app_server, "load_config", return_value=cfg):
            return app_server._ui_file(name)

    def test_default_is_new(self):
        self.assertEqual(app_server.DEFAULT_CONFIG["ui_variant"], "new")

    def test_new_serves_current_ui(self):
        self.assertEqual(self._resolve("new", "cockpit.html"),
                         os.path.join(UI, "cockpit.html"))

    def test_legacy_serves_snapshot(self):
        self.assertEqual(self._resolve("legacy", "cockpit.html"),
                         os.path.join(UI, "legacy", "cockpit.html"))

    def test_legacy_falls_back_to_shared_files(self):
        self.assertEqual(self._resolve("legacy", "fx.js"), os.path.join(UI, "fx.js"))

    def test_every_legacy_page_can_return_to_new_ui(self):
        # 旧UIのコックピットから新UIへ戻る導線が消えていないこと
        with open(os.path.join(UI, "legacy", "cockpit.html"), encoding="utf-8") as f:
            self.assertIn("switchUiVariant('new')", f.read())
        with open(os.path.join(UI, "cockpit.html"), encoding="utf-8") as f:
            self.assertIn("switchUiVariant('legacy')", f.read())


if __name__ == "__main__":
    unittest.main()
