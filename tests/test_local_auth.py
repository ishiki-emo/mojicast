"""Authentication boundaries over real HTTP, including the native-window bootstrap flow."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import app_server
import local_auth
import wordstore

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class AuthFixture:
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
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
        cls.thread.join(2)
        wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready = cls.saved
        cls.tmp.cleanup()

    def setUp(self):
        app_server.save_config(dict(app_server.DEFAULT_CONFIG))
        self.ui = self.server.auth.ui_token
        self.mcp = self.server.auth.rotate()

    def request(self, method, path, body=None, token=None, headers=None):
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = "Bearer " + token
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, h)
            response = conn.getresponse()
            data = response.read()
            return response.status, data
        finally:
            conn.close()


class LocalAuthTests(AuthFixture, unittest.TestCase):
    def test_missing_invalid_and_query_credentials_cannot_change_or_read_config(self):
        for token in (None, "invalid"):
            for method, path, body in (("POST", "/api/config", {"mcp_allow_logs": True}),
                                       ("POST", "/api/start", {}),
                                       ("GET", "/api/config", None),
                                       ("GET", "/api/hotwords", None)):
                with self.subTest(method=method, path=path, supplied=token is not None):
                    self.assertEqual(self.request(method, path, body, token)[0], 401)
        self.assertEqual(self.request("POST", "/api/config?ui_session=" + self.ui,
                                     {"mcp_allow_logs": True})[0], 401)
        self.assertIs(app_server.load_config()["mcp_allow_logs"], False)

    def test_mcp_cannot_escalate_or_change_security_or_start_recording(self):
        for path, body in (("/api/config", {"mcp_allow_logs": True}),
                           ("/api/config", {"port": 12345}),
                           ("/api/config", {"preset": "standard", "save_log": False}),
                           ("/api/auth/mcp", {"action": "rotate"}),
                           ("/api/auth/mcp", {"action": "revoke"}),
                           ("/api/start", {}), ("/api/migrate", {}),
                           ("/api/words/export", {})):
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, body, self.mcp)[0], 403)
        self.assertEqual(self.request("GET", "/api/auth/mcp", token=self.mcp)[0], 403)
        self.assertIs(app_server.load_config()["mcp_allow_logs"], False)
        self.assertTrue(self.server.auth.role(self.mcp) == "mcp")
        self.assertEqual(self.request("POST", "/api/config", {"preset": "standard"}, self.mcp)[0], 200)

    def test_logs_require_both_authentication_and_ui_permission(self):
        with patch.object(app_server, "recent_logs", return_value=[]) as read:
            self.assertEqual(self.request("GET", "/api/logs/recent")[0], 401)
            self.assertEqual(self.request("GET", "/api/logs/recent", token=self.mcp)[0], 403)
            read.assert_not_called()
            self.assertEqual(self.request("POST", "/api/config", {"mcp_allow_logs": True}, self.ui)[0], 200)
            self.assertEqual(self.request("GET", "/api/logs/recent", token=self.mcp)[0], 200)
            self.assertEqual(self.request("POST", "/api/config", {"mcp_allow_logs": False}, self.ui)[0], 200)
            self.assertEqual(self.request("GET", "/api/logs/recent", token=self.mcp)[0], 403)
            self.assertEqual(read.call_count, 1)

    def test_credential_rotation_revocation_and_restart(self):
        status, data = self.request("POST", "/api/auth/mcp", {"action": "rotate"}, self.ui)
        self.assertEqual(status, 200)
        fresh = json.loads(data)["token"]
        self.assertTrue(self.server.auth.role(self.mcp) is None)
        persisted = Path(self.server.auth.path).read_text(encoding="utf-8")
        self.assertFalse(fresh in persisted or self.ui in persisted)
        restarted = local_auth.LocalAuth(self.server.auth.path)
        self.assertTrue(restarted.role(fresh) == "mcp")
        self.assertTrue(restarted.role(self.ui) is None)
        self.assertEqual(self.request("POST", "/api/auth/mcp", {"action": "revoke"}, self.ui)[0], 200)
        self.assertTrue(self.server.auth.role(fresh) is None)
        self.assertFalse(local_auth.LocalAuth(self.server.auth.path).enabled())

    def test_migration_cannot_import_another_installations_credential(self):
        import migrate
        with tempfile.TemporaryDirectory() as old:
            data = Path(old)/"data"
            data.mkdir()
            (data/"config.json").write_text('{}', encoding="utf-8")
            foreign = local_auth.LocalAuth(str(data/"api_auth.json"))
            foreign_token = foreign.rotate()
            current = Path(self.tmp.name)/"migration-test"
            current.mkdir()
            local = local_auth.LocalAuth(str(current/"api_auth.json"))
            local_token = local.rotate()
            migrate.copy_data(old, str(current))
            migrated = local_auth.LocalAuth(str(current/"api_auth.json"))
            self.assertTrue(migrated.role(local_token) == "mcp")
            self.assertTrue(migrated.role(foreign_token) is None)

    def test_tokens_are_never_served_in_public_html_config_or_status(self):
        for path, token in (("/ui/settings", None), ("/ui/auth.js", None),
                            ("/api/auth/mcp", self.ui), ("/api/config", self.ui),
                            ("/api/config", self.mcp), ("/api/style", None)):
            status, data = self.request("GET", path, token=token)
            self.assertEqual(status, 200)
            self.assertFalse(self.ui.encode() in data or self.mcp.encode() in data)
        self.assertEqual(self.request("GET", "/ui/api_auth.json")[0], 404)

    def test_valid_credentials_do_not_bypass_origin_guard(self):
        self.assertEqual(self.request("POST", "/api/config", {"theme": "dark"}, self.ui,
                                     {"Origin": "https://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/config", {"theme": "dark"}, self.mcp,
                                     {"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_config_types_ranges_and_atomic_denial(self):
        original = app_server.load_config()
        for body in ([], {"unknown": True}, {"save_log": "false"}, {"interval": float("nan")},
                     {"max_utt": float("inf")}, {"silence_ms": -1}, {"port": 0},
                     {"device": []}, {"translate_langs": [1]}, {"min_speech_ms": 1},
                     {"theme": "dark", "mcp_allow_logs": 1}):
            with self.subTest(body=str(body)):
                self.assertEqual(self.request("POST", "/api/config", body, self.ui)[0], 400)
                self.assertEqual(app_server.load_config(), original)

    def test_mcp_cannot_remove_banned_words_or_change_mask(self):
        wordstore.save_banned(["秘密"])
        self.assertEqual(self.request("POST", "/api/banned", {"words": []}, self.mcp)[0], 403)
        self.assertEqual(self.request("POST", "/api/banned", {"words": ["秘密"], "mask_char": ""}, self.mcp)[0], 403)
        self.assertEqual(self.request("POST", "/api/banned", {"words": ["秘密", "追加"]}, self.mcp)[0], 200)
        self.assertEqual(wordstore.load_banned(), ["秘密", "追加"])

    def test_obs_still_connects_without_credentials_but_ui_sse_does_not(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/api/style")[0], 200)
        self.assertEqual(self.request("GET", "/events")[0], 401)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/events?overlay=1")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.readline().startswith(b"data: "))
        finally:
            conn.close()


@unittest.skipIf(sync_playwright is None, "playwright unavailable")
class AuthBrowserTests(AuthFixture, unittest.TestCase):
    def test_bootstrap_reload_frames_and_token_controls_without_manual_headers(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                for variant in ("new", "legacy"):
                    app_server.save_config({**app_server.DEFAULT_CONFIG, "ui_variant": variant})
                    page = browser.new_page()
                    try:
                        base = f"http://127.0.0.1:{self.port}"
                        page.goto(base + "/ui/settings?section=ai#mojicast_session=" + self.ui)
                        page.wait_for_function("() => document.getElementById('device').options.length > 0")
                        self.assertFalse(self.ui in page.url)
                        opened = page.evaluate("""() => new Promise(resolve => {
                            const es = new EventSource('/events');
                            es.onopen = () => { es.close(); resolve(true); };
                            es.onerror = () => { es.close(); resolve(false); };
                        })""")
                        self.assertTrue(opened)
                        page.click("#mcpIssue")
                        page.wait_for_function("() => document.getElementById('mcpToken').value.length > 30")
                        issued = page.input_value("#mcpToken")
                        self.assertTrue(self.server.auth.role(issued) == "mcp")
                        page.reload()
                        page.wait_for_function("() => document.getElementById('device').options.length > 0")
                        self.assertEqual(page.input_value("#mcpToken"), "")
                        page.click("#mcpRevoke")
                        page.wait_for_function("() => document.getElementById('mcpRevoke').disabled")
                        self.assertTrue(self.server.auth.role(issued) is None)
                        # Same-origin iframe inherits sessionStorage and authenticates itself.
                        page.evaluate("""() => {const f=document.createElement('iframe');f.id='authTestFrame';f.src='/ui/words';document.body.append(f)}""")
                        frame = page.frame_locator("#authTestFrame")
                        frame.locator("#fxTable").wait_for(state="attached")
                        response = page.frames[-1].evaluate("async () => (await fetch('/api/config')).status")
                        self.assertEqual(response, 200)
                    finally:
                        page.close()
            finally:
                browser.close()
