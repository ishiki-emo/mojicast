"""単語の一括取り込み（wordimport）のテスト

対象: CSV/JSON の解釈・文字コード判別・見出し行・弾く行・追加/上書きの合成・書き出し。
数千語（ゲームの技名）を Excel や LLM 出力から取り込む経路を守る。
"""
import unittest

import wordimport as w


CSV = ("表記,読み,出やすさ,英訳\n"
       "昇龍拳,しょうりゅうけん,,Shoryuken\n"
       "波動拳,はどうけん,3\n"
       "# コメント行\n"
       ",,\n"
       "竜巻旋風脚,たつまきせんぷうきゃく,x,Tatsumaki\n")


class ParseTests(unittest.TestCase):
    def test_csv_with_header(self):
        entries, skipped = w.parse_words(CSV)
        self.assertEqual([e["surface"] for e in entries], ["昇龍拳", "波動拳", "竜巻旋風脚"])
        self.assertEqual(entries[0]["en"], "Shoryuken")
        self.assertEqual(entries[1]["score"], "3")
        self.assertEqual(entries[2]["score"], "")     # 数値でない出やすさは空へ
        self.assertEqual(skipped, [])                  # 空行・コメントは弾いた行に数えない

    def test_csv_without_header_is_positional(self):
        entries, _ = w.parse_words("昇龍拳,しょうりゅうけん\n真空波動拳\n")
        self.assertEqual(entries[0]["reading"], "しょうりゅうけん")
        self.assertEqual(entries[1], {"surface": "真空波動拳", "reading": "",
                                      "score": "", "en": ""})

    def test_csv_header_reorders_columns(self):
        entries, _ = w.parse_words("surface,en,reading\n昇龍拳,Shoryuken,しょうりゅうけん\n")
        self.assertEqual(entries[0]["reading"], "しょうりゅうけん")
        self.assertEqual(entries[0]["en"], "Shoryuken")

    def test_quoted_reading_with_slash_variants(self):
        entries, _ = w.parse_words('"癒色えも","いしきえも／意識エモ"\n')
        self.assertEqual(entries[0]["reading"], "いしきえも／意識エモ")

    def test_skips_empty_surface_and_comma(self):
        entries, skipped = w.parse_words('"a,b",えー\n,よみだけ\n')
        self.assertEqual(entries, [])
        self.assertEqual({s["reason"] for s in skipped}, {"comma", "empty"})

    def test_too_long_is_skipped(self):
        entries, skipped = w.parse_words("あ" * 61 + ",あ\n")
        self.assertEqual(entries, [])
        self.assertEqual(skipped[0]["reason"], "too_long")

    def test_control_chars_are_stripped(self):
        entries, _ = w.parse_words("昇龍拳\x00,しょう\x07りゅうけん\n")
        self.assertEqual(entries[0]["surface"], "昇龍拳")
        self.assertEqual(entries[0]["reading"], "しょうりゅうけん")

    def test_json_words_array(self):
        entries, _ = w.parse_words('{"words":[{"surface":"X","reading":"えっくす","score":2.5,"en":"Ex"}]}')
        self.assertEqual(entries[0], {"surface": "X", "reading": "えっくす",
                                      "score": "2.5", "en": "Ex"})

    def test_json_bare_array(self):
        entries, _ = w.parse_words('[{"surface":"X"}]')
        self.assertEqual(entries[0]["surface"], "X")

    def test_json_mojipack_style_joins_glossary(self):
        entries, _ = w.parse_words(
            '{"hotwords":[{"surface":"A","reading":"えー"}],'
            '"glossary":[{"ja":"A","en":"Ay"},{"ja":"B","en":"Bee"}]}')
        self.assertEqual(entries[0], {"surface": "A", "reading": "えー", "score": "", "en": "Ay"})
        self.assertEqual(entries[1]["en"], "Bee")

    def test_bad_json_is_reported(self):
        entries, skipped = w.parse_words("{bad json")
        self.assertEqual(entries, [])
        self.assertEqual(skipped[0]["reason"], "json")

    def test_limit(self):
        text = "".join(f"語{i},よみ\n" for i in range(w.MAX_ENTRIES + 5))
        entries, skipped = w.parse_words(text)
        self.assertEqual(len(entries), w.MAX_ENTRIES)
        self.assertEqual(skipped[-1]["reason"], "too_many")


