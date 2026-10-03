"""複数翻訳（#11）のサーバ側: 設定の正規化・訳文イベント・ボイスコマンド。

data/ を一時フォルダへ向けて、実際の /api/config と _try_voice_command を通す。
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


class MultiTranslationServerTests(unittest.TestCase):
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
        cls.tmp.cleanup()

    def setUp(self):
        app_server.save_config({"translate": True, "translate_lang": "en"})

    def _post_config(self, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/config", json.dumps(body),
                     {"Content-Type": "application/json",
                      "Host": f"127.0.0.1:{self.port}"})
        res = conn.getresponse()
        data = json.loads(res.read())
        conn.close()
        self.assertEqual(res.status, 200, data)
        return app_server.load_config()

    def test_langs_are_normalized_and_mirrored_to_the_primary_key(self):
        cfg = self._post_config({"translate_langs": ["ko", "xx", "ko", "en", "zh", "id"]})
        self.assertEqual(cfg["translate_langs"], ["ko", "en", "zh"])
        self.assertEqual(cfg["translate_lang"], "ko")

    def test_single_language_save_from_old_ui_resets_the_list(self):
        self._post_config({"translate_langs": ["en", "zh"]})
        cfg = self._post_config({"translate_lang": "ko"})
        self.assertEqual(cfg["translate_langs"], [])
        self.assertEqual(cfg["translate_lang"], "ko")

    def test_translation_event_carries_language_and_order(self):
        sent, vrc = [], []
        with mock.patch.object(app_server, "broadcast", sent.append), \
                mock.patch.object(app_server.vrcchat, "on_translation",
                                  lambda fid, text: vrc.append((fid, text))):
            app_server._engine_on_translation(5, "Hello", False, lang="en", order=0)
            app_server._engine_on_translation(5, "你好", False, lang="zh", order=1)
        self.assertEqual(sent[1], {"type": "translation", "id": 5, "text": "你好",
                                   "fallback": False, "lang": "zh", "order": 1})
        self.assertEqual(vrc, [(5, "Hello")])      # VRChat は主言語だけ

    def _voice(self, text):
        sent = []
        with mock.patch.object(app_server, "broadcast", sent.append), \
                mock.patch.object(app_server, "_engine", None):
            self.assertTrue(app_server._try_voice_command(text))
        return app_server.load_config()

    def test_voice_command_promotes_the_language_and_keeps_the_others(self):
        app_server.save_config({"translate": True, "translate_lang": "en",
                                "translate_langs": ["en", "zh", "ko"],
                                "vc_enabled": True, "vc_wake": ["モジキャスト"]})
        cfg = self._voice("モジキャスト、翻訳を韓国語に")
        self.assertEqual(cfg["translate_langs"], ["ko", "en", "zh"])
        self.assertEqual(cfg["translate_lang"], "ko")

    def test_voice_command_on_single_language_stays_single(self):
        app_server.save_config({"translate": True, "translate_lang": "en",
                                "vc_enabled": True, "vc_wake": ["モジキャスト"]})
        cfg = self._voice("モジキャスト、翻訳を韓国語に")
        self.assertEqual(cfg["translate_lang"], "ko")
        self.assertFalse(cfg.get("translate_langs"))


if __name__ == "__main__":
    unittest.main()
