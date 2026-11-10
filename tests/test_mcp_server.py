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
from unittest.mock import patch

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
        cls.m.TOKEN = cls.server.auth.rotate()

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

    def test_tilt_preset_expands_and_reaches_the_overlay(self):
        res = self.m.update_layout({"tilt": "wall-right"})
        self.assertEqual(res["after"], {"tiltX": 0, "tiltY": -26, "tiltZ": 0, "perspective": 1400})
        self.assertEqual(self.events("style")[-1]["box"]["tiltY"], -26)
        # 個別に渡した値はプリセットより優先
        res = self.m.update_layout({"tilt": "floor", "tiltX": 30})
        self.assertEqual((res["after"]["tiltX"], res["after"]["perspective"]), (30, 900))
        for bad in ({"tilt": "spin"}, {"tiltX": 90}, {"perspective": 100}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.m.update_layout(bad)
        # 厚みはデザイン側
        self.m.update_design({"extrude": 8, "extrudeDir": 45})
        with self.assertRaises(ValueError):
            self.m.update_design({"extrude": 21})

    def test_tilt_presets_match_fx_js(self):
        """MCP の 3D 演出プリセットは fx.js の FX.TILT_PRESETS と同じ値（片方だけ直すずれを防ぐ）"""
        import re
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "ui", "fx.js"), encoding="utf-8") as f:
            src = f.read()
        found = {}
        for m in re.finditer(r'\{ id: "([\w-]+)",\s*ja: "[^"]*",\s*v: \{([^}]*)\} \}', src):
            found[m.group(1)] = {k: int(v) for k, v in re.findall(r"(\w+): (-?\d+)", m.group(2))}
        self.assertEqual(found, self.m.TILT_PRESETS)

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

    # ---------------- 単語 ----------------

    def test_word_effect_is_live_merges_and_undoes(self):
        res = self.m.set_word_effect("癒色えも", anim="rainbow", particle="spark", scale=1.5)
        self.assertIsNone(res["before"])
        eff = next(e for e in self.events("style")[-1]["effects"] if e["word"] == "癒色えも")
        self.assertEqual((eff["anim"], eff["particle"], eff["scale"]), ("rainbow", "spark", 1.5))
        # 2回目は渡した項目だけ変わる
        self.m.set_word_effect("癒色えも", color="#FF00AA")
        cur = next(e for e in self.m.get_words()["effects"] if e["word"] == "癒色えも")
        self.assertEqual((cur["color"], cur["anim"]), ("#ff00aa", "rainbow"))
        self.m.undo()
        self.m.undo()
        self.assertFalse(any(e["word"] == "癒色えも" for e in self.m.get_words()["effects"]))

    def test_word_effect_rejects_unknown_options(self):
        for kw in ({"anim": "explode"}, {"particle": "fire"}, {"scale": 9}, {"color": "pink"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.m.set_word_effect("テスト", **kw)
        self.assertEqual(self.m._history, [])

    def test_add_words_keeps_existing_entries(self):
        before = self.m.get_words()["recognition"]
        res = self.m.add_words("recognition", [{"surface": "癒色えも", "reading": "いしきえも／意識エモ"}])
        self.assertEqual(res["added"], ["癒色えも"])
        self.assertFalse(res["restart_needed"])   # 認識は動いていない
        after = self.m.get_words()["recognition"]
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[-1]["reading"], "いしきえも／意識エモ")
        res = self.m.add_words("recognition", [{"surface": "癒色えも", "reading": "いしきえも"}])
        self.assertEqual((res["added"], res["updated"]), ([], ["癒色えも"]))
        self.m.undo()
        self.m.undo()
        self.assertEqual(self.m.get_words()["recognition"], before)

    def test_kanji_word_needs_a_reading(self):
        with self.assertRaisesRegex(ValueError, "reading"):
            self.m.add_words("recognition", [{"surface": "癒色"}])

    def test_translation_and_banned_round_trip(self):
        self.m.add_words("translation", [{"ja": "癒色えも", "en": "ISHIKI Emo"}])
        self.m.add_words("banned", ["ひみつ"])
        w = self.m.get_words()
        self.assertIn({"ja": "癒色えも", "en": "ISHIKI Emo"}, w["translation"])
        self.assertIn("ひみつ", w["banned"])
        self.assertEqual(self.m.remove_words("translation", ["癒色えも"])["removed"], ["癒色えも"])
        self.assertFalse(self.m.remove_words("translation", ["癒色えも"])["ok"])

    def test_banned_words_cannot_be_removed_by_ai(self):
        self.m.add_words("banned", ["ないしょ"])
        with self.assertRaisesRegex(ValueError, "単語"):
            self.m.remove_words("banned", ["ないしょ"])
        self.assertIn("ないしょ", self.m.get_words()["banned"])

    def test_banned_undo_keeps_words_added_by_hand(self):
        self.m.add_words("banned", ["AIが足した語"])
        # AI の操作のあとに、人が Mojicast の画面で伏せ字を足した
        cur = self.m.get_words()["banned"]
        with patch.object(self.m, "TOKEN", self.server.auth.ui_token):
            self.m._api("/api/banned", {"words": cur + ["人が足した語"]})
        self.m.undo()
        banned = self.m.get_words()["banned"]
        self.assertNotIn("AIが足した語", banned)
        self.assertIn("人が足した語", banned)

    def test_sample_caption_is_refused_while_recognizing(self):
        orig = self.m._engine_running
        self.m._engine_running = lambda: True
        try:
            with self.assertRaisesRegex(ValueError, "認識中"):
                self.m.show_sample_caption("配信に出したくない文")
        finally:
            self.m._engine_running = orig
        self.assertEqual(self.events("final"), [])

    def test_profile_scope_is_separate_and_checked(self):
        wordstore.create_profile("ゲームA")
        try:
            self.m.add_words("banned", ["ネタバレ"], profile="ゲームA")
            self.assertIn("ネタバレ", self.m.get_words("ゲームA")["banned"])
            self.assertNotIn("ネタバレ", self.m.get_words()["banned"])
            with self.assertRaisesRegex(ValueError, "プロファイル"):
                self.m.add_words("banned", ["x"], profile="存在しない")
        finally:
            wordstore.delete_profile("ゲームA")

    def test_log_permission_defaults_denies_and_revokes_immediately(self):
        original = app_server.load_config()
        try:
            # An existing config without the new key must fail closed.
            legacy = dict(original)
            legacy.pop("mcp_allow_logs", None)
            app_server.save_config(legacy)
            self.assertIs(app_server.load_config()["mcp_allow_logs"], False)
            with patch.object(app_server, "recent_logs", return_value=[{"lines": ["private"]}]) as read:
                for value in (None, False, "true", 1):
                    with patch.object(app_server, "load_config", return_value={"mcp_allow_logs": value}):
                        with self.assertRaisesRegex(RuntimeError, "ログの読み取りは許可されていません"):
                            self.m.read_recent_logs()
                read.assert_not_called()
                with patch.object(self.m, "TOKEN", self.server.auth.ui_token):
                    self.m._api("/api/config", {"mcp_allow_logs": True})
                self.assertEqual(self.m.read_recent_logs()["logs"], [{"lines": ["private"]}])
                with patch.object(self.m, "TOKEN", self.server.auth.ui_token):
                    self.m._api("/api/config", {"mcp_allow_logs": False})
                with self.assertRaisesRegex(RuntimeError, "ログの読み取りは許可されていません"):
                    self.m._api("/api/logs/recent?sessions=10")
                self.assertEqual(read.call_count, 1)
                self.assertEqual(app_server.load_config()["save_log"], original["save_log"])
                for invalid in ("true", 1, None):
                    with patch.object(self.m, "TOKEN", self.server.auth.ui_token), self.assertRaisesRegex(RuntimeError, "must be a boolean"):
                        self.m._api("/api/config", {"mcp_allow_logs": invalid})
                self.assertIs(app_server.load_config()["mcp_allow_logs"], False)
        finally:
            app_server.save_config(original)

    def test_recent_logs_newest_first(self):
        original = app_server.load_config()
        app_server.save_config({**original, "mcp_allow_logs": True})
        saved = app_server.DATA_BASE
        app_server.DATA_BASE = self.tmp.name
        try:
            self.assertEqual(self.m.read_recent_logs()["logs"], [])
            for day, name, line in (
                    ("2026-10-24", "2026-10-24_200000.log", "[2026-10-24 20:00:01] 前の配信"),
                    ("2026-10-25", "2026-10-25_210000.log", "[2026-10-25 21:00:01] いしきえもです")):
                d = os.path.join(self.tmp.name, "logs", day)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(line + "\n\n")
            logs = self.m.read_recent_logs(5)["logs"]
            self.assertEqual([x["file"] for x in logs],
                             ["2026-10-25/2026-10-25_210000.log", "2026-10-24/2026-10-24_200000.log"])
            self.assertEqual(logs[0]["lines"], ["[2026-10-25 21:00:01] いしきえもです"])
            self.assertEqual(len(self.m.read_recent_logs(1)["logs"]), 1)
        finally:
            app_server.DATA_BASE = saved
            app_server.save_config(original)

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

    @unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
    def test_word_effect_is_drawn_on_the_sample_caption(self):
        """MCP で付けたエフェクトが、試し表示の行のその語に当たる"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{self.port}/overlay")
                page.wait_for_function("() => styleRev !== null")
                self.m.set_word_effect("癒色えも", anim="rainbow", particle="spark")
                self.m.show_sample_caption("癒色えもがお送りしています")
                span = page.locator(".line .fx", has_text="癒色えも")
                span.wait_for(timeout=3000)
                self.assertIn("fx-rainbow", span.get_attribute("class"))
            finally:
                browser.close()
                self.m.undo()

    def test_missing_credential_gives_setup_instructions(self):
        with patch.object(self.m, "TOKEN", ""):
            with self.assertRaisesRegex(RuntimeError, "MOJICAST_TOKEN"):
                self.m.mojicast_status()

    def test_redirect_never_forwards_the_credential(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        seen = []
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(handler):
                seen.append(handler.path)
                handler.send_response(302)
                handler.send_header("Location", "/credential-sink")
                handler.end_headers()
            def log_message(handler, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(self.m, "BASE", f"http://127.0.0.1:{server.server_address[1]}"):
                with self.assertRaises(RuntimeError):
                    self.m._api("/api/status")
            self.assertEqual(seen, ["/api/status"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

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
