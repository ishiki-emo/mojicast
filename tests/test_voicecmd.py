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
PRESETS = [
    {"id": "standard", "name": "スタンダード"},
    {"id": "cute", "name": "キュート"},
    {"id": "cyber", "name": "サイバー"},
    {"id": "collab", "name": "コラボ（小さめ）"},
]


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


class TestPreset(unittest.TestCase):
    def test_by_name_with_keyword(self):
        cmd = voicecmd.parse("モジキャスト、デザインをサイバーに",
                             WAKES, BOXES, PRESETS)
        self.assertEqual(cmd, {"action": "preset", "id": "cyber",
                               "name": "サイバー"})

    def test_by_bare_name(self):
        # キーワードなしでもデザイン名だけで切り替わる
        cmd = voicecmd.parse("モジキャスト、キュートにして",
                             WAKES, BOXES, PRESETS)
        self.assertEqual(cmd["id"], "cute")

    def test_by_number(self):
        cmd = voicecmd.parse("モジキャスト、デザイン2", WAKES, BOXES, PRESETS)
        self.assertEqual(cmd["id"], "cute")

    def test_by_kanji_number_style_keyword(self):
        cmd = voicecmd.parse("モジキャスト、スタイル三番", WAKES, BOXES, PRESETS)
        self.assertEqual(cmd["id"], "cyber")

    def test_paren_stripped(self):
        cmd = voicecmd.parse("モジキャスト、コラボにして", WAKES, BOXES, PRESETS)
        self.assertEqual(cmd["id"], "collab")

    def test_random(self):
        cmd = voicecmd.parse("モジキャスト、デザイン変えて", WAKES, BOXES, PRESETS)
        self.assertEqual(cmd["action"], "preset_random")

    def test_layout_random_unchanged(self):
        # 「レイアウト変えて」は従来どおりレイアウト側のおまかせ
        cmd = voicecmd.parse("モジキャスト、レイアウト変えて",
                             WAKES, BOXES, PRESETS)
        self.assertEqual(cmd["action"], "box_random")

    def test_box_name_wins_over_preset(self):
        # 同名がある場合はレイアウト優先
        presets = PRESETS + [{"id": "p-lower", "name": "下部バー"}]
        cmd = voicecmd.parse("モジキャスト、下部バーにして",
                             WAKES, BOXES, presets)
        self.assertEqual(cmd["action"], "box")


SCENES = [
    {"id": "talk", "name": "雑談", "preset": "standard", "box": "card"},
    {"id": "game", "name": "ゲーム", "preset": "telop", "box": "top"},
    {"id": "song", "name": "歌枠", "preset": "elegant", "box": "lyric"},
]


class TestScene(unittest.TestCase):
    def test_by_bare_name(self):
        cmd = voicecmd.parse("モジキャスト、歌枠", WAKES, BOXES, PRESETS, SCENES)
        self.assertEqual(cmd, {"action": "scene", "id": "song", "name": "歌枠"})

    def test_mode_suffix(self):
        # 「歌枠モード」のような語尾も名前の包含でそのまま通る
        cmd = voicecmd.parse("モジキャスト、歌枠モードにして",
                             WAKES, BOXES, PRESETS, SCENES)
        self.assertEqual(cmd["id"], "song")

    def test_by_number(self):
        cmd = voicecmd.parse("モジキャスト、シーン2", WAKES, BOXES, PRESETS, SCENES)
        self.assertEqual(cmd["id"], "game")

    def test_random(self):
        cmd = voicecmd.parse("モジキャスト、シーン変えて",
                             WAKES, BOXES, PRESETS, SCENES)
        self.assertEqual(cmd["action"], "scene_random")

    def test_scene_wins_over_box_and_preset(self):
        # 同名がある場合はシーン優先（ユーザーが意図して付けた呼び名）
        scenes = SCENES + [{"id": "s-lower", "name": "下部バー",
                            "preset": "cute", "box": "lower"}]
        cmd = voicecmd.parse("モジキャスト、下部バーにして",
                             WAKES, BOXES, PRESETS, scenes)
        self.assertEqual(cmd["action"], "scene")

    def test_no_scenes_still_works(self):
        # シーン未定義でも従来のレイアウト照合が動く
        cmd = voicecmd.parse("モジキャスト、下部バーにして",
                             WAKES, BOXES, PRESETS, None)
        self.assertEqual(cmd["action"], "box")


