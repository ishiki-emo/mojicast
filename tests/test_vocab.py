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


if __name__ == "__main__":
    unittest.main()
