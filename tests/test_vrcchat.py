"""vrcchat（VRChatチャットボックス送信）の純関数部分のテスト"""
import unittest

import vrcchat


class TestOscMsg(unittest.TestCase):
    def test_chatbox_input(self):
        # アドレス14文字→NUL込み16バイト、タグ",sTF"→NUL込み8バイト、
        # 文字列"hi"→NUL込み4バイト。真偽値はタグのみでデータ部なし
        msg = vrcchat._osc_msg("/chatbox/input", "hi", True, False)
        self.assertEqual(msg, b"/chatbox/input\x00\x00" b",sTF\x00\x00\x00\x00"
                              b"hi\x00\x00")

    def test_typing(self):
        msg = vrcchat._osc_msg("/chatbox/typing", True)
        self.assertEqual(msg, b"/chatbox/typing\x00" b",T\x00\x00")

    def test_utf8_padding(self):
        # "あ"=3バイト→NUL終端込みで4バイト境界へ
        msg = vrcchat._osc_msg("/chatbox/input", "あ", True, False)
        self.assertTrue(msg.endswith("あ".encode("utf-8") + b"\x00"))
        self.assertEqual(len(msg) % 4, 0)

    def test_pad4_exact_boundary(self):
        # 4の倍数長でもNUL終端は必ず付く（丸ごと4バイト追加）
        self.assertEqual(vrcchat._pad4(b"abcd"), b"abcd\x00\x00\x00\x00")


class TestMerge(unittest.TestCase):
    def test_join_newline(self):
        self.assertEqual(vrcchat._merge(["a", "b", "c"]), "a\nb\nc")

    def test_drop_oldest(self):
        # 新しい行を優先し、収まらない古い行は捨てる
        lines = ["x" * 100, "y" * 100, "z" * 40]
        self.assertEqual(vrcchat._merge(lines), "y" * 100 + "\n" + "z" * 40)

    def test_single_long_line_truncated(self):
        out = vrcchat._merge(["x" * 200])
        self.assertEqual(len(out), vrcchat.MAX_CHARS)
        self.assertTrue(out.endswith("…"))

    def test_multibyte_counted_as_chars(self):
        # 上限はバイトではなく文字数（VRChat仕様の144文字）で数える
        out = vrcchat._merge(["あ" * 200])
        self.assertEqual(len(out), vrcchat.MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
