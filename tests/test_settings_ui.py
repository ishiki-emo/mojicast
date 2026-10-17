"""アプリ設定の入力メーター／保存ボタン式の画面の「未保存」の目印

- アプリ設定のマイク欄でも入力レベルが動くこと（コックピットへ行かずに確かめられる）。
  認識中だけ動き、コラボ相手の音声レベルは映さない。
- デザイン・レイアウトと単語の画面は保存ボタン式。未保存のあいだだけ目印が出ること。

前提: pip install playwright && python -m playwright install chromium
"""
import os
import tempfile
import threading
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:                       # pragma: no cover
    sync_playwright = None

import app_server
import wordstore


@unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
class SettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
        cls.tmp = tempfile.TemporaryDirectory()
        wordstore.DATA = cls.tmp.name
        wordstore.PROFILES_DIR = os.path.join(cls.tmp.name, "profiles")
        wordstore._ready = False
        wordstore.ensure_data()
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
        cls.tmp.cleanup()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1100, "height": 800})

    def tearDown(self):
        self.page.close()

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _meter_width(self):
        return self.page.eval_on_selector("#meterBar", "e => parseFloat(e.style.width) || 0")

    def test_settings_mic_card_shows_input_level_while_running(self):
        page = self.page
        page.goto(self._url("/ui/settings?section=hearing"))
        page.wait_for_function("() => document.getElementById('device').options.length > 0")
        page.wait_for_timeout(600)                       # SSE の接続待ち
        app_server.broadcast({"type": "state", "state": "running", "detail": ""})
        app_server.broadcast({"type": "level", "value": 0.5, "speaker": ""})
        page.wait_for_function("() => parseFloat(document.getElementById('meterBar').style.width) > 0")
        self.assertAlmostEqual(self._meter_width(), 50, delta=1)
        # コラボ相手のレベルは映さない
        cfg = app_server.load_config()
        cfg.update(collab=True, guest_name="ゲスト")
        app_server.save_config(cfg)
        page.reload()
        page.wait_for_function("() => document.getElementById('device').options.length > 0")
        page.wait_for_timeout(600)
        app_server.broadcast({"type": "state", "state": "running", "detail": ""})
        app_server.broadcast({"type": "level", "value": 0.9, "speaker": "ゲスト"})
        page.wait_for_timeout(300)
        self.assertEqual(self._meter_width(), 0)
        # 停止したら0に戻る
        app_server.broadcast({"type": "level", "value": 0.4, "speaker": ""})
        page.wait_for_function("() => parseFloat(document.getElementById('meterBar').style.width) > 0")
        app_server.broadcast({"type": "state", "state": "stopped", "detail": ""})
        page.wait_for_function("() => document.getElementById('meterBar').style.width === '0%'")
        cfg = app_server.load_config()
        cfg.update(collab=False)
        app_server.save_config(cfg)

    def test_reaction_fx_page_follows_ui_language(self):
        cfg = app_server.load_config()
        cfg["ui_lang"] = "en"
        app_server.save_config(cfg)
        try:
            page = self.page
            # スタジオに埋め込んだとき（言語は親から ?lang と postMessage で届く）
            page.goto(self._url("/ui/studio?pillar=soundfx"))
            page.wait_for_selector("#fSoundfx[src]")
            frame = None
            for _ in range(50):
                frame = page.frame(url=lambda u: "/ui/soundfx_settings" in u)
                if frame is not None:
                    break
                page.wait_for_timeout(100)
            frame.wait_for_function("() => document.querySelector('#groupList .grow')")
            frame.wait_for_function("() => document.body.innerText.includes('Laughter')")
            text = frame.inner_text("body")
            self.assertIn("Test fire", text)
            self.assertIn("Particles", text)
            self.assertNotIn("テスト発火", text)
            self.assertEqual(frame.eval_on_selector("#rEnter", "e => e.options[0].text"), "Pop")
            # 単体で開いたとき（theme-sync が config の言語を当てる）
            page.goto(self._url("/ui/soundfx_settings"))
            page.wait_for_function("() => document.body.innerText.includes('Reaction FX')")
        finally:
            cfg = app_server.load_config()
            cfg["ui_lang"] = "ja"
            app_server.save_config(cfg)

    def test_words_editor_marks_unsaved_until_saved(self):
        page = self.page
        page.goto(self._url("/ui/words"))
        page.wait_for_function("() => !document.getElementById('saveAllBtn').disabled")
        self.assertFalse(page.is_visible("#unsavedMark"))
        page.fill("#hotTable tbody tr:first-child .h-surface", "テスト")
        self.assertTrue(page.is_visible("#unsavedMark"))
        page.click("#saveAllBtn")
        page.wait_for_selector("#unsavedMark", state="hidden")

    def test_layout_editor_marks_unsaved_until_saved(self):
        page = self.page
        page.goto(self._url("/ui/style?tab=box"))
        page.wait_for_selector("#blist .pitem")
        self.assertFalse(page.is_visible("#unsavedMark"))
        page.click("#customizeBoxBtn")
        page.eval_on_selector("#b_x", "e => { e.value = 20; e.dispatchEvent(new Event('input')) }")
        self.assertTrue(page.is_visible("#unsavedMark"))
        page.click("#saveOnlyBtn")
        page.wait_for_selector("#unsavedMark", state="hidden")


if __name__ == "__main__":
    unittest.main()
