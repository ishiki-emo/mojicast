"""字幕デザイン（presets）・レイアウト（boxes）の項目検証

- 許可リスト・型・色・フォント名・数値の範囲を確かめる（style_schema）
- 保存（POST）は不正なら 400、読み込み・取り込みは不正な項目だけを捨てる
- 既存の利用者のデータが消えないこと: 同梱の既定・画面が書き出す項目・
  AI 連携の項目と範囲がすべて通ること、読み込みでファイルを書き換えないこと
"""
import http.client
import json
import os
import re
import tempfile
import threading
import unittest

import app_server
import style_schema as ss
import wordstore

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class CompatibilityTests(unittest.TestCase):
    """今あるデータ・画面・AI 連携が作る値を落とさないこと"""

    def test_bundled_defaults_are_valid(self):
        for fname, key, kind in (("presets.json", "presets", "preset"),
                                 ("boxes.json", "boxes", "box")):
            items = json.loads(_read(os.path.join("defaults", fname)))[key]
            self.assertEqual(ss.validate_items(kind, items), items)
            self.assertEqual(ss.sanitize_items(kind, items), items)

    def _assigned(self, src, var):
        names = set(re.findall(r"\b" + var + r"\.([A-Za-z]+)\s*=[^=]", src))
        return names - {"className", "textContent", "innerHTML", "onclick", "disabled"}

    def test_every_field_the_editors_write_is_allowed(self):
        for rel in ("ui/style.html", "ui/legacy/style.html"):
            src = _read(rel)
            self.assertLessEqual(self._assigned(src, "p"), set(ss.PRESET_FIELDS), rel)
            self.assertLessEqual(self._assigned(src, "b"), set(ss.BOX_FIELDS), rel)
        # 訳文の書式は readTrFields() の返り値で書く（Object.assign(p, tr) / trStyles[言語]）
        m = re.search(r"function readTrFields\(\) \{\s*return \{(.*?)\};", _read("ui/style.html"), re.S)
        tr = set(re.findall(r"(\w+):", m.group(1)))
        self.assertEqual(tr, set(ss.TR_FIELDS))
        # 3D の傾き（collectTilt が FX.TILT_KEYS を書く）
        keys = re.search(r"FX\.TILT_KEYS = \[(.*?)\]", _read("ui/fx.js")).group(1)
        self.assertLessEqual(set(re.findall(r'"(\w+)"', keys)), set(ss.BOX_FIELDS))

    def test_every_field_the_overlay_reads_is_allowed(self):
        # fx.js / overlay.html が box.xxx / preset.xxx で読む項目
        src = _read("ui/fx.js") + _read("overlay.html")
        for var, fields in (("box", ss.BOX_FIELDS), ("preset", ss.PRESET_FIELDS)):
            read = set(re.findall(r"\b" + var + r"\??\.([a-z][A-Za-z]+)\b", src))
            read -= {"style", "querySelector", "appendChild", "classList", "dataset"}
            self.assertLessEqual(read, set(fields), var)

    def test_slider_ranges_of_the_editor_are_allowed(self):
        # 画面のスライダーの最小・最大が検証を通ること（保存が 400 にならない）
        alias = {"f_spacing": "letterSpacing", "f_partial": "partialOpacity"}
        for rel in ("ui/style.html", "ui/legacy/style.html"):
            for m in re.finditer(r'id="([fb])_(\w+)"([^>]*)', _read(rel)):
                attrs = m.group(3)
                lo = re.search(r'\bmin="([-\d.]+)"', attrs)
                hi = re.search(r'\bmax="([-\d.]+)"', attrs)
                if not (lo and hi):
                    continue
                key = alias.get(f"{m.group(1)}_{m.group(2)}", m.group(2))
                fields = ss.PRESET_FIELDS if m.group(1) == "f" else ss.BOX_FIELDS
                self.assertIn(key, fields, f"{rel}: {key}")
                for v in (float(lo.group(1)), float(hi.group(1))):
                    self.assertTrue(fields[key](v), f"{rel}: {key}={v}")

    def test_ai_connection_fields_and_ranges_are_allowed(self):
        # mcp パッケージが無くても読めるよう、test_mcp_server と同じ読み込み方をする
        from tests.test_mcp_server import _load_mcp_module
        m = _load_mcp_module(0)
        for fields, schema in ((m.DESIGN_FIELDS, ss.PRESET_FIELDS),
                               (m.LAYOUT_FIELDS, ss.BOX_FIELDS)):
            for key, (kind, lo, hi, _) in fields.items():
                if key == "tilt":                  # tiltX/Y/Z・perspective に展開される名前
                    continue
                self.assertIn(key, schema)
                if kind in ("num", "int"):
                    self.assertTrue(schema[key](lo) and schema[key](hi), key)
                if kind == "enum":
                    for v in hi:
                        self.assertTrue(schema[key](v), f"{key}={v}")
        self.assertLessEqual(m.LAYOUT_ADVANCED, set(ss.BOX_FIELDS))
        for v in m.TILT_PRESETS.values():
            for k, x in v.items():
                self.assertTrue(ss.BOX_FIELDS[k](x), k)


