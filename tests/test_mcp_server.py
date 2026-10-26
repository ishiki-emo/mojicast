"""MCP サーバ（tools/mcp/mojicast_mcp.py）のツールの中身を、本物の app_server に対して検証する

mcp パッケージは配布物にも開発 venv にも入れない（uv のスクリプト内依存で持つ）ため、
FastMCP はデコレータだけの差し替えで読み込み、ツール関数を直接呼ぶ。
データフォルダは一時フォルダへ差し替える（本物の設定は触らない）。
"""
import importlib.util
import json
import os
import queue
import sys
import tempfile
import threading
import types
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:                       # pragma: no cover
    sync_playwright = None

import app_server
import wordstore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_mcp_module(port):
    class _FastMCP:
        def __init__(self, *a, **kw):
            pass

        def tool(self, *a, **kw):
            return lambda f: f

    fake = types.ModuleType("mcp.server.fastmcp")
    fake.FastMCP = _FastMCP
    saved = {k: sys.modules.get(k) for k in ("mcp", "mcp.server", "mcp.server.fastmcp")}
    sys.modules.update({"mcp": types.ModuleType("mcp"),
                        "mcp.server": types.ModuleType("mcp.server"),
                        "mcp.server.fastmcp": fake})
    os.environ["MOJICAST_PORT"] = str(port)
    try:
        spec = importlib.util.spec_from_file_location(
            "mojicast_mcp", os.path.join(ROOT, "tools", "mcp", "mojicast_mcp.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.pop("MOJICAST_PORT", None)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class McpToolTests(unittest.TestCase):
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
        cls.m = _load_mcp_module(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        self.m._history.clear()
        self.q = queue.Queue()
        with app_server._clients_lock:
            app_server._clients.append(self.q)

    def tearDown(self):
        with app_server._clients_lock:
            app_server._clients.remove(self.q)

    def events(self, kind):
        out = []
        while not self.q.empty():
            ev = json.loads(self.q.get_nowait())
            if ev["type"] == kind:
                out.append(ev)
        return out

    def test_status_lists_designs_layouts_and_overlay_url(self):
        st = self.m.mojicast_status()
        self.assertEqual(st["overlay_url"], f"http://127.0.0.1:{self.port}/")
        self.assertIn(st["active_design"], [d["id"] for d in st["designs"]])
        self.assertIn(st["active_layout"], [b["id"] for b in st["layouts"]])

    def test_update_design_is_live_and_undoable(self):
        before = self.m.get_design()["design"]
        res = self.m.update_design({"size": 52, "color": "#FFEEDD"})
        self.assertTrue(res["live"])
        self.assertEqual(res["after"], {"size": 52, "color": "#ffeedd"})
        style = self.events("style")[-1]["style"]
        self.assertEqual((style["size"], style["color"]), (52, "#ffeedd"))
        self.m.undo()
        self.assertEqual(self.m.get_design()["design"]["size"], before["size"])
        self.assertEqual(self.events("style")[-1]["style"]["size"], before["size"])

    def test_bad_values_are_rejected_without_saving(self):
        before = self.m.get_design()["design"]
        for changes in ({"size": 999}, {"color": "red"}, {"animIn": "spin"},
                        {"fontSize": 40}, {"shadow": "yes"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.m.update_design(changes)
        self.assertEqual(self.m.get_design()["design"], before)
        self.assertEqual(self.m._history, [])

    def test_layout_must_stay_on_screen(self):
        with self.assertRaises(ValueError):
            self.m.update_layout({"x": 60, "w": 50})
        res = self.m.update_layout({"x": 5, "y": 72, "w": 90, "h": 24, "bgOpacity": 0.5})
        self.assertEqual(self.events("style")[-1]["box"]["y"], 72)
        self.assertEqual(res["before"]["x"], 0)

    def test_duplicate_keeps_the_original_and_switches(self):
        orig = self.m.mojicast_status()["active_design"]
        res = self.m.duplicate("design", "AIで調整")
        st = self.m.mojicast_status()
        self.assertEqual(st["active_design"], res["id"])
        self.m.update_design({"size": 60})
        self.assertNotEqual(self.m.get_design(orig)["design"]["size"], 60)
        self.m.undo()
        self.m.undo()
        self.assertEqual(self.m.mojicast_status()["active_design"], orig)

    def test_sample_caption_reaches_the_overlay(self):
        self.m.show_sample_caption("テストです", translation="This is a test.")
        evs = []
        while not self.q.empty():
            evs.append(json.loads(self.q.get_nowait()))
        f = next(e for e in evs if e["type"] == "final")
        t = next(e for e in evs if e["type"] == "translation")
        self.assertEqual((f["text"], f["sample"]), ("テストです", True))
        self.assertEqual((t["id"], t["text"]), (f["id"], "This is a test."))

    @unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
    def test_sample_caption_is_drawn_with_the_new_design(self):
        """MCP で変えたデザインが、試し表示の行にそのまま当たって描かれる"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{self.port}/overlay")
                page.wait_for_function("() => styleRev !== null")
                self.m.update_design({"size": 57, "color": "#12ab34"})
                self.m.show_sample_caption("見た目の確認です")
                line = page.locator(".line", has_text="見た目の確認です")
                line.wait_for(timeout=3000)
                css = line.evaluate("el => [getComputedStyle(el).fontSize, getComputedStyle(el).color]")
                self.assertEqual(css, ["57px", "rgb(18, 171, 52)"])
            finally:
                browser.close()
                self.m.undo()

    def test_unreachable_mojicast_gives_a_clear_message(self):
        saved = self.m.BASE
        self.m.BASE = "http://127.0.0.1:1"
        try:
            with self.assertRaisesRegex(RuntimeError, "接続できません"):
                self.m.mojicast_status()
        finally:
            self.m.BASE = saved


if __name__ == "__main__":
    unittest.main()