class DecodeTests(unittest.TestCase):
    def test_utf8_bom(self):
        self.assertEqual(w.decode_bytes(CSV.encode("utf-8-sig")), CSV)

    def test_shift_jis_from_excel(self):
        self.assertEqual(w.decode_bytes(CSV.encode("cp932")), CSV)

    def test_bom_then_header_is_still_a_header(self):
        entries, _ = w.parse_words(w.decode_bytes(CSV.encode("utf-8-sig")))
        self.assertEqual(entries[0]["surface"], "昇龍拳")


class PlanApplyTests(unittest.TestCase):
    def setUp(self):
        self.hot = [{"surface": "波動拳", "reading": "はどうけん", "score": "2.5"}]
        self.gloss = [{"ja": "波動拳", "en": "Hadoken"}]
        self.entries, _ = w.parse_words(CSV + "昇龍拳,しょうりゅうけん\n波動拳,はどうけん,,Hadouken\n")

    def test_plan_counts(self):
        stats, uniq = w.plan_import(self.entries, self.hot, self.gloss)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["dup_in_file"], 2)
        self.assertEqual(stats["exists"], 1)
        self.assertEqual(stats["new"], 2)
        self.assertEqual(stats["with_en"], 2)     # 先勝ちなので 波動拳 の英訳は付かない
        self.assertEqual(len(uniq), 3)

    def test_no_reading_counts_kanji_only(self):
        entries, _ = w.parse_words("真空波動拳\nえもてぃっく\n")
        stats, _ = w.plan_import(entries, [], [])
        self.assertEqual(stats["no_reading"], 1)

    def test_apply_add_keeps_existing(self):
        _, uniq = w.plan_import(self.entries, self.hot, self.gloss)
        hot, gl, n = w.apply_import(uniq, self.hot, self.gloss, "add")
        self.assertEqual(n, {"hotwords": 2, "glossary": 2})
        self.assertEqual(hot[0]["score"], "2.5")           # 既存は無傷
        self.assertEqual([h["surface"] for h in hot], ["波動拳", "昇龍拳", "竜巻旋風脚"])
        self.assertEqual(gl[0]["en"], "Hadoken")
        self.assertEqual(hot[1]["reading"], "しょうりゅうけん")

    def test_apply_overwrite_replaces_in_place(self):
        entries, _ = w.parse_words("波動拳,はどーけん,4,Hadouken\n")
        _, uniq = w.plan_import(entries, self.hot, self.gloss)
        hot, gl, n = w.apply_import(uniq, self.hot, self.gloss, "overwrite")
        self.assertEqual(n, {"hotwords": 1, "glossary": 1})
        self.assertEqual(hot, [{"surface": "波動拳", "reading": "はどーけん", "score": "4"}])
        self.assertEqual(gl, [{"ja": "波動拳", "en": "Hadouken"}])

    def test_apply_without_glossary(self):
        _, uniq = w.plan_import(self.entries, [], [])
        hot, gl, n = w.apply_import(uniq, [], [], "add", with_glossary=False)
        self.assertEqual(gl, [])
        self.assertEqual(n["glossary"], 0)

    def test_reading_defaults_to_surface(self):
        entries, _ = w.parse_words("えもてぃっく\n")
        hot, _, _ = w.apply_import(entries, [], [], "add")
        self.assertEqual(hot[0]["reading"], "えもてぃっく")

    def test_inputs_are_not_mutated(self):
        _, uniq = w.plan_import(self.entries, self.hot, self.gloss)
        w.apply_import(uniq, self.hot, self.gloss, "overwrite")
        self.assertEqual(self.hot[0]["score"], "2.5")
        self.assertEqual(len(self.hot), 1)