COMMANDS = [
    {"id": "c1", "phrases": ["歌枠モード", "うたわく"],
     "action": {"type": "scene", "id": "song"}},
    {"id": "c2", "phrases": ["おそうじ"], "action": {"type": "clear"}},
    {"id": "c3", "phrases": ["翻訳オン"],
     "action": {"type": "translate_lang", "lang": "ko"}},
]


class TestCustom(unittest.TestCase):
    def test_match_phrase(self):
        cmd = voicecmd.parse("モジキャスト、おそうじして", WAKES, BOXES,
                             PRESETS, SCENES, COMMANDS)
        self.assertEqual(cmd["action"], "custom")
        self.assertEqual(cmd["command"]["id"], "c2")

    def test_kana_variant_phrase(self):
        # ひらがな登録の言い回しにカタカナ認識でも当たる
        cmd = voicecmd.parse("モジキャスト、ウタワク", WAKES, BOXES,
                             PRESETS, SCENES, COMMANDS)
        self.assertEqual(cmd["command"]["id"], "c1")

    def test_custom_wins_over_builtin(self):
        # 組み込みの「翻訳オン」と同じ言い回しはカスタム側が優先
        cmd = voicecmd.parse("モジキャスト、翻訳オン", WAKES, BOXES,
                             PRESETS, SCENES, COMMANDS)
        self.assertEqual(cmd["action"], "custom")
        self.assertEqual(cmd["command"]["id"], "c3")

    def test_custom_wins_over_scene_name(self):
        # シーン名「歌枠」を含む「歌枠モード」はカスタムの最長一致で拾う
        cmd = voicecmd.parse("モジキャスト、歌枠モード", WAKES, BOXES,
                             PRESETS, SCENES, COMMANDS)
        self.assertEqual(cmd["action"], "custom")

    def test_no_commands_falls_through(self):
        cmd = voicecmd.parse("モジキャスト、歌枠モード", WAKES, BOXES,
                             PRESETS, SCENES, None)
        self.assertEqual(cmd["action"], "scene")


class TestTranslate(unittest.TestCase):
    def test_lang_en(self):
        cmd = voicecmd.parse("モジキャスト、翻訳を英語に", WAKES, BOXES)
        self.assertEqual(cmd, {"action": "translate_lang",
                               "lang": "en", "label": "英語"})

    def test_lang_zh_simplified_default(self):
        cmd = voicecmd.parse("モジキャスト、翻訳を中国語にして", WAKES, BOXES)
        self.assertEqual(cmd["lang"], "zh")

    def test_lang_zh_tw_specific_wins(self):
        # 「繁体字」が「中国語」より優先される
        cmd = voicecmd.parse("モジキャスト、翻訳を中国語の繁体字に", WAKES, BOXES)
        self.assertEqual(cmd["lang"], "zh_tw")

    def test_lang_ko(self):
        cmd = voicecmd.parse("モジキャスト、翻訳を韓国語に変えて", WAKES, BOXES)
        self.assertEqual(cmd["lang"], "ko")

    def test_off_before_on(self):
        # 「オフにして」の「して」でオンに誤爆しない
        cmd = voicecmd.parse("モジキャスト、翻訳オフにして", WAKES, BOXES)
        self.assertEqual(cmd["action"], "translate_off")

    def test_on(self):
        for phrase in ("翻訳オン", "翻訳して", "翻訳つけて"):
            cmd = voicecmd.parse("モジキャスト、" + phrase, WAKES, BOXES)
            self.assertEqual(cmd["action"], "translate_on", phrase)

    def test_off(self):
        for phrase in ("翻訳を消して", "翻訳やめて", "翻訳なしで"):
            cmd = voicecmd.parse("モジキャスト、" + phrase, WAKES, BOXES)
            self.assertEqual(cmd["action"], "translate_off", phrase)

    def test_translate_unknown(self):
        cmd = voicecmd.parse("モジキャスト、翻訳ってすごいよね", WAKES, BOXES)
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
