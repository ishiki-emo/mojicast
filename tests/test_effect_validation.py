"""Effect input boundaries: reject before writing and safely load old data."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import app_server
import wordstore


class EffectValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in (("DATA", self.tmp.name), ("PROFILES_DIR", str(Path(self.tmp.name)/"profiles")), ("_ready", True)):
            p = patch.object(wordstore, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.good = {"word": "配信", "color": "#FFD400", "font": "'游ゴシック',sans-serif",
                     "scale": 1.5, "anim": "shine", "particle": "confetti"}

    def test_defaults_and_all_supported_variants(self):
        defaults = json.loads((Path(__file__).resolve().parents[1]/"defaults/effects.json").read_text(encoding="utf-8-sig"))["effects"]
        self.assertEqual(wordstore.validate_effects(defaults), defaults)
        for anim in wordstore.EFFECT_ANIMS:
            for particle in wordstore.EFFECT_PARTICLES:
                wordstore.validate_effect({**self.good, "anim": anim, "particle": particle})
        for scale in (0.5, 3):
            wordstore.validate_effect({**self.good, "scale": scale})
        wordstore.validate_effect({"word": "最小"})

    def invalid_batches(self):
        yield None
        yield {}
        yield [None]
        yield [self.good] * 5001
        yield [{**self.good, "url": "https://example.invalid/"}]
        for key, values in {
            "word": ["", "   ", None, 1, "x"*257, "x\n"],
            "color": [None, 1, "red", "#fff", "#ffffff\n", "#ffffff;background-image:url(https://example.invalid/)", "url(https://example.invalid/)"],
            "font": [[], "x"*513, "a; background:url(x)", "url(x)", "a\\62", "a\n"],
            "scale": [True, None, "1", 0, 3.1, float("nan"), float("inf"), 10**400],
            "anim": [[], None, "unknown"],
            "particle": [{}, None, "__proto__"],
        }.items():
            for value in values:
                yield [{**self.good, key: value}]

    def test_invalid_batch_never_overwrites_existing_file(self):
        wordstore.save_effects([self.good])
        path = Path(self.tmp.name)/"effects.json"
        original = path.read_bytes()
        for batch in self.invalid_batches():
            with self.subTest(batch=str(batch)[:100]):
                with self.assertRaises(ValueError):
                    wordstore.save_effects(batch)
                self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(ValueError):
            wordstore.save_effects([self.good, {"word": "bad", "scale": -1}])
        self.assertEqual(path.read_bytes(), original)

    def test_imported_invalid_rows_are_skipped_without_rewriting(self):
        path = Path(self.tmp.name)/"effects.json"
        raw = json.dumps({"effects": [self.good, {"word": "bad", "color": "red; background:url(x)"}, None]}).encode()
        path.write_bytes(raw)
        with self.assertLogs("wordstore", level="WARNING"):
            self.assertEqual(wordstore.load_effects(), [self.good])
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(wordstore.merged_effects(), [self.good])

    def test_http_rejects_bad_data_and_preserves_profile(self):
        wordstore.create_profile("test")
        wordstore.save_effects([self.good], "test")
        server = app_server._QuietHTTPServer(("127.0.0.1", 0), app_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for payload, expected in (({"effects": [{"word": "bad", "color": "red"}]}, 400),
                                      ({}, 400), ({"effects": [self.good]}, 200)):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                try:
                    conn.request("POST", "/api/effects", json.dumps({**payload, "profile": "test"}), {"Content-Type": "application/json"})
                    response = conn.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
                finally:
                    conn.close()
                self.assertEqual(wordstore.load_effects("test"), [self.good])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
