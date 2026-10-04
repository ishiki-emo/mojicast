"""以前の Mojicast（別フォルダの Zip 版）からの引き継ぎ（migrate.py と /api/migrate）"""
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

import migrate


def make_old(root, name="Mojicast", models=True, config=None):
    """旧 Zip 版のフォルダを作る（Mojicast.exe・data\\・models\\）"""
    folder = os.path.join(root, name)
    os.makedirs(os.path.join(folder, "data", "profiles", "game"))
    open(os.path.join(folder, "Mojicast.exe"), "w").close()
    with open(os.path.join(folder, "data", "config.json"), "w", encoding="utf-8") as f:
        json.dump(config or {"self_name": "OLD", "translate_lang": "ko"}, f)
    with open(os.path.join(folder, "data", "presets.json"), "w", encoding="utf-8") as f:
        json.dump({"presets": [{"id": "mine", "name": "自作"}]}, f)
    with open(os.path.join(folder, "data", "hotwords.txt"), "w", encoding="utf-8") as f:
        f.write("昇龍拳 しょうりゅうけん\n")
    with open(os.path.join(folder, "data", "profiles", "game", "hotwords.txt"), "w",
              encoding="utf-8") as f:
        f.write("波動拳\n")
    open(os.path.join(folder, "data", "_hotwords_gen.txt"), "w").close()
    if models:
        os.makedirs(os.path.join(folder, "models", "hub"))
        with open(os.path.join(folder, "models", "hub", "model.bin"), "wb") as f:
            f.write(b"x" * 3 * 1048576)
    return folder


class InspectAndFindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.current = os.path.join(self.tmp, "Programs", "Mojicast")
        os.makedirs(self.current)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inspect_accepts_the_exe_folder_and_reports_model_size(self):
        old = make_old(self.tmp)
        r = migrate.inspect(old, self.current)
        self.assertTrue(r["ok"])
        self.assertEqual(r["path"], old)
        self.assertEqual(r["models_mb"], 3)

    def test_inspect_accepts_the_data_folder_itself(self):
        old = make_old(self.tmp)
        self.assertEqual(migrate.inspect(os.path.join(old, "data"), self.current)["path"], old)

    def test_inspect_rejects_folders_without_settings_and_the_current_one(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertEqual(migrate.inspect(empty, self.current)["error"], "no_data")
        self.assertEqual(migrate.inspect(os.path.join(self.tmp, "nope"), self.current)["error"],
                         "not_found")
        make_old(self.tmp, "Programs/Mojicast2")
        os.makedirs(os.path.join(self.current, "data"), exist_ok=True)
        open(os.path.join(self.current, "data", "config.json"), "w").close()
        self.assertEqual(migrate.inspect(self.current, self.current)["error"], "same_folder")

    def test_find_candidates_looks_two_levels_deep_and_skips_the_current(self):
        desktop = os.path.join(self.tmp, "Desktop")
        downloads = os.path.join(self.tmp, "Downloads")
        a = make_old(desktop, "Mojicast")
        b = make_old(os.path.join(downloads, "Mojicast-v0.9.8-win-x64"), "Mojicast")
        make_old(os.path.join(downloads, "a", "b"), "TooDeep")
        found = migrate.find_candidates(self.current, roots=[desktop, downloads])
        self.assertEqual(sorted(found), sorted([a, b]))
        self.assertEqual(migrate.find_candidates(a, roots=[desktop]), [])


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = make_old(self.tmp)
        self.data = os.path.join(self.tmp, "new", "data")
        os.makedirs(self.data)
        with open(os.path.join(self.data, "presets.json"), "w", encoding="utf-8") as f:
            json.dump({"presets": [{"id": "standard", "name": "初期"}]}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_data_is_replaced_without_generated_files(self):
        migrate.copy_data(self.old, self.data)
        with open(os.path.join(self.data, "presets.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["presets"][0]["id"], "mine")
        self.assertTrue(os.path.isfile(os.path.join(self.data, "profiles", "game", "hotwords.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.data, "_hotwords_gen.txt")))
        leftovers = [n for n in os.listdir(os.path.dirname(self.data)) if "before-migrate" in n]
        self.assertEqual(leftovers, [])

    def test_failed_copy_restores_the_current_data(self):
        real_copytree = migrate.shutil.copytree
        def flaky(src, dst, **kw):          # 旧フォルダからの写しだけ失敗させる
            if os.path.normcase(src).startswith(os.path.normcase(self.old)):
                raise OSError("disk full")
            return real_copytree(src, dst, **kw)
        with mock.patch.object(migrate.shutil, "copytree", side_effect=flaky):
            with self.assertRaises(OSError):
                migrate.copy_data(self.old, self.data)
        with open(os.path.join(self.data, "presets.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["presets"][0]["id"], "standard")

    def test_job_copies_models_and_skips_files_already_there(self):
        models = os.path.join(self.tmp, "new", "models")
        done = threading.Event()
        job = migrate.Job()
        self.assertTrue(job.start(self.old, True, self.data, models, done.set))
        self.assertTrue(done.wait(10))
        for _ in range(50):
            if not job.snapshot()["running"]:
                break
            time.sleep(0.05)
        st = job.snapshot()
        self.assertTrue(st["done"], st)
        self.assertEqual(st["copied_mb"], 3)
        self.assertEqual(os.path.getsize(os.path.join(models, "hub", "model.bin")), 3 * 1048576)
        # 2回目は同じ大きさのファイルを飛ばす
        job2 = migrate.Job()
        done2 = threading.Event()
        job2.start(self.old, True, self.data, models, done2.set)
        self.assertTrue(done2.wait(10))
        time.sleep(0.1)
        self.assertEqual(job2.snapshot()["total_mb"], 0)


class ServerMigrateTests(unittest.TestCase):
    """/api/migrate を、data/ と models/ を一時フォルダへ向けて通す"""

    @classmethod
    def setUpClass(cls):
        import app_server
        import wordstore
        cls.app, cls.ws = app_server, wordstore
        cls._orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready,
                     app_server.DATA_BASE)
        cls.tmp = tempfile.mkdtemp()
        cls.base = os.path.join(cls.tmp, "Programs", "Mojicast")
        wordstore.DATA = os.path.join(cls.base, "data")
        wordstore.PROFILES_DIR = os.path.join(wordstore.DATA, "profiles")
        wordstore._ready = False
        app_server.DATA_BASE = cls.base
        cls.server = app_server._QuietHTTPServer(("127.0.0.1", 0), app_server.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        (cls.ws.DATA, cls.ws.PROFILES_DIR, cls.ws._ready, cls.app.DATA_BASE) = cls._orig
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        shutil.rmtree(self.base, ignore_errors=True)
        self.ws._ready = False
        self.ws.ensure_data()
        self.assertTrue(self.ws.fresh_install)
        self.app.save_config({"migrate_offer": "pending"})
        self.app._migrate_job = migrate.Job()
        self.old = make_old(os.path.join(self.tmp, "Desktop"))

    def tearDown(self):
        shutil.rmtree(os.path.join(self.tmp, "Desktop"), ignore_errors=True)

    def _req(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}"})
        res = conn.getresponse()
        data = json.loads(res.read())
        conn.close()
        return res.status, data

    def test_offer_lists_candidates_and_dismiss_hides_it(self):
        with mock.patch.object(migrate, "find_candidates", return_value=[self.old]):
            st, d = self._req("GET", "/api/migrate")
        self.assertTrue(d["offer"])
        self.assertEqual(d["candidates"], [self.old])
        self._req("POST", "/api/migrate", {"action": "dismiss"})
        st, d = self._req("GET", "/api/migrate")
        self.assertFalse(d["offer"])

    def test_start_is_refused_while_captions_are_running(self):
        with mock.patch.dict(self.app._engine_state, {"state": "running"}):
            st, d = self._req("POST", "/api/migrate",
                              {"action": "start", "path": self.old, "models": False})
        self.assertEqual((st, d["error"]), (409, "engine_running"))

    def test_start_replaces_settings_and_marks_done(self):
        st, d = self._req("POST", "/api/migrate",
                          {"action": "start", "path": self.old, "models": True})
        self.assertEqual(st, 200, d)
        for _ in range(100):
            st, d = self._req("GET", "/api/migrate?job=1")
            if not d["job"]["running"]:
                break
            time.sleep(0.05)
        self.assertTrue(d["job"]["done"], d)
        self.assertFalse(d["offer"])
        cfg = self.app.load_config()
        self.assertEqual((cfg["self_name"], cfg["migrate_offer"]), ("OLD", "done"))
        presets = self.app._read_json(self.app._presets_path(), {"presets": []})["presets"]
        ids = [p["id"] for p in presets]
        self.assertIn("mine", ids)              # 自作のデザインが来ている
        self.assertGreater(len(ids), 1)          # 新しい版の既定デザインも足されている
        self.assertTrue(os.path.isfile(os.path.join(self.base, "models", "hub", "model.bin")))


if __name__ == "__main__":
    unittest.main()
