"""更新通知（latest.json）のリンク先を、許可した配布先に限ること

latest.json が書き換えられても、更新バナーや「更新を確認」から
別のサイトへ誘導されないようにする。許可外なら url を空にし、
バナー（版と説明）は従来どおり出す。
"""
import io
import json
import os
import unittest
from unittest import mock

import app_server

ROOT = os.path.join(os.path.dirname(__file__), "..")


class SafeUpdateUrlTests(unittest.TestCase):
    def test_current_latest_json_url_is_allowed(self):
        # 今 main / develop の latest.json が指している配布先がそのまま通ること
        with open(os.path.join(ROOT, "latest.json"), encoding="utf-8") as f:
            url = json.load(f)["url"]
        self.assertTrue(url)
        self.assertEqual(app_server._safe_update_url(url), url)

    def test_allowed(self):
        for url in ("https://emo-ishiki.booth.pm/items/8643987",
                    "https://EMO-ISHIKI.booth.pm/items/8643987",
                    "https://github.com/ishiki-emo/mojicast/releases/latest",
                    "https://github.com/ishiki-emo/mojicast/releases/tag/v1.2.0",
                    "https://ishiki-emo.com/mojicast",
                    "https://ishiki-emo.com/mojicast?from=update#dl"):
            self.assertEqual(app_server._safe_update_url(url), url, url)

    def test_rejected(self):
        for url in ("http://emo-ishiki.booth.pm/items/8643987",      # http
                    "https://evil.booth.pm/items/1",                 # 他のショップ
                    "https://booth.pm.evil.example/items/1",
                    "https://emo-ishiki.booth.pm.evil.example/",
                    "https://github.com/someone-else/mojicast/releases",
                    "https://github.com/ishiki-emo/../someone-else/x",
                    "https://github.com/ishiki-emo/%2e%2e/someone-else/x",
                    "https://github.com.evil.example/ishiki-emo/",
                    "https://user:pw@emo-ishiki.booth.pm/items/1",
                    "https://emo-ishiki.booth.pm@evil.example/",
                    "https://emo-ishiki.booth.pm:8443/items/1",
                    "https://emo-ishiki.booth.pm:bad/items/1",
                    "https:\\\\evil.example\\x",
                    "https://emo-ishiki.booth.pm/items/1\nx",
                    "javascript:alert(1)",
                    "file:///C:/Windows/System32/calc.exe",
                    "//evil.example/", "", None, 123, ["https://ishiki-emo.com/"]):
            self.assertEqual(app_server._safe_update_url(url), "", url)


class CheckUpdateTests(unittest.TestCase):
    def setUp(self):
        self._saved = (app_server._update_cache, app_server._update_cache_at)
        app_server._update_cache = None

    def tearDown(self):
        app_server._update_cache, app_server._update_cache_at = self._saved

    def _check(self, manifest):
        body = io.BytesIO(json.dumps(manifest).encode("utf-8"))
        with mock.patch("urllib.request.urlopen", return_value=body):
            return app_server._check_update(force=True)

    def test_allowed_url_is_returned(self):
        r = self._check({"version": "99.0.0", "notes": "n",
                         "url": "https://emo-ishiki.booth.pm/items/8643987"})
        self.assertTrue(r["update_available"])
        self.assertEqual(r["url"], "https://emo-ishiki.booth.pm/items/8643987")

    def test_disallowed_url_is_blanked_but_banner_still_shows(self):
        r = self._check({"version": "99.0.0", "notes": "n",
                         "url": "https://evil.example/Mojicast-setup.exe"})
        self.assertTrue(r["update_available"])
        self.assertEqual(r["latest"], "99.0.0")
        self.assertEqual(r["url"], "")
        self.assertEqual(r["notes"], "n")


if __name__ == "__main__":
    unittest.main()
