"""スタジオに埋め込んだ単語画面で単語セットを切り替えたら、外側の選択（＝使用中）も合うこと

背景（v0.9.8 の報告「認識させる言葉を保存したのに、開き直すと消えている」）:
スタジオの「編集する単語セット」と、埋め込んだ単語画面の「編集する単語セット:」が
連動していなかった。内側だけ切り替えて保存すると単語は内側のセットに入るが、
次に開くときは外側の値（使用中のセット）で開くので消えたように見え、認識にも使われない。

前提: pip install playwright && python -m playwright install chromium
"""
import json
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
class WordsProfileSyncTests(unittest.TestCase):
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
        os.makedirs(wordstore.PROFILES_DIR)
        wordstore._ready = True
        self.page = self.browser.new_page(viewport={"width": 1120, "height": 840})

    def tearDown(self):
        self.page.close()
        self.tmp.cleanup()

    def _config(self):
        with open(os.path.join(self.tmp.name, "config.json"), encoding="utf-8") as f:
            return json.load(f)

    def _open_studio_words(self):
        page = self.page
        page.goto(f"http://127.0.0.1:{self.port}/ui/studio?tab=words")
        page.wait_for_selector("#fWords[src]")
        frame = None
        for _ in range(50):
            frame = page.frame(url=lambda u: "/ui/words" in u)
            if frame is not None:
                break
            page.wait_for_timeout(100)
        self.assertIsNotNone(frame, "words の iframe が見つからない")
        frame.wait_for_function("() => !document.getElementById('saveAllBtn').disabled")
        return frame

    def _wait_outer(self, value):
        self.page.wait_for_function(
            "v => document.getElementById('profileScope').value === v", arg=value)

    def test_create_in_embedded_editor_becomes_active_and_survives_reopen(self):
        frame = self._open_studio_words()
        frame.click("text=＋ 新しい単語セット")
        frame.fill("#newProfName", "ゲーム")
        frame.click("#newProfBar >> text=作成")
        frame.wait_for_function("() => document.getElementById('profileSel').value === 'ゲーム'")
        self._wait_outer("ゲーム")
        self.assertEqual(self._config().get("word_profile"), "ゲーム")

        frame.wait_for_function("() => !document.getElementById('saveAllBtn').disabled")
        frame.fill("#hotTable tbody tr:first-child .h-surface", "癒色えも")
        frame.fill("#hotTable tbody tr:first-child .h-reading", "いしきえも")
        frame.click("#saveAllBtn")
        frame.wait_for_function("() => document.getElementById('saved').style.opacity === '1'")
        self.assertEqual([e["surface"] for e in wordstore.load_hotwords("ゲーム")], ["癒色えも"])

        # 開き直しても保存した単語が見える
        frame = self._open_studio_words()
        self.assertEqual(frame.eval_on_selector("#profileSel", "e => e.value"), "ゲーム")
        self.assertEqual(frame.eval_on_selector(
            "#hotTable tbody tr:first-child .h-surface", "e => e.value"), "癒色えも")
        self.assertTrue(frame.eval_on_selector("#inactiveHint", "e => e.hidden"))

    def test_switch_in_embedded_editor_updates_outer_selector(self):
        wordstore.create_profile("雑談")
        frame = self._open_studio_words()
        self._wait_outer("")
        frame.select_option("#profileSel", "雑談")
        self._wait_outer("雑談")
        self.assertEqual(self._config().get("word_profile"), "雑談")
        frame.select_option("#profileSel", "")
        self._wait_outer("")
        self.assertEqual(self._config().get("word_profile"), "")

    def test_standalone_editor_warns_when_editing_unused_set(self):
        wordstore.create_profile("雑談")
        page = self.page
        page.goto(f"http://127.0.0.1:{self.port}/ui/words")
        page.wait_for_function("() => !document.getElementById('saveAllBtn').disabled")
        self.assertTrue(page.eval_on_selector("#inactiveHint", "e => e.hidden"))
        page.select_option("#profileSel", "雑談")
        page.wait_for_function("() => !document.getElementById('inactiveHint').hidden")
        # 単体の画面では使用中のセットを勝手に変えない
        self.assertNotEqual(
            app_server.load_config().get("word_profile"), "雑談")


if __name__ == "__main__":
    unittest.main()
