import unittest

from translate import _apply_stream_terms, _fix_case


class FixCaseTests(unittest.TestCase):
    """英訳の見た目を整える後処理。

    FuguMT は句読点の無い話し言葉を渡すと訳文全体を小文字で返すことがあり、
    終止符の直後が詰まる（`Super Chat!I'll use it.`）こともある。字幕として
    目立つのでここで直す。
    """

    def test_sentence_head_and_lone_i_are_capitalized(self):
        self.assertEqual(_fix_case("i forgot to say that, but i'll be off."),
                         "I forgot to say that, but I'll be off.")

    def test_capitalizes_after_bang_and_question(self):
        self.assertEqual(_fix_case("good! i won! i won!"), "Good! I won! I won!")
        self.assertEqual(_fix_case("really? yes."), "Really? Yes.")

    def test_inserts_the_missing_space_after_bang(self):
        self.assertEqual(_fix_case("Thank you for Super Chat!I'll use it."),
                         "Thank you for Super Chat! I'll use it.")

    def test_leaves_abbreviations_alone(self):
        # ピリオドの直後を大文字にすると U.S.A. や Mr. の後ろを壊すので触らない
        self.assertEqual(_fix_case("The U.S.A. is big."), "The U.S.A. is big.")
        self.assertEqual(_fix_case("Mr. smith is here."), "Mr. smith is here.")

    def test_does_not_break_camel_case_or_empty(self):
        self.assertEqual(_fix_case("the iPhone is mine."), "The iPhone is mine.")
        self.assertEqual(_fix_case(""), "")


if __name__ == "__main__":
    unittest.main()
