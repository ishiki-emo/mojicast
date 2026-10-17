"""レイアウト「吹き出し」（mode="bubble"）: OBS用 overlay での描画

- 最新の発話1つだけを吹き出しで出す。認識途中は薄文字で伸び、確定で同じ吹き出しが
  濃い文字になる。次の発話が来たら新しい吹き出しへ差し替わる。
- 訳は確定した吹き出しの中に併記する。
- 尻尾はアバター位置（tailX/tailY）の方を向く。長い文でも吹き出しは枠からはみ出さない
  （文字を縮め、それでも入らなければ古い側を「…」で省く）。
- 縦書きに対応する。「表示を消す」で消える。
- 同梱の吹き出しレイアウトは、既存の環境にも一度だけ追加される。

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

PHRASE = "えっ、ちょっと待って、いま何が起きたの！？　見間違いじゃないよね、すごすぎない？"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


DEFAULT_BOXES = {b["id"]: b for b in _read(os.path.join(app_server.BASE, "defaults", "boxes.json"))["boxes"]}
STYLE = {p["id"]: p for p in _read(os.path.join(app_server.BASE, "defaults", "presets.json"))["presets"]}["standard"]


class SeedTests(unittest.TestCase):
    def test_bubble_layouts_are_bundled(self):
        for bid in ("bubble", "bubble-tate"):
            self.assertEqual(DEFAULT_BOXES[bid]["mode"], "bubble")
        self.assertEqual(DEFAULT_BOXES["bubble-tate"]["bubbleDir"], "v")

    def test_existing_install_receives_bubble_layouts_once(self):
        orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                wordstore.DATA = tmp
                wordstore.PROFILES_DIR = os.path.join(tmp, "profiles")
                wordstore._ready = False
                wordstore.ensure_data()
                # 吹き出しが無かった頃のデータ（提供済みの印も旧版ぶんだけ）
                path = os.path.join(tmp, "boxes.json")
                boxes = [b for b in _read(path)["boxes"] if b["id"] not in ("bubble", "bubble-tate")]
                _write(path, {"boxes": boxes})
                cfg = app_server.load_config()
                cfg["seeded_styles"] = [s for s in cfg.get("seeded_styles", [])
                                        if "bubble" not in s]
                app_server.save_config(cfg)
                app_server._seed_style_defaults()
                ids = [b["id"] for b in _read(path)["boxes"]]
                self.assertIn("bubble", ids)
                self.assertIn("bubble-tate", ids)
                # 利用者が消したら二度と復活させない
                _write(path, {"boxes": [b for b in _read(path)["boxes"] if b["id"] != "bubble"]})
                app_server._seed_style_defaults()
                ids = [b["id"] for b in _read(path)["boxes"]]
                self.assertNotIn("bubble", ids)
            finally:
                wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready = orig


@unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
class BubbleOverlayTests(unittest.TestCase):
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
        self.page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        self.page.goto(f"http://127.0.0.1:{self.port}/overlay")
        self.page.wait_for_function("() => typeof handle === 'function' && window.FX")

    def tearDown(self):
        self.page.close()

    def _use(self, **over):
        box = dict(DEFAULT_BOXES["bubble"], **over)
        self.page.evaluate("([style, box]) => updatePanes({ style, box, effects: [], hotwords: [] })",
                           [STYLE, box])
        return box

    def _send(self, msg):
        self.page.evaluate("m => handle(m)", msg)

    def _wraps(self):
        return self.page.eval_on_selector_all(".bub-wrap", "els => els.length")

    def test_partial_grows_then_final_then_next_utterance_replaces(self):
        self._use()
        self._send({"type": "partial", "text": "こんば"})
        self.assertEqual(self._wraps(), 1)
        op = self.page.eval_on_selector(".bub-main", "e => e.style.opacity")
        self.assertNotEqual(op, "")                       # 認識途中は薄文字
        self._send({"type": "final", "text": "こんばんは！", "id": 1})
        self.assertEqual(self._wraps(), 1)                # 同じ吹き出しが確定に変わる
        self.assertEqual(self.page.inner_text(".bub-main"), "こんばんは！")
        self.assertEqual(self.page.eval_on_selector(".bub-main", "e => e.style.opacity"), "")
        self._send({"type": "translation", "id": 1, "text": "Good evening!", "order": 0, "lang": "en"})
        self.assertEqual(self.page.inner_text(".bub-tr"), "Good evening!")
        self._send({"type": "partial", "text": "つぎ"})   # 次の発話 → 差し替え（最新の1つだけ）
        self.assertEqual(self._wraps(), 1)
        self.assertEqual(self.page.inner_text(".bub-main"), "つぎ")
        self.assertEqual(self.page.eval_on_selector_all(".bub-tr", "e => e.length"), 0)
        self._send({"type": "clear"})
        self.assertEqual(self._wraps(), 0)

    def _geometry(self):
        return self.page.evaluate("""() => {
          const pane = [...document.querySelectorAll('.pane')].find(p => p.querySelector('.bub-wrap'));
          const box = pane.getBoundingClientRect();
          const fill = pane.querySelector('.bub-svg > g:last-child');
          const body = fill.firstElementChild.getBoundingClientRect();
          const tail = fill.querySelector('path');
          const t = tail ? tail.getBoundingClientRect() : null;
          return { box: [box.left, box.top, box.right, box.bottom],
                   body: [body.left, body.top, body.right, body.bottom],
                   tail: t && [t.left, t.top, t.right, t.bottom] };
        }""")

    def test_tail_points_toward_avatar(self):
        for tx, ty in [(82, 72), (10, 80)]:
            self._use(tailX=tx, tailY=ty)
            self._send({"type": "final", "text": "今日もゆるっと配信していきます", "id": 2})
            g = self._geometry()
            body_cx = (g["body"][0] + g["body"][2]) / 2
            tail_cx = (g["tail"][0] + g["tail"][2]) / 2
            avatar_x = tx / 100 * 1920
            self.assertLess(abs(tail_cx - avatar_x), abs(body_cx - avatar_x))
            self.assertGreater(g["tail"][3], g["body"][3] - 1)   # アバターは下 → 尻尾は下へ出る
            self._send({"type": "clear"})

    def _font(self):
        return self.page.eval_on_selector(".bub-text", "e => parseFloat(e.style.fontSize)")

    def _assert_inside(self):
        g = self._geometry()
        for i in (0, 1):
            self.assertGreaterEqual(g["body"][i], g["box"][i] - 1)
        for i in (2, 3):
            self.assertLessEqual(g["body"][i], g["box"][i] + 1)

    def test_long_text_shrinks_then_trims_but_stays_inside(self):
        self._use()
        self._send({"type": "final", "text": "短い", "id": 3})
        full = self._font()
        # 長め: 文字を縮めて全文を入れる
        self._send({"type": "partial", "text": "x"})
        self._send({"type": "final", "text": PHRASE * 6, "id": 4})
        self._assert_inside()
        self.assertLess(self._font(), full)
        self.assertFalse(self.page.inner_text(".bub-main").startswith("…"))
        # 下限まで縮めても入らない長さ: 古い側を「…」で省く（最新の部分は残す）
        self._send({"type": "partial", "text": "x"})
        self._send({"type": "final", "text": PHRASE * 30 + "最後の言葉", "id": 5})
        self._assert_inside()
        text = self.page.inner_text(".bub-main")
        self.assertTrue(text.startswith("…"))
        self.assertTrue(text.endswith("最後の言葉"))

    def test_vertical_writing(self):
        self._use(**{k: v for k, v in DEFAULT_BOXES["bubble-tate"].items()})
        self._send({"type": "final", "text": "縦書きの吹き出し", "id": 4})
        self.assertEqual(self.page.eval_on_selector(".bub-text", "e => getComputedStyle(e).writingMode"),
                         "vertical-rl")

    def test_switching_mode_away_clears_the_bubble(self):
        self._use()
        self._send({"type": "final", "text": "こんにちは", "id": 5})
        self.assertEqual(self._wraps(), 1)
        self._use(mode="flow")
        self.assertEqual(self._wraps(), 0)


if __name__ == "__main__":
    unittest.main()
