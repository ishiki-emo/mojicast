# -*- coding: utf-8 -*-
"""字幕の3D化・第1段階（枠ごと傾ける＋厚み）のブラウザテスト（Playwright / Chromium）

CAPTION3D_DESIGN.md §2.3。傾きは枠（boxEl）に掛け、遠近は枠ごとの全面の包みに置く。
傾けないときは今と同じ DOM のまま（包みを作らない）であることも確かめる。

前提: pip install playwright && python -m playwright install chromium
実行: reazonspeech-env\\Scripts\\python.exe -m unittest tests.test_caption3d
"""
import http.server
import os
import socketserver
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = "/tests/fixtures/fx_harness.html"

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

# overlay.html の makePane と同じ組み立て（枠 > 切り取り > 流れ > 行）を 1920×1080 の舞台に置く
SETUP = """(box) => {
  document.body.innerHTML = '';
  const stage = document.createElement('div');
  stage.id = 'stage3d';
  stage.style.cssText = 'position:relative;width:1920px;height:1080px;font:900 40px sans-serif;color:#fff';
  const boxEl = document.createElement('div');
  const clipEl = document.createElement('div');
  const scroll = document.createElement('div');
  clipEl.append(scroll); boxEl.append(clipEl); stage.append(boxEl);
  document.body.append(stage);
  window.P = { stage, boxEl, clipEl, scroll };
  FX.applyBox(boxEl, scroll, box, clipEl);
  return true;
}"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, *a):
        pass


@unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
class Caption3dTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        cls._port = cls._srv.server_address[1]
        threading.Thread(target=cls._srv.serve_forever, daemon=True).start()
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._srv.shutdown()
        cls._srv.server_close()

    def setUp(self):
        self.page = self._browser.new_page(viewport={"width": 1920, "height": 1080})
        self.page.goto(f"http://127.0.0.1:{self._port}{HARNESS}")

    def tearDown(self):
        self.page.close()

    def setup(self, **box):
        base = {"mode": "flow", "x": 10, "y": 55, "w": 80, "h": 35, "padding": 16}
        self.page.evaluate(SETUP, {**base, **box})

    def wrap_state(self):
        return self.page.evaluate("""() => ({
            wrapped: P.boxEl.parentElement.classList.contains('fx-persp'),
            parentIsStage: P.boxEl.parentElement === P.stage,
            persp: P.boxEl.parentElement.style.perspective,
            transform: P.boxEl.style.transform,
            origin: P.boxEl.style.transformOrigin,
            wraps: document.querySelectorAll('.fx-persp').length })""")

    # ---- 傾き ----

    def test_flat_box_keeps_the_same_dom(self):
        self.setup()
        s = self.wrap_state()
        self.assertTrue(s["parentIsStage"])
        self.assertEqual((s["wraps"], s["transform"]), (0, ""))

    def test_tilt_wraps_with_perspective_and_unwraps_when_flat(self):
        self.setup(tiltY=-26, perspective=1400, x=60, w=38)
        s = self.wrap_state()
        self.assertTrue(s["wrapped"])
        self.assertEqual(s["persp"], "1400px")
        self.assertIn("rotateY(-26deg)", s["transform"])
        self.assertEqual(s["origin"], "100% 50%")          # 右寄りの枠は右端が軸（壁）
        # 何度当て直しても包みは1つ
        self.page.evaluate("() => FX.applyBox(P.boxEl, P.scroll, {mode:'flow', x:0, w:40, tiltY:26}, P.clipEl)")
        s = self.wrap_state()
        self.assertEqual((s["wraps"], s["origin"], s["persp"]), (1, "0% 50%", "1200px"))
        # 平面に戻すと包みを外して元の場所へ
        self.page.evaluate("() => FX.applyBox(P.boxEl, P.scroll, {mode:'flow'}, P.clipEl)")
        s = self.wrap_state()
        self.assertTrue(s["parentIsStage"])
        self.assertEqual((s["wraps"], s["transform"]), (0, ""))

    def test_lyric_ignores_tilt(self):
        self.setup(mode="lyric", tiltX=40)
        s = self.wrap_state()
        self.assertEqual((s["wraps"], s["transform"]), (0, ""))

    def test_values_are_clamped(self):
        t = self.page.evaluate("() => FX.tiltOf({tiltX: 999, tiltY: -999, tiltZ: 3, perspective: 10})")
        self.assertEqual(t, {"tiltX": 65, "tiltY": -60, "tiltZ": 3, "perspective": 400})
        self.assertIsNone(self.page.evaluate("() => FX.tiltOf({tiltX: '', tiltY: null})"))

    def test_remove_box_takes_the_wrapper_too(self):
        self.setup(tiltX=40)
        self.page.evaluate("() => FX.removeBox(P.boxEl)")
        self.assertEqual(self.page.evaluate("() => document.querySelectorAll('.fx-persp').length"), 0)

    def test_preset_lookup(self):
        ev = self.page.evaluate
        self.assertEqual(ev("() => FX.tiltPresetOf({})"), "flat")
        self.assertEqual(ev("() => FX.tiltPresetOf({tiltY: -26, perspective: 1400})"), "wall-right")
        self.assertEqual(ev("() => FX.tiltPresetOf({tiltX: 40, perspective: 900})"), "floor")
        self.assertEqual(ev("() => FX.tiltPresetOf({tiltY: -25, perspective: 1400})"), "")   # カスタム

    def test_tilted_box_still_draws_text(self):
        self.setup(tiltX=40, perspective=900)
        w = self.page.evaluate("""() => {
            const d = document.createElement('div'); d.textContent = 'みなさんこんばんは';
            P.scroll.append(d); return d.getBoundingClientRect().width; }""")
        self.assertGreater(w, 50)

    # ---- 見切れ行の掃除（傾けても、平面と同じ行を残す） ----

    def pruned_survivors(self, **box):
        self.setup(**box)
        return self.page.evaluate("""async () => {
            for (let i = 0; i < 12; i++) {
              const d = document.createElement('div');
              d.textContent = '行' + i + ' みなさんこんばんは';
              P.scroll.append(d);
            }
            FX.pruneClipped(P.clipEl, P.scroll, %s);
            await new Promise(r => setTimeout(r, 600));
            return [...P.scroll.children].map(d => d.textContent.split(' ')[0]);
        }""" % ("{" + ",".join(f"{k}:{v!r}" for k, v in {"mode": "flow", **box}.items()) + "}"))

    def test_prune_matches_flat_when_tilted(self):
        flat = self.pruned_survivors()
        self.assertLess(len(flat), 12)                       # 枠に入りきらない行は落ちる
        self.assertIn("行11", flat)                           # 最新行は残る
        for tilt in ({"tiltX": 40, "perspective": 900}, {"tiltY": -26, "perspective": 1400},
                     {"tiltX": -20, "tiltZ": 15}):
            with self.subTest(**tilt):
                self.assertEqual(self.pruned_survivors(**tilt), flat)

    # ---- 厚み ----

    def test_extrude_zero_keeps_the_shadow_unchanged(self):
        same = self.page.evaluate("""() => {
            const s = {outlineWidth: 2, outlineColor: '#000000', glow: '#00e5ff', glowSize: 10, shadow: true};
            return FX.buildTextShadow(s) === FX.buildTextShadow({...s, extrude: 0}); }""")
        self.assertTrue(same)

    def test_extrude_adds_layers_and_colored_words_keep_it(self):
        n = self.page.evaluate("""() => {
            const style = {outlineWidth: 2, outlineColor: '#000000', color: '#ffffff', extrude: 8, extrudeDir: 45};
            const layers = (s) => (s.match(/rgba?\\(/g) || []).length;
            const plain = layers(FX.buildTextShadow({...style, extrude: 0}));
            const deep = layers(FX.buildTextShadow(style));
            const el = document.createElement('div');
            FX.renderWords(el, 'これはやばい', FX.buildTable([], [{word: 'やばい', color: '#ff2d95'}], style), style);
            const word = el.querySelector('.fx').style.textShadow;
            return { added: deep - plain, wordHasDepth: (word.match(/rgb\\(/g) || []).length >= 8 }; }""")
        self.assertEqual(n["added"], 9)                      # 8層＋落ち影
        self.assertTrue(n["wordHasDepth"])


if __name__ == "__main__":
    unittest.main()
