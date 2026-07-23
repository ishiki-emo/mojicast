import http.client
import json
import queue
import threading
import unittest

import app_server


class LocalOriginGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app_server._QuietHTTPServer(
            ("127.0.0.1", 0), app_server.Handler
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, *, host=None, origin=None,
                fetch_site=None, body=None):
        headers = {}
        if host is not None:
            headers["Host"] = host
        if origin is not None:
            headers["Origin"] = origin
        if fetch_site is not None:
            headers["Sec-Fetch-Site"] = fetch_site
        if body is not None:
            headers["Content-Type"] = "text/plain"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request(method, path, body=body, headers=headers)
        return conn, conn.getresponse()

    def test_localhost_origin_is_allowed_for_post(self):
        origin = f"http://localhost:{self.port}"
        conn, response = self.request(
            "POST", "/api/clear",
            host=f"localhost:{self.port}",
            origin=origin,
            body="{}",
        )
        try:
            self.assertEqual(response.status, 200)
        finally:
            response.read()
            conn.close()

    def test_127_origin_is_allowed_for_post(self):
        origin = f"http://127.0.0.1:{self.port}"
        conn, response = self.request(
            "POST", "/api/clear",
            host=f"127.0.0.1:{self.port}",
            origin=origin,
            body="{}",
        )
        try:
            self.assertEqual(response.status, 200)
        finally:
            response.read()
            conn.close()

    def test_originless_local_client_is_allowed(self):
        conn, response = self.request(
            "POST", "/api/clear",
            host=f"127.0.0.1:{self.port}",
            body="{}",
        )
        try:
            self.assertEqual(response.status, 200)
        finally:
            response.read()
            conn.close()

    def test_local_sse_is_allowed_without_cors(self):
        origin = f"http://localhost:{self.port}"
        conn, response = self.request(
            "GET", "/events",
            host=f"localhost:{self.port}",
            origin=origin,
        )
        try:
            self.assertEqual(response.status, 200)
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
            self.assertEqual(
                response.getheader("Cross-Origin-Resource-Policy"),
                "same-origin",
            )
        finally:
            conn.close()

    def test_local_engine_start_reaches_engine(self):
        class FakeEngine:
            def __init__(self):
                self.started = False

            def start(self, _cfg):
                self.started = True

        fake = FakeEngine()
        previous = app_server._engine
        app_server._engine = fake
        try:
            origin = f"http://127.0.0.1:{self.port}"
            conn, response = self.request(
                "POST", "/api/engine",
                host=f"127.0.0.1:{self.port}",
                origin=origin,
                body='{"action":"start"}',
            )
            try:
                self.assertEqual(response.status, 200)
                self.assertTrue(fake.started)
            finally:
                response.read()
                conn.close()
        finally:
            app_server._engine = previous

    def test_external_origin_is_rejected_for_sse(self):
        conn, response = self.request(
            "GET", "/events",
            host=f"127.0.0.1:{self.port}",
            origin="https://example.invalid",
        )
        try:
            self.assertEqual(response.status, 403)
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        finally:
            response.read()
            conn.close()

    def test_external_origin_is_rejected_for_post(self):
        conn, response = self.request(
            "POST", "/api/clear",
            host=f"127.0.0.1:{self.port}",
            origin="https://example.invalid",
            body="{}",
        )
        try:
            self.assertEqual(response.status, 403)
        finally:
            response.read()
            conn.close()

    def test_cross_site_post_without_origin_is_rejected(self):
        conn, response = self.request(
            "POST", "/api/clear",
            host=f"127.0.0.1:{self.port}",
            fetch_site="cross-site",
            body="{}",
        )
        try:
            self.assertEqual(response.status, 403)
        finally:
            response.read()
            conn.close()

    def test_dns_rebinding_host_is_rejected(self):
        conn, response = self.request(
            "GET", "/api/status",
            host=f"attacker.invalid:{self.port}",
        )
        try:
            self.assertEqual(response.status, 421)
        finally:
            response.read()
            conn.close()

    def test_oversized_request_is_rejected_before_body_read(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.putrequest("POST", "/api/clear", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{self.port}")
        conn.putheader("Origin", f"http://127.0.0.1:{self.port}")
        conn.putheader("Content-Type", "application/json")
        conn.putheader(
            "Content-Length", str(app_server.MAX_REQUEST_BODY_BYTES + 1)
        )
        conn.endheaders()
        response = conn.getresponse()
        try:
            self.assertEqual(response.status, 413)
        finally:
            response.read()
            conn.close()

    def test_sse_client_count_has_a_high_but_finite_limit(self):
        with app_server._clients_lock:
            original = list(app_server._clients)
            app_server._clients[:] = [
                queue.Queue() for _ in range(app_server.MAX_SSE_CLIENTS)
            ]
        try:
            conn, response = self.request(
                "GET", "/events",
                host=f"127.0.0.1:{self.port}",
                origin=f"http://127.0.0.1:{self.port}",
            )
            try:
                self.assertEqual(response.status, 503)
            finally:
                response.read()
                conn.close()
        finally:
            with app_server._clients_lock:
                app_server._clients[:] = original

    def test_sse_overflow_recovers_to_latest_event(self):
        q = queue.Queue(maxsize=4)
        for value in ("old-0", "old-1", "old-2", "old-3"):
            q.put_nowait(value)
        previous_recover = app_server.SSE_QUEUE_RECOVER_ITEMS
        app_server.SSE_QUEUE_RECOVER_ITEMS = 1
        with app_server._clients_lock:
            app_server._clients.append(q)
        try:
            app_server.broadcast({"type": "state", "state": "latest"})
        finally:
            with app_server._clients_lock:
                if q in app_server._clients:
                    app_server._clients.remove(q)
            app_server.SSE_QUEUE_RECOVER_ITEMS = previous_recover

        values = []
        while not q.empty():
            values.append(q.get_nowait())
        self.assertEqual(values[0], "old-3")
        self.assertEqual(json.loads(values[1])["state"], "latest")


if __name__ == "__main__":
    unittest.main()
