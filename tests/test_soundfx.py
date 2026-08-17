import unittest

import numpy as np

import soundfx
from soundfx import SoundFxDetector, SAMPLE_RATE


HOP = int(soundfx.HOP_SEC * SAMPLE_RATE)        # 4000サンプル
WIN = int(soundfx.WINDOW_SEC * SAMPLE_RATE)     # 24000サンプル


class FakeTagger:
    """classify のたびに台本（dictのリスト）を順に返すダミー分類器"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def classify(self, samples):
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return {}


def run(script, blocks=None):
    """台本を流して発火イベントを収集する。blocks 省略時は台本を全消化する数"""
    events = []
    det = SoundFxDetector(FakeTagger(script),
                          lambda g, s, spk: events.append((g, round(s, 2), spk)),
                          speaker="自分")
    n = blocks if blocks is not None else (WIN // HOP - 1) + len(script)
    for _ in range(n):
        det.feed(np.zeros(HOP, dtype=np.float32))
    return events


class GroupScoreTests(unittest.TestCase):
    def test_laughter_classes_are_summed(self):
        probs = {"Snicker": 0.65, "Chuckle, chortle": 0.40, "Laughter": 0.41,
                 "Speech": 0.9}
        scores = SoundFxDetector.group_scores(probs)
        self.assertAlmostEqual(scores["笑い"], 1.46)
        self.assertNotIn("Speech", scores)

    def test_zero_groups_are_dropped(self):
        self.assertEqual(SoundFxDetector.group_scores({"Speech": 0.99}), {})


class DetectorTests(unittest.TestCase):
    def test_no_event_below_threshold(self):
        self.assertEqual(run([{"Laughter": 0.29}] * 4), [])

    def test_strong_laugh_fires_immediately_and_once(self):
        # 最初の窓から FIRE(0.5) 超え → 遅延なしで1回だけ
        events = run([{"Laughter": 1.2}, {"Laughter": 1.4}, {"Laughter": 0.8}])
        self.assertEqual(events, [("笑い", 1.2, "自分")])

    def test_weak_start_fires_when_peak_reaches_fire_threshold(self):
        # ENTER(0.3) で追跡が始まり、FIRE(0.5) に達した hop で発火する
        events = run([{"Laughter": 0.4}, {"Laughter": 0.45}, {"Laughter": 0.9}])
        self.assertEqual(events, [("笑い", 0.9, "自分")])

    def test_marginal_event_never_reaching_fire_is_silent(self):
        # 位相ずれで 0.3〜0.46 まで上がる非笑い（実測 93s）は発火させない
        self.assertEqual(run([{"Laughter": 0.38}, {"Laughter": 0.46},
                              {"Laughter": 0.35}]), [])

    def test_new_event_after_gap(self):
        # GAP_SEC(1.0s) を超えて途切れたら別の出来事として再発火する。
        # hop=0.25s なので、間に5回（1.25s）静かな判定を挟む
        script = ([{"Laughter": 0.6}] + [{}] * 5 + [{"Laughter": 0.7}])
        events = run(script)
        self.assertEqual([e[0] for e in events], ["笑い", "笑い"])

    def test_short_dip_does_not_retrigger(self):
        # 1.0s 以内の途切れ（3hop=0.75s）は同じ笑いの揺らぎとして扱う
        script = ([{"Laughter": 0.6}] + [{}] * 3 + [{"Laughter": 0.7}])
        events = run(script)
        self.assertEqual(len(events), 1)

    def test_cough_needs_higher_threshold(self):
        # 咳・くしゃみは0.5。笑うと Cough が 0.33 立つ誤発火を切るため
        self.assertEqual(run([{"Cough": 0.45}] * 3), [])
        events = run([{"Cough": 0.55}])
        self.assertEqual(events[0][0], "咳・くしゃみ")

    def test_scream_uses_lower_threshold(self):
        events = run([{"Screaming": 0.26}])
        self.assertEqual(events[0][0], "驚き・叫び")

    def test_only_top_group_fires_when_both_are_above(self):
        # 咳をすると Laughter も立つが、正解側（高い方）だけ採用する
        events = run([{"Cough": 0.98, "Laughter": 0.45}])
        self.assertEqual([e[0] for e in events], ["咳・くしゃみ"])

    def test_no_classify_until_window_is_full(self):
        tagger = FakeTagger([{"Laughter": 9.9}] * 100)
        det = SoundFxDetector(tagger, lambda *a: None)
        for _ in range(WIN // HOP - 1):     # 1.25秒ぶん＝窓が満ちる直前まで
            det.feed(np.zeros(HOP, dtype=np.float32))
        self.assertEqual(tagger.calls, 0)
        det.feed(np.zeros(HOP, dtype=np.float32))
        self.assertEqual(tagger.calls, 1)

    def test_classify_runs_once_per_hop_regardless_of_block_size(self):
        # 512サンプル刻み（engineのWINDOW_SIZE相当）でも hop ごとに1回だけ
        tagger = FakeTagger([{}] * 100)
        det = SoundFxDetector(tagger, lambda *a: None)
        for _ in range(WIN // 512 + 1):
            det.feed(np.zeros(512, dtype=np.float32))
        calls_at_fill = tagger.calls
        for _ in range(-(-HOP // 512)):     # hop分を切り上げで満たす
            det.feed(np.zeros(512, dtype=np.float32))
        self.assertEqual(tagger.calls, calls_at_fill + 1)

    def test_speaker_is_passed_through(self):
        events = []
        det = SoundFxDetector(FakeTagger([{"Applause": 0.9}]),
                              lambda g, s, spk: events.append(spk),
                              speaker="ゲスト")
        for _ in range(WIN // HOP):
            det.feed(np.zeros(HOP, dtype=np.float32))
        self.assertEqual(events, ["ゲスト"])


if __name__ == "__main__":
    unittest.main()
