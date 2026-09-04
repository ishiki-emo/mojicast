"""platform_compat の隔離規約と、コラボ未対応OSでの多重ガードの検証

- Windows専用API（winreg / windll / GDI / os.startfile / WASAPI）が
  platform_compat.py / proc_loopback.py の外に漏れたらCIで落とす。
- /api/config・/api/loopback-apps がOS能力を正しく申告し、
  未対応OSでは collab=True の書き込みを拒否することを確認する。
"""
import http.client
import json
import re
import threading
import unittest
from pathlib import Path
from unittest import mock

import app_server
import platform_compat
import wordstore

ROOT = Path(__file__).resolve().parent.parent


class PlatformIsolationTests(unittest.TestCase):
    """OS依存APIの呼び出し箇所を platform_compat / proc_loopback に限定する規約の監視"""

    ALLOWED = {"platform_compat.py", "proc_loopback.py"}
    PATTERN = re.compile(
        r"\bwinreg\b|\bwindll\b|\bWinDLL\b|\bWINFUNCTYPE\b|\bstartfile\b")

    def test_no_windows_api_outside_compat(self):
        leaks = []
        for py in sorted(ROOT.glob("*.py")):
            if py.name in self.ALLOWED:
                continue
            for i, line in enumerate(
                    py.read_text(encoding="utf-8").splitlines(), 1):
                if self.PATTERN.search(line):
                    leaks.append(f"{py.name}:{i}: {line.strip()}")
        self.assertEqual(
            leaks, [],
            "Windows専用APIは platform_compat.py に集約してください（漏れ検出）")


class CollabGuardTests(unittest.TestCase):
    """コラボ未対応OS（mac）でのサーバ側ガードとOS能力の申告"""

    @classmethod
    def setUpClass(cls):
        wordstore.ensure_data()
        cls.server = app_server._QuietHTTPServer(
            ("127.0.0.1", 0), app_server.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path,
                     body=json.dumps(body) if body else None, headers=headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        conn.close()
        return res.status, data

    def test_config_reports_collab_supported(self):
        status, cfg = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(cfg.get("collab_supported"),
                         platform_compat.collab_supported())

    def test_config_reports_vrchat_supported(self):
        # mac は VRChat クライアントが無いため UI が欄ごと隠す（表示のみ・送信実装は共通）
        status, cfg = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(cfg.get("vrchat_supported"),
                         platform_compat.vrchat_supported())

    def test_collab_write_rejected_on_unsupported_os(self):
        # 実環境の data/config.json を書き換えないよう保存だけ止める
        with mock.patch.object(app_server, "save_config"):
            status, res = self.request("POST", "/api/config", {"collab": True})
        self.assertEqual(status, 200)
        self.assertEqual(res["config"]["collab"],
                         platform_compat.collab_supported())

    def test_loopback_apps_degrades_on_unsupported_os(self):
        status, res = self.request("GET", "/api/loopback-apps")
        self.assertEqual(status, 200)
        self.assertIn("supported", res)
        if not platform_compat.collab_supported():
            self.assertFalse(res["supported"])
            self.assertEqual(res["apps"], [])

    def test_env_suggest_works_on_all_os(self):
        # 旧実装は mac で winreg import が素通りして 500 になっていた（回帰ガード）
        status, res = self.request("GET", "/api/env-suggest")
        self.assertEqual(status, 200)
        self.assertIn("cpu", res)
        self.assertIn("os_lang", res)

    def test_env_suggest_apple_silicon_tier(self):
        status, res = self.request("GET", "/api/env-suggest?cpu=Apple%20M4")
        self.assertEqual(status, 200)
        self.assertEqual(res["cpu"]["tier"], "best")

    def test_fonts_endpoint_no_error(self):
        status, res = self.request("GET", "/api/fonts")
        self.assertEqual(status, 200)
        self.assertIsInstance(res.get("fonts"), list)
        if platform_compat.IS_WIN or platform_compat.IS_MAC:
            self.assertNotIn("error", res)   # 列挙自体が失敗したら回帰


if __name__ == "__main__":
    unittest.main()
