"""ガンビット判定部（gambit.py）: 数え方・つなぎ・リセット・検証の境界"""
import unittest

import gambit
from gambit import GambitEngine

GROUPS = {"笑い", "拍手・歓声"}
ANIMS = {"pop", "bounce", "none"}


def card(cid, **kw):
    c = {"id": cid, "name": kw.pop("name", cid), "triggers": [], "links": []}
    c.update(kw)
    return c


def word(*words, speaker="any"):
    return {"type": "word", "words": list(words), "speaker": speaker}


def sound(group, speaker="any", cooldown=0):
    return {"type": "sound", "group": group, "speaker": speaker,
            "cooldown": cooldown}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def engine(raw, values=None, clock=None):
    e = GambitEngine(clock=clock or Clock())
    e.load(gambit.validate_cards(raw, GROUPS, ANIMS), values)
    return e


def values_of(effects):
    return [(f["id"], f["value"]) for f in effects if f["kind"] == "value"]


def actions_of(effects):
    return [f["action"] for f in effects if f["kind"] == "action"]


class CountWordsTests(unittest.TestCase):
    def test_counts_each_occurrence(self):
        self.assertEqual(gambit.count_words("やばいやばい", ["やばい"]), 2)

    def test_spelling_variants_are_one_word(self):
        self.assertEqual(gambit.count_words("ヤバい、ヤバイ！やばい", ["やばい"]), 3)

    def test_overlapping_words_count_once(self):
        self.assertEqual(gambit.count_words("やばい", ["やば", "やばい"]), 1)
        self.assertEqual(gambit.count_words("やばっ", ["やば", "やばい"]), 1)

    def test_no_match(self):
        self.assertEqual(gambit.count_words("たのしい", ["やばい"]), 0)
        self.assertEqual(gambit.count_words("", ["やばい"]), 0)


class TriggerTests(unittest.TestCase):
    def test_final_text_bumps_by_step(self):
        e = engine([card("c1", step=2, triggers=[word("やばい")])])
        self.assertEqual(values_of(e.on_final("やばいやばい", "self")), [("c1", 4)])
        self.assertEqual(e.values["c1"], 4)

    def test_speaker_filter(self):
        e = engine([card("me", triggers=[word("ごめん", speaker="self")]),
                    card("you", triggers=[word("ごめん", speaker="guest")])])
        self.assertEqual(values_of(e.on_final("ごめん", "guest")), [("you", 1)])
        self.assertEqual(values_of(e.on_final("ごめん", "self")), [("me", 1)])

    def test_disabled_card_ignores_triggers_but_allows_manual(self):
        e = engine([card("c1", enabled=False, triggers=[word("やばい")])])
        self.assertEqual(e.on_final("やばい", "self"), [])
        self.assertEqual(values_of(e.bump("c1", "add", 3)), [("c1", 3)])

    def test_sound_cooldown(self):
        clock = Clock()
        e = engine([card("c1", triggers=[sound("笑い", cooldown=5)])], clock=clock)
        self.assertEqual(values_of(e.on_sound("笑い", "self")), [("c1", 1)])
        clock.t = 4.9
        self.assertEqual(e.on_sound("笑い", "self"), [])
        clock.t = 5.0
        self.assertEqual(values_of(e.on_sound("笑い", "self")), [("c1", 2)])
        self.assertEqual(e.on_sound("拍手・歓声", "self"), [])

    def test_value_is_clamped(self):
        e = engine([card("c1", triggers=[word("やばい")])],
                   {"values": {"c1": gambit.MAX_VALUE}})
        self.assertEqual(e.on_final("やばい", "self"), [])
        self.assertEqual(values_of(e.bump("c1", "add", -gambit.MAX_VALUE - 5)),
                         [("c1", 0)])
        self.assertEqual(e.bump("c1", "add", -1), [])

    def test_unknown_card_bump(self):
        e = engine([card("c1")])
        with self.assertRaises(KeyError):
            e.bump("nope")
        with self.assertRaises(ValueError):
            e.bump("c1", "explode")


def silence(seconds, speaker="any"):
    return {"type": "silence", "seconds": seconds, "speaker": speaker}


class SilenceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def run_to(self, e, t, collab=False):
        self.clock.t = t
        return e.on_tick(True, collab)

    def started(self, raw):
        e = engine(raw, clock=self.clock)
        self.assertEqual(e.on_tick(True, False), [])     # 動き出した時点から数える
        return e

    def test_counts_every_n_seconds_and_resets_on_voice(self):
        e = self.started([card("c1", step=5, triggers=[silence(5)])])
        self.assertEqual(self.run_to(e, 4.9), [])
        self.assertEqual(values_of(self.run_to(e, 5)), [("c1", 5)])
        self.assertEqual(self.run_to(e, 9.9), [])
        self.assertEqual(values_of(self.run_to(e, 10)), [("c1", 10)])
        self.assertEqual(values_of(self.run_to(e, 21)), [("c1", 20)])   # 遅れた分はまとめて
        self.clock.t = 22
        self.assertEqual(values_of(e.heard("self")), [("c1", 0)])       # 話したら0
        self.assertEqual(e.heard("self"), [])                           # 数えていなければ何もしない
        self.assertEqual(self.run_to(e, 26.9), [])
        self.assertEqual(values_of(self.run_to(e, 27)), [("c1", 5)])

    def test_voice_of_other_speaker_does_not_reset(self):
        e = self.started([card("me", triggers=[silence(10, "self")])])
        self.run_to(e, 10)
        self.assertEqual(e.heard("guest"), [])
        self.assertEqual(e.values["me"], 1)

    def test_resume_after_stop_continues_and_voice_still_resets(self):
        e = self.started([card("c1", triggers=[silence(10)])])
        self.run_to(e, 10)
        self.clock.t = 15
        e.on_tick(False, False)
        self.clock.t = 50
        self.assertEqual(e.on_tick(True, False), [])      # 止めていた間は数えない
        self.assertEqual(values_of(self.run_to(e, 60)), [("c1", 2)])
        self.assertEqual(values_of(e.heard("self")), [("c1", 0)])

    def test_only_one_silence_per_card(self):
        raw = [card("c1", triggers=[silence(5), silence(10, "self")])]
        with self.assertRaises(ValueError):
            gambit.validate_cards(raw, GROUPS, ANIMS)
        # 読み込みはカードを捨てず、2つ目以降の「黙っている」だけ外す
        out = gambit.load_cards(raw, GROUPS, ANIMS)
        self.assertEqual(out[0]["triggers"], [{"type": "silence", "speaker": "any", "seconds": 5}])

    def test_not_counted_while_engine_stopped(self):
        e = self.started([card("c1", triggers=[silence(10)])])
        self.clock.t = 5
        e.on_tick(False, False)
        self.clock.t = 30
        self.assertEqual(e.on_tick(False, False), [])
        self.assertEqual(e.on_tick(True, False), [])      # 再開したら数え直し
        self.assertEqual(self.run_to(e, 39), [])
        self.assertEqual(values_of(self.run_to(e, 40)), [("c1", 1)])

    def test_speaker_roles(self):
        e = self.started([card("me", triggers=[silence(10, "self")]),
                          card("all", triggers=[silence(10, "any")]),
                          card("gs", triggers=[silence(10, "guest")])])
        self.clock.t = 8
        e.heard("guest")                                  # 相手だけ話している
        out = values_of(self.run_to(e, 12, collab=True))
        self.assertEqual(out, [("me", 1)])
        out = values_of(self.run_to(e, 18, collab=True))
        self.assertEqual(out, [("all", 1), ("gs", 1)])

    def test_guest_silence_needs_collab(self):
        e = self.started([card("gs", triggers=[silence(10, "guest")])])
        self.assertEqual(self.run_to(e, 100), [])

    def test_reload_restarts_the_wait(self):
        raw = [card("c1", triggers=[silence(10)])]
        e = self.started(raw)
        self.clock.t = 8
        e.load(gambit.validate_cards(raw, GROUPS, ANIMS), e.snapshot())
        self.assertEqual(self.run_to(e, 17), [])
        self.assertEqual(values_of(self.run_to(e, 18)), [("c1", 1)])

    def test_links_fire_from_silence(self):
        e = self.started([card("c1", triggers=[silence(5)],
                               links=[{"when": "reach", "n": 1, "op": "stamp",
                                       "group": "笑い"}])])
        self.assertEqual(actions_of(self.run_to(e, 5)),
                         [{"type": "stamp", "group": "笑い"}])

    def test_validation(self):
        for bad in (4, 601, 10.5, True, "30"):
            with self.assertRaises(ValueError):
                gambit.validate_cards([card("c1", triggers=[silence(bad)])], GROUPS, ANIMS)
        out = gambit.validate_cards([card("c1", triggers=[{"type": "silence"}])],
                                    GROUPS, ANIMS)
        self.assertEqual(out[0]["triggers"][0], {"type": "silence", "speaker": "any",
                                                  "seconds": 30})


