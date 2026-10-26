"""見た目（style）の配信が OBS の overlay で古いまま止まらないことの検証

報告: 字幕のフォントやレイアウトを変えても、OBS のブラウザソースをリロードするまで
反映されないことがある（字幕自体は出る）。経路は2つ:

- 送信待ちが溢れたとき古い順に捨てていたため、style が捨てられると後続の字幕だけが届く。
- 新UIの保存はプリセットとレイアウトを同時に POST し、それぞれが両方のファイルを
  読み直して style を送る。排他が無いと先に作った古い一式が後から届きうる。

対策: 溢れても style/clear は捨てない・overlay へレベル表示を送らない・style の作成から
送信までを排他・final に版番号を載せ、overlay が食い違いに気づいたら取り直す。

実行: reazonspeech-env\\Scripts\\python.exe -m unittest tests.test_style_sync
"""
import http.client
import json
import os
import queue
import tempfile
import threading
import unittest
from unittest import mock

try:
    from playwright.sync_api import sync_playwright
except ImportError:                       # pragma: no cover
    sync_playwright = None

import app_server
import wordstore


def _kinds(q):
    out = []
    while not q.empty():
        out.append(json.loads(q.get_nowait())["type"])
    return out


class _TempData:
    """本物の設定を触らないよう、データフォルダを一時フォルダへ差し替える"""

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

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body is not None else None)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        conn.close()
        return data

    @staticmethod
    def active(items, wanted):
        return next(i for i, x in enumerate(items) if x["id"] == wanted)


class QueueOverflowTests(unittest.TestCase):
    """送信待ちが溢れたときの整理"""

    def setUp(self):
        self._recover = app_server.SSE_QUEUE_RECOVER_ITEMS
        app_server.SSE_QUEUE_RECOVER_ITEMS = 3

    def tearDown(self):
        app_server.SSE_QUEUE_RECOVER_ITEMS = self._recover

    def _broadcast_into(self, q, events):
        with app_server._clients_lock:
            app_server._clients.append(q)
        try:
            for ev in events:
                app_server.broadcast(ev)
        finally:
            with app_server._clients_lock:
                app_server._clients.remove(q)

    def test_style_survives_a_flood_of_levels(self):
        q = queue.Queue(maxsize=6)
        self._broadcast_into(q, [{"type": "style"}, {"type": "clear"}]
                             + [{"type": "level"}] * 20 + [{"type": "final"}])
        kinds = _kinds(q)
        self.assertIn("style", kinds)
        self.assertIn("clear", kinds)
        self.assertEqual(kinds[-1], "final")

    def test_levels_and_partials_go_before_finals(self):
        q = queue.Queue(maxsize=5)
        self._broadcast_into(q, [{"type": "final"}, {"type": "partial"},
                                 {"type": "level"}, {"type": "translation"},
                                 {"type": "level"}, {"type": "final"}])
        self.assertEqual(_kinds(q), ["final", "translation", "level", "final"])

    def test_only_the_latest_style_is_kept(self):
        q = queue.Queue(maxsize=4)
        self._broadcast_into(q, [{"type": "style", "rev": 1}, {"type": "style", "rev": 2},
                                 {"type": "final"}, {"type": "final"},
                                 {"type": "final"}])
        items = []
        while not q.empty():
            items.append(json.loads(q.get_nowait()))
        self.assertEqual([e.get("rev") for e in items if e["type"] == "style"], [2])

    def test_overlay_connection_does_not_receive_levels(self):
        overlay, gui = queue.Queue(), queue.Queue()
        overlay.skip = app_server._SSE_OVERLAY_SKIP
        with app_server._clients_lock:
            app_server._clients.extend([overlay, gui])
        try:
            app_server.broadcast({"type": "level", "value": 0.5})
            app_server.broadcast({"type": "final", "text": "こんにちは"})
        finally:
            with app_server._clients_lock:
                app_server._clients.remove(overlay)
                app_server._clients.remove(gui)
        self.assertEqual(_kinds(overlay), ["final"])
        self.assertEqual(_kinds(gui), ["level", "final"])