class ValidateTests(unittest.TestCase):
    BASE_P = {"id": "p1", "name": "テスト", "font": "'BIZ UDPGothic', sans-serif",
              "size": 40, "color": "#ffffff", "glow": "", "animIn": "pop"}
    BASE_B = {"id": "b1", "name": "枠", "x": 5, "y": 70.5, "w": 90, "h": 25,
              "mode": "flow", "bg": "#000000", "bubbleSpots": [{"x": 10, "y": 20}]}

    def test_valid_items_pass_unchanged(self):
        self.assertEqual(ss.validate_item("preset", self.BASE_P), self.BASE_P)
        self.assertEqual(ss.validate_item("box", self.BASE_B), self.BASE_B)
        # 旧UIは選択肢に無いモード（吹き出し）を開くと mode="" で保存する（従来どおり通す）
        self.assertEqual(ss.validate_item("box", dict(self.BASE_B, mode="")),
                         dict(self.BASE_B, mode=""))
        p = dict(self.BASE_P, trStyles={"en": {"enScale": 0.8, "enColor": ""},
                                        "zh_hk": {"enFont": "'Noto Sans HK'"}})
        self.assertEqual(ss.validate_item("preset", p), p)

    BAD_PRESET = [
        ("onload", "x"),                              # 許可リスト外
        ("color", "red"), ("color", "#fff"), ("color", "#ffffff;background:url(x)"),
        ("hotColor", None),
        ("font", "x}body{background:url(//e)"), ("font", "a;b"), ("font", "url(x)"),
        ("font", "<script>"), ("font", "x" * 513), ("font", 12),
        ("size", "40"), ("size", True), ("size", float("nan")), ("size", float("inf")),
        ("size", 0), ("size", 10000), ("partialOpacity", 1.5), ("letterSpacing", -5),
        ("animIn", "Pop"), ("animIn", "pop;x"), ("animIn", " "), ("animIn", "x" * 41),
        ("shadow", 1), ("shadow", "true"),
        ("trStyles", []), ("trStyles", {"EN": {}}), ("trStyles", {"en": {"color": "#000000"}}),
        ("trStyles", {"en": {"enColor": "blue"}}), ("trStyles", {"en": "x"}),
        ("name", ""), ("name", None), ("name", "a\nb"), ("id", ""), ("id", 3),
        ("desc", "x" * 2001),
    ]
    BAD_BOX = [
        ("x", -101), ("w", 201), ("x", "5"), ("bg", "transparent"),
        ("bubbleSpots", [{"x": 1, "y": 2, "z": 3}]), ("bubbleSpots", [{"x": "1"}]),
        ("bubbleSpots", {"x": 1}), ("bubbleSpots", [{"x": 1}] * 51),
        ("mode", "flow bubble"), ("perspective", 10), ("tiltY", 120), ("font", "x"),
    ]

    def test_invalid_values_are_rejected_on_save(self):
        for kind, base, cases in (("preset", self.BASE_P, self.BAD_PRESET),
                                  ("box", self.BASE_B, self.BAD_BOX)):
            for key, value in cases:
                with self.assertRaises(ValueError, msg=f"{kind}.{key}={value!r}"):
                    ss.validate_item(kind, dict(base, **{key: value}))

    def test_invalid_values_are_dropped_on_load(self):
        for kind, base, cases in (("preset", self.BASE_P, self.BAD_PRESET),
                                  ("box", self.BASE_B, self.BAD_BOX)):
            for key, value in cases:
                if key in ("id", "name"):
                    self.assertIsNone(ss.sanitize_item(kind, dict(base, **{key: value})))
                    continue
                clean = ss.sanitize_item(kind, dict(base, **{key: value}))
                want = {k: v for k, v in base.items() if k != key}
                if key == "trStyles" and isinstance(value, dict):
                    want["trStyles"] = clean.get("trStyles")   # 言語ごとに中身だけ捨てる
                    self.assertNotIn("color", json.dumps(clean["trStyles"]))
                    self.assertNotIn("blue", json.dumps(clean["trStyles"]))
                self.assertEqual(clean, want, f"{kind}.{key}={value!r}")

    def test_tr_styles_keep_valid_languages_and_fields(self):
        p = dict(self.BASE_P, trStyles={"en": {"enScale": 0.8, "enColor": "javascript:x"},
                                        "BAD": {"enScale": 0.5}, "ko": {"enDeco": True}})
        self.assertEqual(ss.sanitize_item("preset", p)["trStyles"],
                         {"en": {"enScale": 0.8}, "ko": {"enDeco": True}})

    def test_list_level_rules(self):
        with self.assertRaises(ValueError):
            ss.validate_items("preset", [])
        with self.assertRaises(ValueError):
            ss.validate_items("preset", {"id": "p"})
        with self.assertRaises(ValueError):
            ss.validate_items("box", [self.BASE_B] * (ss.MAX_ITEMS + 1))
        # 既に id が重複している人の保存を止めない（従来どおり）
        self.assertEqual(len(ss.validate_items("box", [self.BASE_B, self.BASE_B])), 2)
        self.assertEqual(ss.sanitize_items("preset", "x"), [])
        self.assertEqual(ss.sanitize_items("preset", [1, None, self.BASE_P, {"id": "x"}]),
                         [self.BASE_P])