class LinkTests(unittest.TestCase):
    def test_every_n_fires_on_each_milestone(self):
        e = engine([
            card("c1", triggers=[word("やばい")],
                 links=[{"when": "every", "n": 3, "op": "add", "target": "c2"}]),
            card("c2"),
        ])
        for _ in range(5):
            e.on_final("やばい", "self")
        self.assertEqual(e.values["c2"], 1)
        e.on_final("やばい", "self")
        self.assertEqual(e.values["c2"], 2)

    def test_jumping_over_several_milestones_adds_per_milestone(self):
        e = engine([
            card("c1", links=[{"when": "every", "n": 10, "op": "add",
                               "target": "c2", "value": 2}]),
            card("c2"),
        ])
        self.assertEqual(values_of(e.bump("c1", "add", 25)),
                         [("c1", 25), ("c2", 4)])

    def test_reach_fires_once_until_reset(self):
        e = engine([
            card("c1", links=[{"when": "reach", "n": 2, "op": "scene", "id": "s1"}]),
        ])
        e.bump("c1")
        self.assertEqual(actions_of(e.bump("c1")), [{"type": "scene", "id": "s1"}])
        e.bump("c1", "add", -1)
        self.assertEqual(actions_of(e.bump("c1")), [])   # 下げて戻しても1回きり
        e.bump("c1", "reset")
        e.bump("c1")
        self.assertEqual(actions_of(e.bump("c1")), [{"type": "scene", "id": "s1"}])

    def test_switch_and_stamp_fire_once_per_change(self):
        e = engine([card("c1", links=[
            {"when": "every", "n": 1, "op": "stamp", "group": "笑い"},
            {"when": "every", "n": 1, "op": "box", "id": "b1"},
        ])])
        self.assertEqual(actions_of(e.bump("c1", "add", 3)),
                         [{"type": "stamp", "group": "笑い"},
                          {"type": "box", "id": "b1"}])

    def test_decrease_does_not_fire(self):
        e = engine([card("c1", links=[{"when": "every", "n": 1, "op": "stamp",
                                       "group": "笑い"}])],
                   {"values": {"c1": 5}})
        self.assertEqual(actions_of(e.bump("c1", "add", -1)), [])

    def test_reset_link(self):
        e = engine([
            card("c1", links=[{"when": "reach", "n": 1, "op": "reset", "target": "c2"}]),
            card("c2"),
        ], {"values": {"c2": 7}})
        self.assertEqual(values_of(e.bump("c1")), [("c1", 1), ("c2", 0)])

    def test_chain_stops_at_max_depth(self):
        cards = [card(f"c{i}", links=[{"when": "every", "n": 1, "op": "add",
                                       "target": f"c{i + 1}"}])
                 for i in range(5)] + [card("c5")]
        e = engine(cards)
        e.bump("c0")
        self.assertEqual([e.values[f"c{i}"] for i in range(6)],
                         [1, 1, 1, 1, 0, 0])

    def test_link_into_disabled_card_is_ignored(self):
        e = engine([
            card("c1", links=[{"when": "every", "n": 1, "op": "add", "target": "c2"}]),
            card("c2", enabled=False),
        ])
        self.assertEqual(values_of(e.bump("c1")), [("c1", 1)])


class ResetTests(unittest.TestCase):
    def test_reset_on_start_only_touches_start_cards(self):
        e = engine([card("a", reset="start"), card("b", reset="manual")],
                   {"values": {"a": 3, "b": 4}})
        self.assertEqual(values_of(e.reset_on_start()), [("a", 0)])
        self.assertEqual(e.values, {"a": 0, "b": 4})

    def test_snapshot_roundtrip(self):
        e = engine([card("c1", links=[{"when": "reach", "n": 1, "op": "scene",
                                       "id": "s"}])])
        e.bump("c1")
        e2 = engine([card("c1", links=[{"when": "reach", "n": 1, "op": "scene",
                                        "id": "s"}])], e.snapshot())
        self.assertEqual(e2.values, {"c1": 1})
        e2.bump("c1", "add", -1)
        self.assertEqual(actions_of(e2.bump("c1")), [])   # 発火済みも引き継ぐ