class StyleBroadcastTests(_TempData, unittest.TestCase):
    """保存から style 配信までの経路"""

    def setUp(self):
        self.q = queue.Queue()
        with app_server._clients_lock:
            app_server._clients.append(self.q)

    def tearDown(self):
        with app_server._clients_lock:
            app_server._clients.remove(self.q)

    def styles(self):
        out = []
        while not self.q.empty():
            ev = json.loads(self.q.get_nowait())
            if ev["type"] == "style":
                out.append(ev)
        return out

    def test_concurrent_preset_and_layout_save_ends_with_both(self):
        """新UIの persistAll と同じ同時 POST。最後の style は両方の保存を含み、
        版は単調に進む"""
        cfg = app_server.load_config()
        presets = self.request("GET", "/api/presets")["presets"]
        boxes = self.request("GET", "/api/boxes")["boxes"]
        pi, bi = self.active(presets, cfg["preset"]), self.active(boxes, cfg["box"])
        for t in range(30):
            presets[pi]["font"] = f"'Font{t}'"
            boxes[bi]["x"] = t
            th = [threading.Thread(target=self.request, args=("POST", "/api/presets", {"presets": presets})),
                  threading.Thread(target=self.request, args=("POST", "/api/boxes", {"boxes": boxes}))]
            for x in th:
                x.start()
            for x in th:
                x.join()
            styles = self.styles()
            with self.subTest(trial=t):
                self.assertEqual(styles[-1]["style"]["font"], f"'Font{t}'")
                self.assertEqual(styles[-1]["box"]["x"], t)
                revs = [s["rev"] for s in styles]
                self.assertEqual(revs, sorted(set(revs)))

    def test_final_carries_the_style_revision(self):
        app_server.broadcast_style()
        rev = self.styles()[-1]["rev"]
        with mock.patch.object(app_server, "_try_voice_command", return_value=False), \
                mock.patch.object(app_server.vrcchat, "on_final"):
            app_server._engine_on_final("こんにちは", 1)
        final = json.loads(self.q.get_nowait())
        self.assertEqual((final["type"], final["rev"]), ("final", rev))

    def test_api_style_returns_the_current_snapshot(self):
        app_server.broadcast_style()
        sent = self.styles()[-1]
        got = self.request("GET", "/api/style")
        self.assertEqual(got["type"], "style")
        self.assertEqual(got["rev"], sent["rev"])
        self.assertEqual(got["style"], sent["style"])
        self.assertEqual(got["box"], sent["box"])

    def test_write_json_leaves_no_temp_file(self):
        path = os.path.join(self.tmp.name, "probe.json")
        app_server._write_json(path, {"a": 1})
        app_server._write_json(path, {"a": 2})
        self.assertEqual(app_server._read_json(path, None), {"a": 2})
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.endswith(".tmp")], [])


@unittest.skipIf(sync_playwright is None, "playwright 未導入のためスキップ")
class OverlayResyncTests(_TempData, unittest.TestCase):
    """style を取りこぼした overlay が、次の字幕で自力で最新の見た目へ戻る"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:            # pragma: no cover
            cls.pw.stop()
            super().tearDownClass()
            raise unittest.SkipTest(f"chromium 未導入: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def test_missed_style_is_recovered_on_next_final(self):
        page = self.browser.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/overlay")
        page.wait_for_function("() => styleRev !== null")

        # 保存はされたが style イベントが overlay に届かなかった状態を作る
        cfg = app_server.load_config()
        presets = app_server._read_json(app_server._presets_path(), {})["presets"]
        presets[self.active(presets, cfg["preset"])]["font"] = "'MissedFont'"
        app_server._write_json(app_server._presets_path(), {"presets": presets})
        with app_server._style_lock:
            app_server._style_rev += 1
        self.assertNotEqual(page.evaluate("panes.get('').style.font"), "'MissedFont'")

        app_server.broadcast({"type": "final", "text": "こんにちは", "id": 1,
                              "speaker": "", "rev": app_server._style_rev})
        page.wait_for_function("() => panes.get('').style.font === \"'MissedFont'\"",
                               timeout=3000)
        page.close()

    def test_overlay_connects_as_overlay(self):
        page = self.browser.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/overlay")
        page.wait_for_function("() => styleRev !== null")
        with app_server._clients_lock:
            skips = [getattr(q, "skip", ()) for q in app_server._clients]
        self.assertIn(app_server._SSE_OVERLAY_SKIP, skips)
        page.close()


if __name__ == "__main__":
    unittest.main()
