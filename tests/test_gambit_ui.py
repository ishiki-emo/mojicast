"""カウンター（ガンビット）の画面: カードを組んで保存し、字幕で数が増えると
設定画面とオーバーレイの両方に出ること。

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
class GambitUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
        cls.tmp = tempfile.TemporaryDirectory()
        wordstore.DATA = cls.tmp.name
        wordstore.PROFILES_DIR = os.path.join(cls.tmp.name, "profiles")
        wordstore._ready = False
        wordstore.ensure_data()
        app_server._gambit = None
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
        app_server._gambit = None
        cls.tmp.cleanup()

    def setUp(self):
        self.errors = []
        self.page = self.browser.new_page(
            extra_http_headers={"Authorization": "Bearer " + self.server.auth.ui_token},
            viewport={"width": 1280, "height": 860})
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        # どのテストも空の状態から（前のテストのカードを持ち越さない）
        app_server._write_json(app_server._gambits_path(), {"cards": []})
        app_server._gambit = None

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _saved(self):
        with open(wordstore.data_path("gambits.json"), encoding="utf-8") as f:
            return json.load(f)["cards"]

    def test_build_card_count_and_show_on_overlay(self):
        p = self.page
        p.goto(self._url("/ui/gambit"))
        p.wait_for_selector(".empty")
        p.click("#addCard")
        # 書きかけ（言葉なし）でも保存は通る（その行だけ送らない）
        p.wait_for_function("() => document.getElementById('status').textContent.includes('保存しました')")
        self.assertEqual(self._saved()[0]["triggers"], [])

        p.fill(".card .item input[placeholder]", "やばい")
        with p.expect_response(lambda r: r.url.endswith("/api/gambits")
                               and r.request.method == "POST") as res:
            p.press(".card .item input[placeholder]", "Enter")
        self.assertEqual(res.value.status, 200)
        card = self._saved()[0]
        self.assertEqual(card["name"], "やばい")                 # 最初の語がカード名になる
        self.assertEqual(card["triggers"][0]["words"], ["やばい"])

        overlay = self.browser.new_page(viewport={"width": 1280, "height": 720})
        overlay.goto(self._url("/"))
        overlay.wait_for_selector(".wg")

        app_server._engine_on_final("やばいやばい", 1, "")
        p.wait_for_function("() => document.querySelector('.card .val').textContent === '2'")
        overlay.wait_for_function("() => document.querySelector('.wg .v').textContent === '2'")
        self.assertEqual(overlay.text_content(".wg .lb"), "やばい")
        # 名前は数字とは別の大きさ（既定は数字の55%）
        ratio = overlay.evaluate("""() => parseFloat(getComputedStyle(document.querySelector('.wg .lb')).fontSize)
                                        / parseFloat(getComputedStyle(document.querySelector('.wg .v')).fontSize)""")
        self.assertAlmostEqual(ratio, 0.55, places=2)

        # ?widgets=0 のオーバーレイには出さない
        plain = self.browser.new_page()
        plain.goto(self._url("/?widgets=0"))
        plain.wait_for_timeout(500)
        self.assertEqual(plain.query_selector_all(".wg"), [])

        # 手直し: −で1減る
        p.click(".card .chead button[title='1減らす']")
        overlay.wait_for_function("() => document.querySelector('.wg .v').textContent === '1'")
        overlay.close(); plain.close()

    def test_link_needs_a_card_below_and_moving_up_drops_it(self):
        p = self.page
        p.goto(self._url("/ui/gambit"))
        p.wait_for_selector(".empty")
        p.click("#addCard")
        p.click("#addCard")
        p.locator(".card").nth(0).locator("button.add", has_text="つなぐ").click()
        p.wait_for_function("() => document.getElementById('status').textContent.includes('保存しました')")
        cards = self._saved()
        self.assertEqual(cards[0]["links"][0]["target"], cards[1]["id"])
        # 下のカードを上へ動かすと、つなぎは外れて、外したことが画面に出る
        with p.expect_response(lambda r: r.url.endswith("/api/gambits")
                               and r.request.method == "POST"):
            p.locator(".card").nth(1).locator("button[title='上へ']").click()
        self.assertIn("外しました", p.text_content("#status"))
        self.assertTrue(all(not c["links"] for c in self._saved()))

    def test_add_silence_trigger(self):
        p = self.page
        p.goto(self._url("/ui/gambit"))
        p.wait_for_selector(".empty")
        p.click("#addCard")
        p.wait_for_function("() => document.getElementById('status').textContent.includes('保存しました')")
        with p.expect_response(lambda r: r.url.endswith("/api/gambits")
                               and r.request.method == "POST"):
            p.locator(".card button.add", has_text="黙っている").click()
        self.assertEqual(self._saved()[0]["triggers"],
                         [{"type": "silence", "speaker": "any", "seconds": 30}])
        row = p.locator(".card .item", has_text="黙っている")
        with p.expect_response(lambda r: r.url.endswith("/api/gambits")
                               and r.request.method == "POST"):
            row.locator("select").nth(1).select_option("120")
        self.assertEqual(self._saved()[0]["triggers"][0]["seconds"], 120)
        self.assertIn("2分", row.locator("select").nth(1).inner_text())
        if os.environ.get("GB_SHOT"):
            p.screenshot(path=os.environ["GB_SHOT"])


if __name__ == "__main__":
    unittest.main()
