"""コックピットの字幕モニタ: 既定は配信プレビュー・前回の表示方法を次回の起動でも使うこと

GUI窓（pywebview）はプライベートモードで、localStorage が起動ごとに消える。
そのため記憶は config に持つ。テストでは毎回まっさらなブラウザ文脈で開き直し、
「アプリを終了して起動し直した」状態を再現する。

前提: pip install playwright && python -m playwright install chromium
"""
import os
import tempfile
import threading
import unittest
from unittest import mock

try:
    from playwright.sync_api import sync_playwright
except ImportError:                       # pragma: no cover
    sync_playwright = None

import app_server
import wordstore


@unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
class CockpitMonitorModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
        cls.server = app_server._QuietHTTPServer(("127.0.0.1", 0), app_server.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:            # pragma: no cover
            cls.pw.stop()
            raise unittest.SkipTest(f"chromium 未導入: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready = cls._orig

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        wordstore.DATA = self.tmp.name
        wordstore.PROFILES_DIR = os.path.join(self.tmp.name, "profiles")
        wordstore._ready = False
        wordstore.ensure_data()

    def tearDown(self):
        self.tmp.cleanup()

    def _launch(self):
        """起動し直しに相当（新しい文脈＝localStorage も空）"""
        ctx = self.browser.new_context(viewport={"width": 1160, "height": 780})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/ui/cockpit")
        page.wait_for_function("() => document.getElementById('device').options.length > 0")
        return ctx, page

    def _mode(self, page):
        return "preview" if "active" in page.get_attribute("#modePreview", "class") else "log"

    def test_default_is_stream_preview(self):
        ctx, page = self._launch()
        self.assertEqual(self._mode(page), "preview")
        self.assertTrue(page.is_visible("#previewStage"))
        ctx.close()

    def test_last_mode_is_restored_after_restart(self):
        ctx, page = self._launch()
        page.click("#modeLog")
        page.wait_for_function("() => true")
        for _ in range(30):
            if app_server.load_config().get("monitor_mode") == "log":
                break
            page.wait_for_timeout(100)
        self.assertEqual(app_server.load_config().get("monitor_mode"), "log")
        ctx.close()

        ctx, page = self._launch()
        page.wait_for_function("() => document.getElementById('modeLog').classList.contains('active')")
        self.assertTrue(page.is_visible("#monitor"))
        page.click("#modePreview")
        ctx.close()
        ctx, page = self._launch()
        page.wait_for_function("() => document.getElementById('modePreview').classList.contains('active')")
        ctx.close()

    def test_dismissed_update_stays_hidden_after_restart(self):
        info = {"update_available": True, "latest": "9.9.9", "url": "", "notes": ""}
        with mock.patch.object(app_server, "_check_update", return_value=info):
            ctx, page = self._launch()
            page.wait_for_selector("#updateBar", state="visible")
            page.click("#updateBar .ub-x")
            for _ in range(30):
                if app_server.load_config().get("update_dismissed") == "9.9.9":
                    break
                page.wait_for_timeout(100)
            self.assertEqual(app_server.load_config().get("update_dismissed"), "9.9.9")
            ctx.close()
            ctx, page = self._launch()
            page.wait_for_timeout(500)
            self.assertFalse(page.is_visible("#updateBar"))
            ctx.close()
        # さらに新しい版が出たら、また知らせる
        info = dict(info, latest="10.0.0")
        with mock.patch.object(app_server, "_check_update", return_value=info):
            ctx, page = self._launch()
            page.wait_for_selector("#updateBar", state="visible")
            ctx.close()

    def test_guide_shows_on_first_launch_only(self):
        ctx, page = self._launch()
        page.wait_for_selector("#guide", state="visible")   # 配信プレビューでも案内は出る
        page.click("#guide >> text=閉じる")
        for _ in range(30):
            if app_server.load_config().get("guide_dismissed"):
                break
            page.wait_for_timeout(100)
        self.assertTrue(app_server.load_config().get("guide_dismissed"))
        ctx.close()
        ctx, page = self._launch()
        page.wait_for_timeout(500)
        self.assertFalse(page.is_visible("#guide"))
        ctx.close()


if __name__ == "__main__":
    unittest.main()
