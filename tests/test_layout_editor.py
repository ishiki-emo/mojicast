"""レイアウト編集: 編集中は横並びになり、プレビューの枠を直接動かせること

- カスタマイズ中は一覧を畳み、フォームとプレビューを横に並べる（縦に積むと
  「もっと細かく調整」でフォームが数行しか見えなくなっていた）。
- プレビュー上の枠をドラッグすると位置が、右下の角をドラッグすると大きさが変わり、
  スライダーと未保存の印に反映されること。

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
class LayoutEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
        cls.tmp = tempfile.TemporaryDirectory()
        wordstore.DATA = cls.tmp.name
        wordstore.PROFILES_DIR = os.path.join(cls.tmp.name, "profiles")
        wordstore._ready = False
        wordstore.ensure_data()            # 同梱のデザイン・レイアウトを用意する
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
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 800})
        self.page.goto(f"http://127.0.0.1:{self.port}/ui/style?tab=box")
        self.page.wait_for_selector("#blist .pitem")
        self.page.click("#customizeBoxBtn")

    def tearDown(self):
        self.page.close()

    def _geo(self):
        return [int(self.page.eval_on_selector(i, "e => e.value"))
                for i in ("#b_x", "#b_y", "#b_w", "#b_h")]

    def _set(self, sel, value):
        self.page.eval_on_selector(
            sel, "(e, v) => { e.value = v; e.dispatchEvent(new Event('input')) }", value)

    def test_editing_places_form_and_preview_side_by_side(self):
        form = self.page.locator("#bform").bounding_box()
        stage = self.page.locator("#stageWrap").bounding_box()
        self.assertFalse(self.page.is_visible("#blist"))      # 一覧は畳まれる
        self.assertGreater(stage["x"], form["x"] + form["width"] - 1)
        self.assertLess(abs(stage["y"] - form["y"]), 40)      # 縦に積まれていない
        self.page.click("#boxEditTools >> text=← 一覧に戻る")
        self.assertTrue(self.page.is_visible("#blist"))

    def test_drag_moves_and_corner_resizes(self):
        self._set("#b_x", 0); self._set("#b_y", 0)
        self._set("#b_w", 40); self._set("#b_h", 30)
        box = self.page.locator("#stageHandle").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(cx, cy)
        self.page.mouse.down()
        self.page.mouse.move(cx + 60, cy + 40, steps=6)
        self.page.mouse.up()
        x, y, w, h = self._geo()
        self.assertGreater(x, 0); self.assertGreater(y, 0)
        self.assertEqual((w, h), (40, 30))                    # 移動では大きさは変わらない
        self.assertTrue(self.page.evaluate("mojiIsDirty()"))

        grip = self.page.locator("#stageHandle .grip").bounding_box()
        gx, gy = grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2
        self.page.mouse.move(gx, gy)
        self.page.mouse.down()
        self.page.mouse.move(gx + 50, gy + 30, steps=6)
        self.page.mouse.up()
        x2, y2, w2, h2 = self._geo()
        self.assertEqual((x2, y2), (x, y))                    # 大きさ変更では位置は動かない
        self.assertGreater(w2, 40); self.assertGreater(h2, 30)

    def test_scatter_points_can_be_added_moved_and_removed(self):
        page = self.page
        page.click("#boxEditTools >> text=← 一覧に戻る")
        page.click("#blist .pitem:has-text('吹き出し')")
        page.click("#customizeBoxBtn")
        page.select_option("#b_bubbleArrange", "scatter")
        spots = lambda: page.evaluate("boxes[curBox].bubbleSpots.length")
        self.assertEqual(spots(), 4)                                  # 初期ポイント
        self.assertEqual(page.eval_on_selector("#b_bubbleHistory", "e => e.value"), "4")
        self.assertFalse(page.is_visible("#b_x"))                     # 枠の位置と大きさは使わない
        self.assertEqual(page.eval_on_selector_all(".spot-dot", "e => e.length"), 4)
        st = page.locator("#stage").bounding_box()
        page.mouse.click(st["x"] + st["width"] * 0.1, st["y"] + st["height"] * 0.85)
        self.assertEqual(spots(), 5)                                  # クリックで追加
        dot = page.locator(".spot-dot").nth(0).bounding_box()
        before = page.evaluate("boxes[curBox].bubbleSpots[0]")
        page.mouse.move(dot["x"] + 11, dot["y"] + 11)
        page.mouse.down()
        page.mouse.move(dot["x"] + 60, dot["y"] + 30, steps=5)
        page.mouse.up()
        after = page.evaluate("boxes[curBox].bubbleSpots[0]")
        self.assertGreater(after["x"], before["x"])                   # ドラッグで移動
        page.locator(".spot-dot").nth(1).dblclick()
        self.assertEqual(spots(), 4)                                  # ダブルクリックで削除
        self.assertTrue(page.evaluate("mojiIsDirty()"))
        page.click("#saveOnlyBtn")
        page.wait_for_selector("#unsavedMark", state="hidden")
        saved = [b for b in app_server._read_json(app_server._boxes_path(), {})["boxes"] if b["id"] == "bubble"][0]
        self.assertEqual(saved["bubbleArrange"], "scatter")
        self.assertEqual(len(saved["bubbleSpots"]), 4)

    def test_drag_never_leaves_the_screen(self):
        self._set("#b_w", 50); self._set("#b_h", 40)
        box = self.page.locator("#stageHandle").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(cx, cy)
        self.page.mouse.down()
        self.page.mouse.move(cx + 2000, cy + 2000, steps=6)
        self.page.mouse.up()
        x, y, w, h = self._geo()
        self.assertLessEqual(x + w, 100)
        self.assertLessEqual(y + h, 100)


if __name__ == "__main__":
    unittest.main()