class ServerTests(unittest.TestCase):
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

    def setUp(self):
        # 毎回、同梱の既定に戻す
        for fname in ("presets.json", "boxes.json"):
            with open(os.path.join(ROOT, "defaults", fname), encoding="utf-8") as f:
                data = f.read()
            with open(wordstore.data_path(fname), "w", encoding="utf-8") as f:
                f.write(data)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": "Bearer " + self.server.auth.ui_token}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"))
        conn.close()
        return out

    def _file(self, fname):
        with open(wordstore.data_path(fname), "rb") as f:
            return f.read()

    def test_editor_roundtrip_is_accepted(self):
        # 画面と同じく GET → そのまま POST が通る
        for key in ("presets", "boxes"):
            status, data = self.call("GET", f"/api/{key}")
            self.assertEqual(status, 200)
            status, res = self.call("POST", f"/api/{key}", {key: data[key]})
            self.assertEqual((status, res.get("ok")), (200, True), res)

    def test_invalid_save_is_rejected_and_file_untouched(self):
        before = self._file("presets.json")
        _, data = self.call("GET", "/api/presets")
        bad = data["presets"]
        bad[0] = dict(bad[0], font="x}body{background:red")
        status, res = self.call("POST", "/api/presets", {"presets": bad})
        self.assertEqual(status, 400)
        self.assertIn("font", res.get("detail", ""))
        self.assertEqual(self._file("presets.json"), before)
        before = self._file("boxes.json")
        _, data = self.call("GET", "/api/boxes")
        status, _ = self.call("POST", "/api/boxes",
                              {"boxes": [dict(data["boxes"][0], onclick="x")]})
        self.assertEqual(status, 400)
        self.assertEqual(self._file("boxes.json"), before)
        # 従来どおり: 空の一覧・id/name 欠けは 400
        self.assertEqual(self.call("POST", "/api/boxes", {"boxes": []})[0], 400)
        self.assertEqual(self.call("POST", "/api/boxes", {"boxes": [{"id": "x"}]})[0], 400)

    def test_load_ignores_bad_fields_without_rewriting_file(self):
        presets = json.loads(self._file("presets.json"))["presets"]
        presets[0]["color"] = "red;}</style><script>x</script>"
        presets[0]["legacyKey"] = 1
        presets.append({"id": "broken"})               # name が無い件は丸ごと除く
        raw = json.dumps({"presets": presets}, ensure_ascii=False).encode("utf-8")
        with open(wordstore.data_path("presets.json"), "wb") as f:
            f.write(raw)
        status, data = self.call("GET", "/api/presets")
        self.assertEqual(status, 200)
        first = data["presets"][0]
        self.assertNotIn("color", first)
        self.assertNotIn("legacyKey", first)
        self.assertEqual(first["name"], presets[0]["name"])
        self.assertEqual(len(data["presets"]), len(presets) - 1)
        # 字幕に送る style 一式にも混ざらない
        cfg = app_server.load_config()
        cfg["preset"] = presets[0]["id"]
        style = app_server.resolve_style(cfg)
        self.assertEqual(style["style"]["name"], presets[0]["name"])
        self.assertNotIn("script", json.dumps(style, ensure_ascii=False))
        # ファイルはそのまま
        self.assertEqual(self._file("presets.json"), raw)

    def test_mojipack_import_drops_bad_fields_and_keeps_existing(self):
        raw = json.loads(self._file("boxes.json"))
        raw["boxes"][0]["legacyKey"] = "残す"           # 既存側の未知の項目は書き換えない
        with open(wordstore.data_path("boxes.json"), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        pack = {"mojipack": 1,
                "presets": [{"id": "x", "name": "共有デザイン", "size": 44,
                             "font": "a;}*{display:none", "color": "#123456",
                             "onload": "alert(1)"}],
                "boxes": [{"id": "y", "name": "共有枠", "x": 5, "bg": "url(x)",
                           "mode": "bubble"}]}
        res, err = app_server.import_mojipack(pack)
        self.assertIsNone(err)
        self.assertEqual(res, {"presets": 1, "boxes": 1})
        p = json.loads(self._file("presets.json"))["presets"][-1]
        self.assertEqual({k: p[k] for k in ("name", "size", "color")},
                         {"name": "共有デザイン", "size": 44, "color": "#123456"})
        self.assertNotIn("font", p)
        self.assertNotIn("onload", p)
        self.assertTrue(p["id"].startswith("imp-p-"))
        boxes = json.loads(self._file("boxes.json"))["boxes"]
        self.assertEqual(boxes[0]["legacyKey"], "残す")
        self.assertNotIn("bg", boxes[-1])
        self.assertEqual(boxes[-1]["mode"], "bubble")


if __name__ == "__main__":
    unittest.main()
