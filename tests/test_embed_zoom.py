"""スタジオに埋め込んだ設定画面（iframe）のフッターが、GUI倍率を掛けても画面内に収まること

背景（2026-09-03 の Mac 報告「1語ずつ登録する画面に保存ボタンがない」）:
WebKit（Mac の WKWebView）は zoom を掛けた親の iframe 内で 100vh / clientHeight が
「iframe の高さ × 親の倍率」になる。倍率>1 で本文が iframe より背が高くなり、
overflow:hidden のフッター（保存ボタン）が画面外へ出る。Chromium では起きない。
theme-sync.js の fixEmbeddedViewport が clientHeight/innerHeight の比で補正する。

前提: pip install playwright && python -m playwright install chromium webkit
（入っていないエンジンはスキップ）
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


MEASURE = """() => {
  const btn = document.getElementById('saveAllBtn');
  return { innerH: window.innerHeight,
           innerW: window.innerWidth,
           bodyH: Math.round(document.body.getBoundingClientRect().height),
           bodyW: Math.round(document.body.getBoundingClientRect().width),
           btnBottom: Math.round(btn.getBoundingClientRect().bottom) };
}"""


@unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
class EmbeddedFooterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
        cls.tmp = tempfile.TemporaryDirectory()
        wordstore.DATA = cls.tmp.name
        wordstore.PROFILES_DIR = os.path.join(cls.tmp.name, "profiles")
        os.makedirs(wordstore.PROFILES_DIR)
        wordstore._ready = True
        cls.server = app_server._QuietHTTPServer(("127.0.0.1", 0), app_server.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready = cls._orig
        cls.tmp.cleanup()

    def _set_scale(self, scale):
        with open(os.path.join(self.tmp.name, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"ui_scale": scale}, f)

    def _open_words(self, browser, scale, width=None, height=None):
        """スタジオ →「認識させる言葉」を GUI 倍率 scale で開き、(page, 子フレーム) を返す

        width / height を指定すると窓の実寸を固定できる（UIサイズだけ変えた状況の再現。
        実アプリでは窓の実寸は次回起動まで変わらないため、倍率と窓寸は独立に動く）
        """
        self._set_scale(scale)
        page = browser.new_page(viewport={"width": width or int(1120 * scale),
                                          "height": height or int(840 * scale)})
        page.goto(f"http://127.0.0.1:{self.port}/ui/studio?s={scale}&tab=words")
        page.wait_for_selector("#fWords[src]")
        # src が付いた直後はナビゲーション未確定で frame の URL が about:blank のことがある
        frame = None
        for _ in range(50):
            frame = page.frame(url=lambda u: "/ui/words" in u)
            if frame is not None:
                break
            page.wait_for_timeout(100)
        self.assertIsNotNone(frame, "words の iframe が見つからない")
        frame.wait_for_selector("#saveAllBtn")
        page.wait_for_timeout(500)          # /api/config 取得後の applyScale を待つ
        return page, frame

    def _assert_fits(self, frame, label):
        m = frame.evaluate(MEASURE)
        self.assertLessEqual(m["btnBottom"], m["innerH"] + 1,
                             f"{label}: 保存ボタンが画面外 {m}")
        self.assertAlmostEqual(m["bodyH"], m["innerH"], delta=2,
                               msg=f"{label}: 本文の高さが画面と合っていない {m}")
        # 幅も高さと同じ機序で乱れる（WebKit では width:100% が iframe の幅×親の倍率に
        # なり、倍率<1 で右側に塗り残しの白帯、>1 で右端が見切れる）
        self.assertAlmostEqual(m["bodyW"], m["innerW"], delta=2,
                               msg=f"{label}: 本文の幅が画面と合っていない {m}")

    def _assert_frame_inside(self, page, label):
        """iframe が親スタジオの画面内に収まっていること。

        スタジオの .layout はグリッドで、暗黙の auto 行だとナビの自然な高さが
        利用可能高さ（窓の高さ÷倍率−ヘッダー）を超えたとき行ごと膨らみ、
        iframe 下端（＝保存フッター）が窓の外へ押し出される（全エンジン共通）。
        """
        m = page.evaluate("""() => {
          const f = document.getElementById('fWords');
          return { frameBottom: Math.round(f.getBoundingClientRect().bottom),
                   htmlH: Math.round(document.documentElement.getBoundingClientRect().height) };
        }""")
        self.assertLessEqual(m["frameBottom"], m["htmlH"] + 1,
                             f"{label}: 設定画面のiframeが窓の外へはみ出している {m}")

    def _run_engine(self, name):
        with sync_playwright() as p:
            try:
                browser = getattr(p, name).launch()
            except Exception as e:          # 実行ファイル未導入
                self.skipTest(f"{name} 未導入: {str(e).splitlines()[0][:80]}")
            try:
                for scale in (0.8, 1.0, 1.25):
                    page, frame = self._open_words(browser, scale)
                    self._assert_fits(frame, f"{name} s={scale}")
                    self._assert_frame_inside(page, f"{name} s={scale}")
                    page.close()
                # 低い窓 × 大きいUIサイズ（Mac 利用者報告「認識辞書で保存ボタンが
                # 出ないことがある・UIサイズで変わる」）。窓の実寸は変えず倍率だけ上げる
                for scale in (1.0, 1.25, 1.5):
                    page, frame = self._open_words(browser, scale, width=854, height=615)
                    self._assert_frame_inside(page, f"{name} 低い窓 s={scale}")
                    self._assert_fits(frame, f"{name} 低い窓 s={scale}")
                    page.close()
                # 起動後に倍率を変えた場合（SSE → 親の applyScale → 子へ通知）も追従する
                page, frame = self._open_words(browser, 0.8)
                page.request.post(f"http://127.0.0.1:{self.port}/api/config",
                                  data=json.dumps({"ui_scale": 1.25}),
                                  headers={"Content-Type": "application/json"})
                page.wait_for_timeout(800)
                self.assertEqual(page.evaluate("getComputedStyle(document.documentElement).zoom"),
                                 "1.25")
                self._assert_fits(frame, f"{name} 0.8→1.25")
                page.close()
            finally:
                browser.close()

    def test_chromium(self):
        self._run_engine("chromium")

    def test_webkit(self):
        self._run_engine("webkit")


if __name__ == "__main__":
    unittest.main()
