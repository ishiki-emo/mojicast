"""句読点モデルが壊れたときに字幕を守るガード

v0.9.5 で int8 化した句読点BERTが、ある利用者の環境（i9-9900K）で
「あ。い。う。え。」と全文字に句点を打った。推論結果が0付近になると
sigmoid が 0.5 を返し、しきい値(0.1)を全文字が超えるため。
壊れても句読点を諦めるだけで済むよう、付けすぎを検出して原文に倒す。
"""
import unittest
from unittest import mock

import numpy as np

import punct


def _flat_session(prob):
    """全位置で同じ確率を返すダミー。prob=0.5 が「出力が0＝壊れたモデル」の再現"""
    logit = float(np.log(prob / (1 - prob)))

    class Session:
        def run(self, _outputs, feeds):
            n = feeds["input_ids"].shape[1]
            return [np.full((1, n, 2), logit, dtype=np.float32)]

    return Session()


class LooksBrokenTests(unittest.TestCase):
    def test_punctuation_after_every_character_is_broken(self):
        self.assertTrue(punct._looks_broken("受け子", "受。け。子。"))
        self.assertTrue(punct._looks_broken("あいうえ", "あ。い。う。え。"))
        self.assertTrue(punct._looks_broken(
            "ああマイクテスト", "あ。あ。マ。イ。ク。テ。ス。ト。"))

    def test_normal_punctuation_passes(self):
        self.assertFalse(punct._looks_broken(
            "きょうはいい天気ですね明日は雨が降るそうです",
            "きょうはいい天気ですね。明日は雨が降るそうです。"))
        self.assertFalse(punct._looks_broken("受け子", "受け子"))

    def test_short_lines_are_not_flagged(self):
        # 「はい。」のような極端に短い行は句読点の比率が高くても正常
        self.assertFalse(punct._looks_broken("はい", "はい。"))
        self.assertFalse(punct._looks_broken("はいそう", "はい、そう。"))


class BrokenModelTests(unittest.TestCase):
    """推論が壊れている（出力が0）ときの振る舞い"""

    def setUp(self):
        patches = [mock.patch.object(punct, "_sess", _flat_session(0.5)),
                   mock.patch.object(punct, "_vocab", {}),
                   mock.patch.object(punct, "_cls_id", 2),
                   mock.patch.object(punct, "_sep_id", 3),
                   mock.patch.object(punct, "_unk_id", 1)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_caption_survives_without_punctuation(self):
        text = "ああマイクテストマイクテスト"
        self.assertEqual(punct.add_punctuation(text), text)

    def test_selftest_detects_it(self):
        # ロード直後の自己診断が見るのはガード前の生の結果
        probe = punct._punctuate_raw(punct._PROBE)
        self.assertEqual(probe, "こ。れ。は。て。す。と。で。す。")
        self.assertTrue(punct._looks_broken(punct._PROBE, probe))


class SelfTestFailedTests(unittest.TestCase):
    """engine.py は例外の型名を見て「高精度モードで回避できます」と案内する。
    punct を try の中で import しているため型を直接参照できず、名前で見分けている。
    ここを変えると案内が出なくなるので、結合をテストで固定する。
    """

    def test_it_is_a_runtime_error(self):
        # 呼び出し側が Exception で受けている前提を崩さない
        self.assertTrue(issubclass(punct.SelfTestFailed, RuntimeError))

    def test_the_class_name_is_what_engine_looks_for(self):
        self.assertEqual(punct.SelfTestFailed.__name__, "SelfTestFailed")


class HealthyModelTests(unittest.TestCase):
    """正常時（確率が低い＝どこにも打たない）はガードが邪魔をしない"""

    def test_no_punctuation_is_left_alone(self):
        with mock.patch.object(punct, "_sess", _flat_session(0.01)), \
             mock.patch.object(punct, "_vocab", {}), \
             mock.patch.object(punct, "_cls_id", 2), \
             mock.patch.object(punct, "_sep_id", 3), \
             mock.patch.object(punct, "_unk_id", 1):
            self.assertEqual(punct.add_punctuation("あいうえお"), "あいうえお")


if __name__ == "__main__":
    unittest.main()


class _FakePunct:
    """句読点モジュールの差し替え。broken に入れた精度でだけ自己診断に落ちる"""

    def __init__(self, *broken):
        self.broken = set(broken)
        self.loaded = None
        self.calls = []          # load_punctuator に渡された精度の履歴

    def loaded_precision(self):
        return self.loaded

    def unload(self):
        self.loaded = None

    def load_punctuator(self, precision="int8"):
        self.calls.append(precision)
        if precision in self.broken:
            raise punct.SelfTestFailed(f"{precision} は壊れている")
        self.loaded = precision

    def add_punctuation(self, text):
        return text


class AutoFallbackTests(unittest.TestCase):
    """int8 が壊れるPCでは fp32 へ自動で切り替える（engine._load_punct）。

    VNNI 非搭載CPUは開発機に無く実地で試せないため、壊れ方を模して経路を固定する。
    """

    def setUp(self):
        from engine import CaptionEngine
        self.load = CaptionEngine._load_punct

    def test_int8_is_kept_when_it_works(self):
        p = _FakePunct()
        self.assertEqual(self.load(p, "int8"), "")
        self.assertEqual(p.calls, ["int8"])      # fp32 を触らない＝メモリを増やさない

    def test_broken_int8_falls_back_to_fp32(self):
        p = _FakePunct("int8")
        warn = self.load(p, "int8")
        self.assertEqual(p.calls, ["int8", "fp32"])
        self.assertEqual(p.loaded, "fp32")
        self.assertIn("高精度モード", warn)       # 黙って重くしない

    def test_high_precision_mode_does_not_retry(self):
        # 利用者が既に fp32 を選んでいる。落ちたら精度を変えても直らない
        p = _FakePunct("fp32")
        with self.assertRaises(punct.SelfTestFailed):
            self.load(p, "fp32")
        self.assertEqual(p.calls, ["fp32"])

    def test_both_broken_raises(self):
        p = _FakePunct("int8", "fp32")
        with self.assertRaises(punct.SelfTestFailed):
            self.load(p, "int8")
        self.assertEqual(p.calls, ["int8", "fp32"])

    def test_other_failures_are_not_retried(self):
        # ファイル不足・DL失敗などは精度を変えても直らないので即座に投げ直す
        class Missing(_FakePunct):
            def load_punctuator(self, precision="int8"):
                self.calls.append(precision)
                raise FileNotFoundError("vocab.txt がない")

        p = Missing()
        with self.assertRaises(FileNotFoundError):
            self.load(p, "int8")
        self.assertEqual(p.calls, ["int8"])
