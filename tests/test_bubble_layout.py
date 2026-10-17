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
        # サーバからの style/init（実際の設定＝通常表示のレイアウト）が後から届くと、
        # 表示のしかたが変わったとして吹き出しが消える（本番では正しい動き）。テストでは
        # 吹き出しのレイアウトを自分で当てるので、SSE は止めておく
        self.page.route("**/events", lambda route: route.abort())
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

    def test_text_alignment_follows_writing_direction(self):
        def lines_left_edges():
            return self.page.evaluate("""() => {
              const r = document.createRange(); r.selectNodeContents(document.querySelector('.bub-main'));
              return [...r.getClientRects()].map(x => Math.round(x.left));
            }""")
        # 横書き: 左詰めなら各行の左端がそろい、中央なら短い最終行が右へずれる
        self._use(bubbleAlign="start")
        self._send({"type": "final", "text": PHRASE * 2 + "おわり", "id": 6})
        self.assertEqual(self.page.eval_on_selector(".bub-text", "e => e.style.textAlign"), "start")
        lefts = lines_left_edges()
        self.assertGreater(len(lefts), 1)
        self.assertLessEqual(max(lefts) - min(lefts), 2)
        self._use(bubbleAlign="center")
        self._send({"type": "partial", "text": "x"})
        self._send({"type": "final", "text": PHRASE * 2 + "おわり", "id": 7})
        self.assertGreater(max(lines_left_edges()) - min(lines_left_edges()), 10)
        # 縦書き: start は上詰め（論理値なので向きを変えても同じ値で引き継ぐ）
        self._use(**dict(DEFAULT_BOXES["bubble-tate"], bubbleAlign="start"))
        self._send({"type": "partial", "text": "x"})
        self._send({"type": "final", "text": PHRASE, "id": 8})
        self.assertEqual(self.page.eval_on_selector(".bub-text", "e => getComputedStyle(e).textAlign"), "start")

    def _bubbles(self):
        """新しい順に [{z, opacity, top, bottom, left, right, tail}]（top 等は本体の外接矩形）"""
        self.page.wait_for_timeout(400)   # 押し出された古い吹き出しの移動アニメ（0.28秒）を待つ
        return self.page.evaluate("""() => {
          const pane = [...document.querySelectorAll('.pane')].find(p => p.querySelector('.bub-wrap'));
          return [...pane.querySelectorAll('.bub-wrap')].map(w => {
            const fill = w.querySelector('.bub-svg > g:last-child');
            const r = fill.firstElementChild.getBoundingClientRect();
            const t = w.querySelector('.bub-text');
            return { z: +w.style.zIndex, opacity: +(t.style.opacity || 1), text: w.innerText,
                     top: r.top, bottom: r.bottom, left: r.left, right: r.right,
                     tail: !!fill.querySelector('path, circle + circle') };
          }).sort((a, b) => b.z - a.z);
        }""")

    def test_default_keeps_only_the_latest_bubble(self):
        self._use()
        for i, t in enumerate(["ひとつめ", "ふたつめ", "みっつめ"]):
            self._send({"type": "final", "text": t, "id": 20 + i})
        self.assertEqual(self._wraps(), 1)
        self.assertEqual(self.page.inner_text(".bub-main"), "みっつめ")

    def test_history_stacks_older_bubbles_behind_and_away_from_avatar(self):
        self._use(bubbleHistory=3)   # アバターは右下（tailY=72）→ 古いものは上へ積む
        for i, t in enumerate(["いちばん古い", "まんなか", "ふたつ前", "最新の発話"]):
            self._send({"type": "final", "text": t, "id": 30 + i})
        b = self._bubbles()
        self.assertEqual(len(b), 3)                         # 表示する数を超えた古いものは消える
        self.assertIn("最新の発話", b[0]["text"])
        self.assertIn("まんなか", b[2]["text"])
        self.assertTrue(b[0]["tail"])                       # 尻尾は最新だけ
        self.assertFalse(b[1]["tail"])
        self.assertGreater(b[0]["opacity"], b[1]["opacity"])   # 奥ほど文字が薄い
        self.assertLess(b[1]["top"], b[0]["top"])           # 古いものはアバターと反対側（上）
        self.assertLess(b[2]["top"], b[1]["top"])
        self.assertLess(b[1]["bottom"], b[0]["bottom"])
        self.assertGreater(b[1]["bottom"], b[0]["top"])     # 少し重なる（手前が新しい）
        # 訳は、まだ出ている古い吹き出しにも付けられる
        self._send({"type": "translation", "id": 32, "text": "two ago", "order": 0, "lang": "en"})
        self.assertIn("two ago", self._bubbles()[1]["text"])
        # 表示する数を減らすと、その場で古いものから消える
        self._use(bubbleHistory=1)
        self.assertEqual(self._wraps(), 1)
        self.assertIn("最新の発話", self.page.inner_text(".bub-wrap"))

    def test_vertical_history_lines_up_sideways(self):
        self._use(**dict(DEFAULT_BOXES["bubble-tate"], bubbleHistory=2, w=40))
        for i, t in enumerate(["前の発話", "今の発話"]):
            self._send({"type": "final", "text": t, "id": 40 + i})
        b = self._bubbles()
        self.assertEqual(len(b), 2)
        self.assertLess(b[1]["left"], b[0]["left"])         # アバターは右 → 古いものは左へ

    def _scatter_state(self):
        self.page.wait_for_timeout(300)
        return self.page.evaluate("""() => {
          const pane = [...document.querySelectorAll('.pane')].find(p => p.querySelector('.bub-wrap'));
          return [...pane.querySelectorAll('.bub-wrap')].map(w => {
            const fill = w.querySelector('.bub-svg > g:last-child');
            const r = fill.firstElementChild.getBoundingClientRect();
            return { z: +w.style.zIndex, opacity: +(w.style.opacity || 1), text: w.innerText,
                     rect: [r.left, r.top, r.right, r.bottom], tail: !!fill.querySelector('path') };
          }).sort((a, b) => b.z - a.z);
        }""")

    def test_scatter_places_bubbles_on_free_points_without_overlap(self):
        spots = [{"x": 15, "y": 20}, {"x": 50, "y": 20}, {"x": 85, "y": 20}]
        self._use(bubbleArrange="scatter", bubbleSpots=spots, bubbleHistory=3,
                  bubbleSpotW=26, bubbleSpotH=24, tailX=50, tailY=85)
        for i, t in enumerate(["ひとつめ", "ふたつめ", "みっつめ"]):
            self._send({"type": "final", "text": t, "id": 50 + i})
        b = self._scatter_state()
        self.assertEqual(len(b), 3)
        for i in range(3):                                    # 重ならない
            for j in range(i + 1, 3):
                r1, r2 = b[i]["rect"], b[j]["rect"]
                self.assertFalse(r1[0] < r2[2] and r2[0] < r1[2] and r1[1] < r2[3] and r2[1] < r1[3])
        self.assertTrue(all(x["tail"] for x in b))            # 尻尾は全部に
        self.assertGreater(b[0]["opacity"], b[1]["opacity"])  # 古いほど薄い
        self.assertGreater(b[1]["opacity"], b[2]["opacity"])
        centers = sorted(round((x["rect"][0] + x["rect"][2]) / 2 / 1920 * 100) for x in b)
        self.assertEqual(centers, [15, 50, 85])               # 各ポイントに1つずつ
        # 4つ目: 表示する数を超えたいちばん古いものが消え、その場所を使う（ほかは動かない）
        before = {x["text"]: x["rect"] for x in b}
        self._send({"type": "final", "text": "よっつめ", "id": 53})
        b = self._scatter_state()
        self.assertEqual(len(b), 3)
        self.assertIn("よっつめ", b[0]["text"])
        self.assertFalse(any("ひとつめ" in x["text"] for x in b))
        center = lambda r: (round((r[0] + r[2]) / 2), round((r[1] + r[3]) / 2))
        self.assertEqual(center(b[0]["rect"]), center(before["ひとつめ"]))   # 同じポイントに出る
        for x in b[1:]:                                       # 残った吹き出しは動かない
            for v, w in zip(x["rect"], before[x["text"]]):
                self.assertAlmostEqual(v, w, delta=2)

    def test_switching_mode_away_clears_the_bubble(self):
        self._use()
        self._send({"type": "final", "text": "こんにちは", "id": 5})
        self.assertEqual(self._wraps(), 1)
        self._use(mode="flow")
        self.assertEqual(self._wraps(), 0)


if __name__ == "__main__":
    unittest.main()
