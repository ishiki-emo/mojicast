# -*- coding: utf-8 -*-
"""字幕描画のブラウザテスト（Playwright / Chromium）

CSSのレイアウト結果を検証するテスト。DOMを組み立てるだけの jsdom では
getBoundingClientRect() が常に 0 を返すため、この種のバグは捕まえられない。

きっかけ: 登場アニメ「タイプライター」で英訳字幕の半角スペースが消え、
「I don't look like you.」が「Idon'tlooklikeyou.」と繋がって表示された
（1文字ずつ display:inline-block の span に入れると、空白1文字だけの
inline-block は空白の折り畳みで幅が 0 になる）。日本語には半角スペースが
無いので日本語だけ使っていると踏まない。プリセット「スマートグラス風」と
配布された「EvenG2」が typewriter のため、そちらでのみ出ていた。

前提: pip install playwright && python -m playwright install chromium
      （開発環境だけの依存。配布物には入れない）
未導入の環境ではスキップする。

実行: reazonspeech-env\\Scripts\\python.exe -m unittest tests.test_overlay_render
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
except ImportError:                                   # 開発環境以外では回さない
    sync_playwright = None

# 半角スペースを含む実際の訳文（英訳・翻訳のみ表示で出る形）
EN_LINE = "I don't look like you, pitto."
JA_LINE = "みなさんこんばんは、今日も配信を始めます。"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, *a):
        pass                                          # テスト出力を汚さない


@unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
class OverlayRenderTests(unittest.TestCase):
    """fx.js が組み立てた行を実ブラウザで描画し、見え方を測る"""

    @classmethod
    def setUpClass(cls):
        cls._srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        cls._port = cls._srv.server_address[1]
        cls._thread = threading.Thread(target=cls._srv.serve_forever, daemon=True)
        cls._thread.start()
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()
        cls.page = cls._browser.new_page(viewport={"width": 1280, "height": 400})
        cls.page.goto(f"http://127.0.0.1:{cls._port}{HARNESS}")

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._srv.shutdown()
        cls._srv.server_close()

    def _render(self, text, anim, effects=None):
        self.page.evaluate("([t, a, e]) => window.render(t, a, e)",
                           [text, anim, effects or []])

    # ---------------- 空白が潰れていないか ----------------

    def test_typewriter_keeps_spaces_between_words(self):
        """タイプライターは1文字ずつ span に切る。空白の span が幅を失うと
        「Idon'tlooklikeyou」になる（実際に配信で出た）"""
        self._render(EN_LINE, "typewriter")
        widths = self.page.evaluate("() => window.spaceWidths()")
        self.assertTrue(widths, "空白が span に切られていない（前提が変わった）")
        self.assertTrue(all(w > 0 for w in widths),
                        f"幅0の空白がある: {widths}")

    def test_every_entrance_animation_keeps_the_line_width(self):
        """登場アニメ全種で、描いた行の幅が素のテキストと変わらないこと。
        空白が潰れると幅が縮むので、種類を増やしてもこの1本で拾える"""
        plain = self.page.evaluate("(t) => window.plainWidth(t)", EN_LINE)
        anims = self.page.evaluate("() => FX.LINE_ANIMS.map(a => a[0])")
        for anim in anims:
            with self.subTest(animIn=anim):
                self._render(EN_LINE, anim)
                w = self.page.evaluate("() => window.lineWidth()")
                # サブピクセルの丸めぶんだけ許容（空白1個=約9pxなので十分小さい）
                self.assertAlmostEqual(
                    w, plain, delta=2.0,
                    msg=f"{anim}: 幅 {w:.1f}px（素のテキストは {plain:.1f}px）")

    def test_wave_effect_word_keeps_inner_space(self):
        """ウェーブは単語内をさらに1文字ずつ切る。「Super Chat」のように
        空白を含む単語を強調登録すると同じ潰れ方をする"""
        effects = [{"word": "Super Chat", "anim": "wave", "color": "#ffd400"}]
        self._render("Thank you for Super Chat!", "slide", effects)
        widths = self.page.evaluate(
            """() => [...document.querySelectorAll('.fx-wavechar')]
                     .filter(e => e.textContent === ' ')
                     .map(e => e.getBoundingClientRect().width)""")
        self.assertTrue(widths, "ウェーブが単語内を文字分割していない")
        self.assertTrue(all(w > 0 for w in widths),
                        f"ウェーブ内に幅0の空白がある: {widths}")

    # ---------------- 文字が落ちていないか ----------------

    def test_rendered_text_matches_the_source_for_every_animation(self):
        """描画後のテキストが原文と一致すること（文字の欠落・重複の検出）"""
        for text in (EN_LINE, JA_LINE):
            for anim in ("typewriter", "slide", "none"):
                with self.subTest(animIn=anim, text=text[:12]):
                    self._render(text, anim)
                    got = self.page.evaluate(
                        "() => document.querySelector('#stage .line').textContent")
                    self.assertEqual(got, text)

    def test_japanese_line_is_unaffected(self):
        """日本語には半角スペースが無い。従来どおり描けていること"""
        plain = self.page.evaluate("(t) => window.plainWidth(t)", JA_LINE)
        self._render(JA_LINE, "typewriter")
        self.assertAlmostEqual(self.page.evaluate("() => window.lineWidth()"),
                               plain, delta=2.0)


if __name__ == "__main__":
    unittest.main()