class ExportTests(unittest.TestCase):
    def test_roundtrip(self):
        hot = [{"surface": "昇龍拳", "reading": "しょうりゅうけん", "score": "3"},
               {"surface": "えもてぃっく", "reading": "えもてぃっく", "score": ""}]
        gloss = [{"ja": "昇龍拳", "en": "Shoryuken"}, {"ja": "英訳だけ", "en": "Only"}]
        text = w.to_csv(hot, gloss)
        self.assertTrue(text.startswith("\ufeff表記,読み,出やすさ,英訳\r\n"))
        entries, skipped = w.parse_words(text)
        self.assertEqual(skipped, [])
        self.assertEqual(entries[0], {"surface": "昇龍拳", "reading": "しょうりゅうけん",
                                      "score": "3", "en": "Shoryuken"})
        self.assertEqual(entries[1]["reading"], "")        # 表記と同じ読みは空欄で出す
        self.assertEqual(entries[2], {"surface": "英訳だけ", "reading": "", "score": "", "en": "Only"})


class GlossaryImportTests(unittest.TestCase):
    def test_parse_csv_glossary(self):
        text = "日本語,英訳\n星野ひかり,Hikari Hoshino\n癒色えも,ISHIKI Emo\n"
        entries, skipped = w.parse_glossary(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0], {"ja": "星野ひかり", "en": "Hikari Hoshino"})
        self.assertEqual(entries[1], {"ja": "癒色えも", "en": "ISHIKI Emo"})
        self.assertEqual(skipped, [])

    def test_parse_csv_glossary_without_header(self):
        text = "星野ひかり,Hikari Hoshino\n癒色えも,ISHIKI Emo\n"
        entries, _ = w.parse_glossary(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["ja"], "星野ひかり")

    def test_parse_json_glossary(self):
        text = '{"glossary":[{"ja":"星野ひかり","en":"Hikari Hoshino"}]}'
        entries, _ = w.parse_glossary(text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0], {"ja": "星野ひかり", "en": "Hikari Hoshino"})

    def test_parse_glossary_skips_empty(self):
        text = "星野ひかり,\n,Hikari Hoshino\n"
        entries, skipped = w.parse_glossary(text)
        self.assertEqual(entries, [])
        self.assertEqual(len(skipped), 2)
        self.assertTrue(all(s["reason"] == "empty" for s in skipped))

    def test_plan_import_glossary(self):
        existing = [{"ja": "星野ひかり", "en": "Hikari Hoshino"}]
        entries = [{"ja": "星野ひかり", "en": "Hoshino Hikari"},
                   {"ja": "癒色えも", "en": "ISHIKI Emo"},
                   {"ja": "癒色えも", "en": "Emo"}]
        stats, uniq = w.plan_import_glossary(entries, existing)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["dup_in_file"], 1)
        self.assertEqual(stats["exists"], 1)
        self.assertEqual(stats["new"], 1)

    def test_apply_import_glossary_add(self):
        existing = [{"ja": "星野ひかり", "en": "Hikari Hoshino"}]
        entries = [{"ja": "星野ひかり", "en": "Hoshino Hikari"},
                   {"ja": "癒色えも", "en": "ISHIKI Emo"}]
        gl, counts = w.apply_import_glossary(entries, existing, "add")
        self.assertEqual(counts["glossary"], 1)
        self.assertEqual(len(gl), 2)
        self.assertEqual(gl[0]["en"], "Hikari Hoshino")  # existing kept
        self.assertEqual(gl[1]["en"], "ISHIKI Emo")

    def test_apply_import_glossary_overwrite(self):
        existing = [{"ja": "星野ひかり", "en": "Hikari Hoshino"}]
        entries = [{"ja": "星野ひかり", "en": "Hoshino Hikari"}]
        gl, counts = w.apply_import_glossary(entries, existing, "overwrite")
        self.assertEqual(counts["glossary"], 1)
        self.assertEqual(len(gl), 1)
        self.assertEqual(gl[0]["en"], "Hoshino Hikari")  # overwritten

    def test_to_csv_glossary_only(self):
        gloss = [{"ja": "星野ひかり", "en": "Hikari Hoshino"},
                 {"ja": "癒色えも", "en": "ISHIKI Emo"}]
        text = w.to_csv_glossary_only(gloss)
        self.assertTrue(text.startswith("\ufeff日本語,英訳\r\n"))
        self.assertIn("星野ひかり,Hikari Hoshino\r\n", text)
        self.assertIn("癒色えも,ISHIKI Emo\r\n", text)

    def test_glossary_roundtrip(self):
        gloss = [{"ja": "星野ひかり", "en": "Hikari Hoshino"},
                 {"ja": "癒色えも", "en": "ISHIKI Emo"}]
        text = w.to_csv_glossary_only(gloss)
        entries, skipped = w.parse_glossary(text)
        self.assertEqual(skipped, [])
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0], {"ja": "星野ひかり", "en": "Hikari Hoshino"})