class ValidationTests(unittest.TestCase):
    def good(self):
        return [
            card("c1", name="やばい", step=1, reset="start",
                 triggers=[word("やばい"), sound("笑い", "self", 5)],
                 display={"widget": {"type": "counter", "pos": {"x": 0.9, "y": 0.1},
                                     "size": 0.12, "style": "p1", "anim": "pop"}},
                 links=[{"when": "every", "n": 10, "op": "add", "target": "c2"}]),
            card("c2", name="やばランク",
                 links=[{"when": "reach", "n": 5, "op": "scene", "id": "s1"}]),
        ]

    def test_good_passes_and_fills_defaults(self):
        out = gambit.validate_cards(self.good(), GROUPS, ANIMS)
        self.assertEqual(out[0]["links"][0]["value"], 1)
        self.assertTrue(out[0]["enabled"])
        self.assertEqual(out[0]["display"]["widget"]["label"], True)
        self.assertEqual(out[1]["display"], {})

    def test_label_look_defaults_and_clamp(self):
        out = gambit.validate_cards(self.good(), GROUPS, ANIMS)
        w = out[0]["display"]["widget"]
        self.assertEqual((w["label_style"], w["label_scale"]), ("", 0.55))
        raw = self.good()
        raw[0]["display"]["widget"].update(label_style="p2", label_scale=9)
        w = gambit.validate_cards(raw, GROUPS, ANIMS)[0]["display"]["widget"]
        self.assertEqual((w["label_style"], w["label_scale"]), ("p2", gambit.MAX_LABEL_SCALE))

    def test_unit_defaults_trim_and_limits(self):
        w = gambit.validate_cards(self.good(), GROUPS, ANIMS)[0]["display"]["widget"]
        self.assertEqual((w["unit"], w["unit_style"], w["unit_scale"]), ("", "", 0.55))
        raw = self.good()
        raw[0]["display"]["widget"].update(unit=" 回 ", unit_style="p2", unit_scale=0)
        w = gambit.validate_cards(raw, GROUPS, ANIMS)[0]["display"]["widget"]
        self.assertEqual((w["unit"], w["unit_style"], w["unit_scale"]),
                         ("回", "p2", gambit.MIN_LABEL_SCALE))
        for bad in ("あ" * (gambit.MAX_UNIT_LEN + 1), 5):
            raw = self.good()
            raw[0]["display"]["widget"]["unit"] = bad
            with self.assertRaises(ValueError):
                gambit.validate_cards(raw, GROUPS, ANIMS)

    def invalid(self):
        g = self.good
        yield None
        yield [card(f"c{i}", name="x") for i in range(gambit.MAX_CARDS + 1)]
        yield [card("c1", name="a"), card("c1", name="b")]            # ID重複
        c = g(); c[1]["links"] = [{"when": "every", "n": 1, "op": "add",
                                   "target": "c1"}]
        yield c                                                        # 上へつなぐ
        c = g(); c[0]["links"][0]["target"] = "c1"
        yield c                                                        # 自分へつなぐ
        c = g(); c[0]["triggers"] = [word("やばい")] * (gambit.MAX_TRIGGERS + 1)
        yield c
        c = g(); c[0]["triggers"] = [word("あ" * (gambit.MAX_WORD_LEN + 1))]
        yield c
        c = g(); c[0]["triggers"] = [word("！？")]                      # 記号だけ
        yield c
        c = g(); c[0]["triggers"] = [sound("いびき")]
        yield c
        c = g(); c[0]["triggers"] = [{"type": "eval", "code": "1"}]
        yield c
        c = g(); c[0]["step"] = 0
        yield c
        c = g(); c[0]["step"] = True
        yield c
        c = g(); c[0]["links"][0]["value"] = 0
        yield c
        c = g(); c[0]["links"][0]["op"] = "exec"
        yield c
        c = g(); c[0]["display"]["widget"]["anim"] = "explode"
        yield c
        c = g(); c[0]["reset"] = "daily"
        yield c
        c = g(); c[0]["display"]["widget"]["label_style"] = 3
        yield c

    def test_invalid_is_rejected(self):
        for i, raw in enumerate(self.invalid()):
            with self.subTest(i=i), self.assertRaises((ValueError, TypeError)):
                gambit.validate_cards(raw, GROUPS, ANIMS)

    def test_load_drops_only_broken_parts(self):
        raw = self.good()
        raw.insert(1, {"id": "bad", "name": ""})            # 壊れたカード
        raw[0]["links"].append({"when": "every", "n": 1, "op": "add",
                                "target": "bad"})           # 壊れたカードを指す
        raw[0]["links"].append({"op": "nope"})
        out = gambit.load_cards(raw, GROUPS, ANIMS)
        self.assertEqual([c["id"] for c in out], ["c1", "c2"])
        self.assertEqual([lk.get("target") for lk in out[0]["links"]], ["c2"])

    def test_load_values_clamps_and_drops_unknown(self):
        cards = gambit.validate_cards(self.good(), GROUPS, ANIMS)
        v = gambit.load_values({"values": {"c1": -3, "c2": "x", "zz": 5},
                                "reached": {"c2:0": True, "zz:0": True,
                                            "c1:0": "yes"}}, cards)
        self.assertEqual(v, {"values": {"c1": 0, "c2": 0},
                             "reached": {"c2:0": True}})
        self.assertEqual(gambit.load_values(None, cards)["values"],
                         {"c1": 0, "c2": 0})


if __name__ == "__main__":
    unittest.main()
