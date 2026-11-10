"""ガンビットのサーバ側: API・字幕/音イベントからの流し込み・行動の実行・音声コマンドの回帰。

data/ を一時フォルダへ向けて、実際の HTTP API と確定字幕のコールバックを通す。
"""
import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import app_server
import wordstore


def card(cid, **kw):
    c = {"id": cid, "name": kw.pop("name", cid), "triggers": [], "links": []}
    c.update(kw)
    return c


YABAI = card("c1", name="やばい",
             triggers=[{"type": "word", "words": ["やばい"], "speaker": "any"},
                       {"type": "sound", "group": "笑い", "speaker": "any"}],
             display={"widget": {"type": "counter", "pos": {"x": 0.9, "y": 0.1}}},
             links=[{"when": "every", "n": 2, "op": "add", "target": "c2"}])
SOLO = {**YABAI, "links": []}      # つなぎ先なしで単独保存できる形
RANK = card("c2", name="やばランク",
            links=[{"when": "reach", "n": 1, "op": "box", "id": "b2"},
                   {"when": "reach", "n": 1, "op": "stamp", "group": "笑い"}])


class GambitServerTests(unittest.TestCase):
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
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready = cls._orig
        app_server._gambit = None
        cls.tmp.cleanup()

    def setUp(self):
        for name in ("gambits.json", "gambit_values.json"):
            try:
                os.remove(wordstore.data_path(name))
            except FileNotFoundError:
                pass
        app_server._gambit = None
        app_server.save_config({"collab": False, "vc_enabled": False})
        app_server._write_json(app_server._boxes_path(), {"boxes": [
            {"id": "b1", "name": "ふつう"}, {"id": "b2", "name": "限界"}]})
        app_server._write_json(app_server._presets_path(), {"presets": [
            {"id": "p1", "name": "標準"}]})
        app_server._write_json(app_server._scenes_path(), {"scenes": []})

    def _req(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Authorization": "Bearer " + self.server.auth.ui_token,
                      "Content-Type": "application/json",
                      "Host": f"127.0.0.1:{self.port}"})
        res = conn.getresponse()
        data = json.loads(res.read())
        conn.close()
        return res.status, data

    def _save(self, cards):
        with mock.patch.object(app_server, "broadcast"):
            status, data = self._req("POST", "/api/gambits", {"cards": cards})
        self.assertEqual(status, 200, data)
        return data

    def _final(self, text, spk=""):
        sent = []
        with mock.patch.object(app_server, "broadcast", sent.append):
            app_server._engine_on_final(text, 1, spk)
        return sent

    # ---- API ----

    def test_save_and_get(self):
        self._save([YABAI, RANK])
        status, data = self._req("GET", "/api/gambits")
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in data["cards"]], ["c1", "c2"])
        self.assertEqual(data["values"], {"c1": 0, "c2": 0})
        with open(wordstore.data_path("gambits.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["cards"]), 2)

    def test_invalid_save_is_rejected_before_writing(self):
        bad = [card("c1", links=[{"when": "every", "n": 1, "op": "add",
                                  "target": "c1"}])]
        status, data = self._req("POST", "/api/gambits", {"cards": bad})
        self.assertEqual(status, 400)
        self.assertIn("下のカード", data["error"])
        self.assertFalse(os.path.exists(wordstore.data_path("gambits.json")))

    def test_bump_and_reset(self):
        self._save([YABAI, RANK])
        with mock.patch.object(app_server, "broadcast"):
            self.assertEqual(self._req("POST", "/api/gambits/bump",
                                       {"id": "c1", "value": 5})[1]["value"], 5)
            self.assertEqual(self._req("POST", "/api/gambits/bump",
                                       {"id": "c1", "op": "reset"})[1]["value"], 0)
            self.assertEqual(self._req("POST", "/api/gambits/bump",
                                       {"id": "zz"})[0], 404)
            self.assertEqual(self._req("POST", "/api/gambits/bump",
                                       {"id": "c1", "value": True})[0], 400)

    def test_saving_keeps_current_values(self):
        self._save([YABAI, RANK])
        self._final("やばい")
        self._save([YABAI, RANK])
        self.assertEqual(self._req("GET", "/api/gambits")[1]["values"]["c1"], 1)

    # ---- 字幕・音イベントからの流し込み ----

    def test_final_counts_saves_and_broadcasts(self):
        self._save([YABAI, RANK])
        sent = self._final("やばいやばい")
        kinds = [e["type"] for e in sent]
        self.assertEqual(kinds[0], "final")                    # 字幕が先
        self.assertIn({"type": "widget", "id": "c1", "value": 2, "delta": 2}, sent)
        with open(wordstore.data_path("gambit_values.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["values"], {"c1": 2, "c2": 1})
        # 節目のつなぎ: レイアウト切替とスタンプ
        self.assertEqual(app_server.load_config()["box"], "b2")
        self.assertTrue(any(e["type"] == "sound" and e.get("gambit") for e in sent))
        self.assertTrue(any(e["type"] == "vc" and e["ok"] for e in sent))

    def test_sound_event_counts(self):
        self._save([YABAI, RANK])
        sent = []
        with mock.patch.object(app_server, "broadcast", sent.append):
            app_server._engine_on_sound("笑い", 0.8, "")
        self.assertEqual(sent[0]["type"], "sound")
        self.assertIn({"type": "widget", "id": "c1", "value": 1, "delta": 1}, sent)

    def test_missing_switch_target_is_reported(self):
        gone = card("c1", links=[{"when": "reach", "n": 1, "op": "scene", "id": "nope"}],
                    triggers=[{"type": "word", "words": ["やばい"]}])
        self._save([gone])
        sent = self._final("やばい")
        errors = [e for e in sent if e["type"] == "vc" and not e["ok"]]
        self.assertEqual(len(errors), 1)
        self.assertIn("カード「c1」", errors[0]["message"])

    def test_gambit_failure_does_not_stop_captions(self):
        self._save([SOLO])
        with mock.patch.object(app_server._get_gambit(), "on_final",
                               side_effect=RuntimeError("boom")):
            sent = self._final("やばい")
        self.assertEqual(sent[0]["type"], "final")
        self.assertTrue(any(e["type"] == "warn" and "boom" in e["message"]
                            for e in sent))

    def test_collab_speaker_roles(self):
        only_guest = card("g", triggers=[{"type": "word", "words": ["ごめん"],
                                          "speaker": "guest"}])
        self._save([only_guest])
        app_server.save_config({"collab": True, "self_name": "えも"})
        self._final("ごめん", "えも")
        self._final("ごめん", "")
        self.assertEqual(app_server._get_gambit().values["g"], 0)
        self._final("ごめん", "ゲスト")
        self.assertEqual(app_server._get_gambit().values["g"], 1)

    def test_voice_command_is_not_counted_and_still_works(self):
        app_server._write_json(app_server._vc_commands_path(), {"commands": [
            {"id": "v1", "phrases": ["やばいモード"],
             "action": {"type": "box", "id": "b2"}}]})
        app_server.save_config({"vc_enabled": True, "vc_wake": ["モジキャスト"],
                                "box": "b1"})
        self._save([SOLO])
        sent = self._final("モジキャスト、やばいモード")
        self.assertFalse(any(e["type"] == "final" for e in sent))
        self.assertEqual(app_server.load_config()["box"], "b2")
        self.assertEqual(app_server._get_gambit().values["c1"], 0)

    def test_voice_command_missing_target_message_is_unchanged(self):
        app_server._write_json(app_server._vc_commands_path(), {"commands": [
            {"id": "v1", "phrases": ["消えた"],
             "action": {"type": "scene", "id": "gone"}}]})
        app_server.save_config({"vc_enabled": True, "vc_wake": ["モジキャスト"]})
        sent = self._final("モジキャスト、消えた")
        self.assertEqual([e["message"] for e in sent if e["type"] == "vc"],
                         ["コマンドの切替先が見つかりません（削除された可能性があります）"])

    def test_voice_resets_silence_and_tick_counts(self):
        quiet = card("q", triggers=[{"type": "silence", "seconds": 5}])
        self._save([quiet])
        g = app_server._get_gambit()
        t = [100.0]
        g._clock = lambda: t[0]
        sent = []
        with mock.patch.object(app_server, "broadcast", sent.append):
            g.on_tick(True, False)                          # 動き出し
            t[0] = 104
            app_server._engine_on_partial("あの", "")       # 話している途中
            t[0] = 108
            app_server._gambit_run(lambda g: g.on_tick(True, False))
            self.assertEqual(g.values["q"], 0)
            t[0] = 109
            app_server._gambit_run(lambda g: g.on_tick(True, False))
        self.assertEqual(g.values["q"], 1)
        self.assertIn({"type": "widget", "id": "q", "value": 1, "delta": 1}, sent)

    def test_heard_failure_does_not_stop_captions(self):
        self._save([{**SOLO, "triggers": [{"type": "silence", "seconds": 5}]}])
        with mock.patch.object(app_server._get_gambit(), "heard",
                               side_effect=RuntimeError("boom")):
            sent = self._final("やばい")
        # 字幕は止めない。失敗は黙らせず警告に出す（確定字幕・音イベントの経路と同じ）
        self.assertIn("final", [e["type"] for e in sent])
        self.assertIn("gambit", [e.get("kind") for e in sent if e["type"] == "warn"])

    # ---- style 一式 / 起動時 ----

    def test_style_carries_widgets_with_values(self):
        self._save([YABAI, RANK])
        self._final("やばい")
        widgets = app_server.resolve_style(app_server.load_config())["widgets"]
        self.assertEqual([(w["id"], w["value"]) for w in widgets], [("c1", 1)])
        self.assertEqual(widgets[0]["name"], "やばい")

    def test_label_look_follows_number_unless_set(self):
        app_server._write_json(app_server._presets_path(), {"presets": [
            {"id": "p1", "name": "標準", "color": "#ffffff"},
            {"id": "p2", "name": "赤", "color": "#ff0000"}]})
        app_server.save_config({"preset": "p1"})
        same = {**SOLO, "display": {"widget": {"type": "counter", "style": "p2"}}}
        self._save([same])
        w = app_server.resolve_style(app_server.load_config())["widgets"][0]
        self.assertEqual((w["look"]["color"], w["label_look"]["color"]), ("#ff0000", "#ff0000"))
        split = {**SOLO, "display": {"widget": {"type": "counter", "style": "p2",
                                                "label_style": "p1", "label_scale": 1.2}}}
        self._save([split])
        w = app_server.resolve_style(app_server.load_config())["widgets"][0]
        self.assertEqual((w["look"]["color"], w["label_look"]["color"]), ("#ff0000", "#ffffff"))
        self.assertEqual(w["label_scale"], 1.2)
        # 単位も、見た目を指定しなければ数字と同じ
        self.assertEqual(w["unit_look"]["color"], "#ff0000")
        unit = {**SOLO, "display": {"widget": {"type": "counter", "style": "p2",
                                               "unit": "回", "unit_style": "p1"}}}
        self._save([unit])
        w = app_server.resolve_style(app_server.load_config())["widgets"][0]
        self.assertEqual((w["unit"], w["unit_look"]["color"]), ("回", "#ffffff"))

    def test_broken_gambit_does_not_break_style(self):
        with mock.patch.object(app_server, "_get_gambit",
                               side_effect=RuntimeError("boom")):
            out = app_server.resolve_style(app_server.load_config())
        self.assertEqual(out["widgets"], [])
        self.assertIn("style", out)

    def test_start_reset_happens_once_per_app_launch(self):
        manual = card("m", reset="manual")
        self._save([SOLO, manual])
        with mock.patch.object(app_server, "broadcast"):
            self._req("POST", "/api/gambits/bump", {"id": "c1", "value": 3})
            self._req("POST", "/api/gambits/bump", {"id": "m", "value": 4})
        app_server._gambit = None                              # アプリ再起動相当
        self.assertEqual(app_server._get_gambit().values, {"c1": 0, "m": 4})


if __name__ == "__main__":
    unittest.main()
