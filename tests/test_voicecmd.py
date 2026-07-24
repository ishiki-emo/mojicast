import unittest

import voicecmd


BOXES = [
    {"id": "none", "name": "フリー（枠なし）"},
    {"id": "lower", "name": "下部バー"},
    {"id": "card", "name": "角丸カード"},
    {"id": "side", "name": "サイドログ"},
    {"id": "tategaki", "name": "縦書き"},
]
WAKES = ["モジキャスト", "文字キャスト"]


class TestNormalize(unittest.TestCase):
    def test_kana_and_punct(self):
        self.assertEqual(voicecmd.normalize("ステラ、下部バーに。"),
                         "すてら下部ばーに")

    def test_nfkc(self):
        self.assertEqual(voicecmd.normalize("レイアウト２"), "れいあうと2")


class TestStripWake(unittest.TestCase):
    def test_no_wake(self):
        self.assertIsNone(voicecmd.strip_wake("今日はいい天気です", WAKES))

    def test_exact(self):
        self.assertEqual(
            voicecmd.strip_wake("モジキャスト、字幕クリア", WAKES),
            "字幕くりあ")

    def test_misrecognized_variant(self):
        # 「モジキャスト」が「文字キャスト」と認識されてもゆれ表記で拾える
        self.assertEqual(
            voicecmd.strip_wake("文字キャスト、レイアウト2", WAKES),
            "れいあうと2")

    def test_hiragana_variant(self):
        # ウェイク登録はカタカナ・認識結果はひらがな、でも一致する
        self.assertEqual(voicecmd.strip_wake("もじきゃすと、テスト", WAKES),
                         "てすと")

    def test_empty_wakes(self):
        self.assertIsNone(voicecmd.strip_wake("モジキャスト、テスト", []))
        self.assertIsNone(voicecmd.strip_wake("モジキャスト、テスト", ["", "  "]))


class TestParse(unittest.TestCase):
    def test_not_command(self):
        self.assertIsNone(voicecmd.parse("今日も配信やっていきます", WAKES, BOXES))

    def test_box_by_name(self):
        cmd = voicecmd.parse("モジキャスト、レイアウトを下部バーに。", WAKES, BOXES)
        self.assertEqual(cmd, {"action": "box", "id": "lower", "name": "下部バー"})

    def test_box_by_name_with_suffix_words(self):
        cmd = voicecmd.parse("文字キャスト、サイドログにして", WAKES, BOXES)
        self.assertEqual(cmd["id"], "side")

    def test_box_paren_stripped(self):
        # 「フリー（枠なし）」は「フリー」だけでもマッチする
        cmd = voicecmd.parse("モジキャスト、レイアウトをフリーに", WAKES, BOXES)
        self.assertEqual(cmd["id"], "none")

    def test_box_kanji_name(self):
        cmd = voicecmd.parse("モジキャスト、縦書きに変更", WAKES, BOXES)
        self.assertEqual(cmd["id"], "tategaki")

    def test_box_by_number(self):
        cmd = voicecmd.parse("モジキャスト、レイアウト2", WAKES, BOXES)
        self.assertEqual(cmd["id"], "lower")   # 1始まりで2番目

    def test_box_by_number_ban(self):
        cmd = voicecmd.parse("モジキャスト、4番にして", WAKES, BOXES)
        self.assertEqual(cmd["id"], "side")

    def test_number_out_of_range_falls_through(self):
        cmd = voicecmd.parse("モジキャスト、レイアウト99", WAKES, BOXES)
        self.assertEqual(cmd["action"], "unknown")

    def test_box_by_kanji_number(self):
        # 数字正規化を通らず漢数字のまま届いても番号指定が効く
        cmd = voicecmd.parse("モジキャスト、レイアウト二番", WAKES, BOXES)
        self.assertEqual(cmd["id"], "lower")

    def test_box_by_kanji_number_ten(self):
        boxes = [{"id": f"b{i}", "name": f"ボックス{i}"} for i in range(12)]
        cmd = voicecmd.parse("モジキャスト、レイアウト十一", WAKES, boxes)
        self.assertEqual(cmd["id"], "b10")   # 11番目（1始まり）

    def test_random_change(self):
        for phrase in ("レイアウト変えて", "レイアウトをチェンジ",
                       "れいあうと、おまかせ", "レイアウト変更"):
            cmd = voicecmd.parse("モジキャスト、" + phrase, WAKES, BOXES)
            self.assertEqual(cmd["action"], "box_random", phrase)

    def test_random_needs_layout_word(self):
        # 「変えて」だけでは発動しない（誤爆防止）
        cmd = voicecmd.parse("モジキャスト、変えて", WAKES, BOXES)
        self.assertEqual(cmd["action"], "unknown")

    def test_longest_name_wins(self):
        boxes = BOXES + [{"id": "lower2", "name": "下部バー2"}]
        cmd = voicecmd.parse("モジキャスト、下部バー2にして", WAKES, boxes)
        self.assertEqual(cmd["id"], "lower2")

    def test_unknown(self):
        cmd = voicecmd.parse("モジキャスト、こんにちは", WAKES, BOXES)
        self.assertEqual(cmd["action"], "unknown")
        self.assertEqual(cmd["rest"], "こんにちは")


if __name__ == "__main__":
    unittest.main()