class ServerImportTests(unittest.TestCase):
    """/api/words/import と /api/words/export を、data/ を一時フォルダへ向けて通す"""

    @classmethod
    def setUpClass(cls):
        import base64
        import http.client
        import os
        import tempfile
        import threading
        import app_server
        import wordstore
        cls._orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
        cls.tmp = tempfile.TemporaryDirectory()
        wordstore.DATA = cls.tmp.name
        wordstore.PROFILES_DIR = os.path.join(cls.tmp.name, "profiles")
        os.makedirs(wordstore.PROFILES_DIR)
        wordstore._ready = True
        cls.server = app_server._QuietHTTPServer(("127.0.0.1", 0), app_server.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.http = http.client
        cls.b64 = staticmethod(base64.b64encode)   # 素の関数を置くとメソッド化される
        cls.wordstore = wordstore

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        (cls.wordstore.DATA, cls.wordstore.PROFILES_DIR,
         cls.wordstore._ready) = cls._orig
        cls.tmp.cleanup()

    def post(self, path, body):
        import json
        conn = self.http.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, body=json.dumps(body).encode("utf-8"),
                     headers={"Content-Type": "application/json",
                              "Host": f"127.0.0.1:{self.port}"})
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        conn.close()
        return res.status, data

    def test_preview_then_add_shift_jis(self):
        ws = self.wordstore
        # Clear any existing data first
        ws.save_hotwords([])
        ws.save_glossary([])
        # Now set up test data
        ws.save_hotwords([{"surface": "波動拳", "reading": "はどうけん", "score": "2.5"}])
        content = self.b64(CSV.encode("cp932")).decode("ascii")
        status, r = self.post("/api/words/import",
                              {"profile": "", "content_b64": content, "mode": "preview"})
        self.assertEqual(status, 200)
        self.assertEqual(r["stats"]["total"], 3)
        self.assertEqual(r["stats"]["exists"], 1)
        self.assertEqual(ws.load_hotwords(), [{"surface": "波動拳", "reading": "はどうけん",
                                               "score": "2.5"}])   # preview は書かない
        status, r = self.post("/api/words/import",
                              {"profile": "", "content_b64": content, "mode": "add"})
        self.assertEqual(status, 200)
        self.assertEqual((r["hotwords"], r["glossary"]), (2, 2))
        self.assertEqual([h["surface"] for h in ws.load_hotwords()],
                         ["波動拳", "昇龍拳", "竜巻旋風脚"])
        self.assertEqual(ws.load_glossary(), [{"ja": "昇龍拳", "en": "Shoryuken"},
                                              {"ja": "竜巻旋風脚", "en": "Tatsumaki"}])
        # 書き出しは取り込みと同じ列構成で data/export/ へ
        status, r = self.post("/api/words/export", {"profile": ""})
        self.assertEqual(status, 200)
        with open(r["path"], encoding="utf-8-sig", newline="") as f:
            text = f.read()               # CRLF をそのまま読む（Excel 向けの改行を検証）
        self.assertTrue(text.startswith("表記,読み,出やすさ,英訳\r\n"))
        self.assertIn("昇龍拳,しょうりゅうけん,,Shoryuken\r\n", text)

    def test_bad_input(self):
        status, r = self.post("/api/words/import", {"profile": "", "content_b64": "***"})
        self.assertEqual(status, 400)
        status, r = self.post("/api/words/import",
                              {"profile": "no-such", "content_b64": "YQ=="})
        self.assertEqual(status, 404)
        status, r = self.post("/api/words/import",
                              {"profile": "", "content_b64": "YQ==", "mode": "bogus"})
        self.assertEqual(status, 400)

    def test_glossary_import_export(self):
        ws = self.wordstore
        # 初期状態: 英訳辞書のみに登録
        ws.save_glossary([{"ja": "星野ひかり", "en": "Hikari Hoshino"}])
        # CSV 取り込み
        csv_text = "日本語,英訳\n癒色えも,ISHIKI Emo\n文字起こし,transcription\n"
        content = self.b64(csv_text.encode("utf-8")).decode("ascii")
        # プレビュー
        status, r = self.post("/api/glossary/import",
                              {"profile": "", "content_b64": content, "mode": "preview"})
        self.assertEqual(status, 200)
        self.assertEqual(r["stats"]["total"], 2)
        self.assertEqual(r["stats"]["new"], 2)
        # 取り込み（add）
        status, r = self.post("/api/glossary/import",
                              {"profile": "", "content_b64": content, "mode": "add"})
        self.assertEqual(status, 200)
        self.assertEqual(r["glossary"], 2)
        gl = ws.load_glossary()
        self.assertEqual(len(gl), 3)
        self.assertEqual({g["ja"] for g in gl},
                        {"星野ひかり", "癒色えも", "文字起こし"})
        # 書き出し
        status, r = self.post("/api/glossary/export", {"profile": ""})
        self.assertEqual(status, 200)
        with open(r["path"], encoding="utf-8-sig", newline="") as f:
            text = f.read()
        self.assertTrue(text.startswith("日本語,英訳\r\n"))
        self.assertIn("癒色えも,ISHIKI Emo\r\n", text)

    def test_glossary_import_does_not_add_to_hotwords(self):
        """英訳辞書への取り込みは認識辞書を汚染しない"""
        ws = self.wordstore
        ws.save_hotwords([])
        ws.save_glossary([])
        csv_text = "日本語,英訳\n癒色えも,ISHIKI Emo\n"
        content = self.b64(csv_text.encode("utf-8")).decode("ascii")
        status, r = self.post("/api/glossary/import",
                              {"profile": "", "content_b64": content, "mode": "add"})
        self.assertEqual(status, 200)
        self.assertEqual(r["glossary"], 1)
        # 認識辞書は空のまま
        self.assertEqual(ws.load_hotwords(), [])
        # 英訳辞書には入っている
        self.assertEqual(len(ws.load_glossary()), 1)
        self.assertEqual(ws.load_glossary()[0]["ja"], "癒色えも")

    def test_words_export_includes_glossary_only_entries(self):
        """認識辞書の書き出しは英訳だけの語も落とさない（既存動作の保持）"""
        ws = self.wordstore
        ws.save_hotwords([{"surface": "昇龍拳", "reading": "しょうりゅうけん", "score": ""}])
        ws.save_glossary([{"ja": "昇龍拳", "en": "Shoryuken"},
                         {"ja": "英訳だけ", "en": "Translation Only"}])
        status, r = self.post("/api/words/export", {"profile": ""})
        self.assertEqual(status, 200)
        with open(r["path"], encoding="utf-8-sig", newline="") as f:
            text = f.read()
        self.assertIn("英訳だけ,,,Translation Only\r\n", text)


if __name__ == "__main__":
    unittest.main()
