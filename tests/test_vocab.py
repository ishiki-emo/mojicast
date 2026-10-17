"""読み→表記 置換（vocab.build_replacer）のテスト

数千語の一括登録（ゲームの技名）を前提に、意味論と速度の両方を固定する。
"""
import random
import time
import unittest

from vocab import build_replacer


class ReplacerSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.rep = build_replacer([
            ("癒色えも", "いしきえも／意識エモ", None),
            ("意識エモい系", "意識エモい系", None),
            ("文字起こし", "もじおこし", None),
        ])

    def test_reading_to_surface(self):
        self.assertEqual(self.rep("いしきえもです"), "癒色えもです")

    def test_multiple_readings(self):
        self.assertEqual(self.rep("意識エモです"), "癒色えもです")

    def test_lenient_punctuation_inside_word(self):
        self.assertEqual(self.rep("意識、エモです"), "癒色えもです")

    def test_longer_surface_is_protected(self):
        # 本文に「意識エモい系」が出ているとき、読み「意識エモ」に食われない
        self.assertEqual(self.rep("意識エモい系の話"), "意識エモい系の話")

    def test_multiple_hits_in_one_line(self):
        self.assertEqual(self.rep("もじおこしといしきえも"), "文字起こしと癒色えも")

    def test_no_chain_replacement(self):
        # 置換結果の中に別エントリの読みが含まれても再置換しない
        rep = build_replacer([("ABC", "x", None), ("Q", "B", None)])
        self.assertEqual(rep("x"), "ABC")

    def test_untouched_text(self):
        self.assertEqual(self.rep("無関係な文"), "無関係な文")

    def test_empty(self):
        rep = build_replacer([("同じ", "同じ", None)])
        self.assertEqual(rep("同じ"), "同じ")
        self.assertEqual(build_replacer([])("何か"), "何か")


class ReplacerScaleTests(unittest.TestCase):
    def test_thousands_of_words_stay_fast(self):
        random.seed(1)
        kana = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん"
        entries = [(f"技{i}", "".join(random.choice(kana) for _ in range(random.randint(3, 8))), None)
                   for i in range(2500)]
        rep = build_replacer(entries)
        text = "今日は" + entries[5][1] + "を出してから" + entries[100][1] + "で締める" * 5
        t = time.time()
        for _ in range(50):
            out = rep(text)
        per_call = (time.time() - t) / 50
        self.assertEqual(out.count("技"), 2)
        # 旧実装（全パターンを1本の正規表現に束ねる）は1回100msだった
        self.assertLess(per_call, 0.01)


class HotwordFileTests(unittest.TestCase):
    """読み欄の「,」で列がずれ、強さに文字が入ると sherpa-onnx が
    「invalid stof argument」でロードごと失敗していた（2026-10-17）"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _parse(self, text):
        import os
        import vocab
        p = os.path.join(self.tmp.name, "h.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return vocab.parse_vocab(p)

    def _gen(self, entries):
        import os
        import vocab
        out = vocab.write_hotwords(entries, os.path.join(self.tmp.name, "g.txt"))
        with open(out, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_broken_comma_line_is_read_as_extra_readings(self):
        e = self._parse("癒色えも,意識エモ,意識えも\nC,しー,でぃー,3\nB,びー,\n")
        self.assertEqual(e[0], ("癒色えも", "意識エモ／意識えも", None))
        self.assertEqual(e[1], ("C", "しー／でぃー", "3"))
        self.assertEqual(e[2], ("B", "びー", None))

    def test_generated_file_never_has_non_numeric_score(self):
        lines = self._gen([("癒色えも", "意識エモ", "意識えも"), ("A", "えー", "2.5"), ("B", "びー", "nan")])
        self.assertEqual(lines, ["意識エモ", "えー :2.5", "びー"])
        for line in lines:
            if " :" in line:
                float(line.split(" :")[1])

    def test_save_turns_reading_commas_into_slashes(self):
        import os
        import wordstore
        orig = (wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready)
        try:
            wordstore.DATA = self.tmp.name
            wordstore.PROFILES_DIR = os.path.join(self.tmp.name, "profiles")
            os.makedirs(wordstore.PROFILES_DIR, exist_ok=True)
            wordstore._ready = True
            wordstore.save_hotwords([
                {"surface": "癒色えも", "reading": "意識エモ,意識えも，いしきえも", "score": ""},
                {"surface": "A,B", "reading": "えーびー", "score": "強め"},
            ])
            got = wordstore.load_hotwords()
            self.assertEqual(got[0]["reading"], "意識エモ／意識えも／いしきえも")
            self.assertEqual(got[1]["surface"], "A，B")
            self.assertEqual(got[1]["score"], "")
        finally:
            wordstore.DATA, wordstore.PROFILES_DIR, wordstore._ready = orig


if __name__ == "__main__":
    unittest.main()
