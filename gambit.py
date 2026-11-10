"""ガンビット（きっかけ→変数→見せ方）の判定部（GAMBIT_DESIGN.md）

出来事（確定字幕・音イベント・沈黙・ボタン）を受け取り、カードの値を動かして
「値の変化」と「実行してほしい行動」の一覧を返す。行動の実行・保存・配信は
呼び出し側（app_server）の仕事で、ここでは何もしない。

soundfx.py と同じ「独立部品」の位置づけ。**app_server を import しない。**
照合の正規化だけは音声コマンドと揃えるため voicecmd.normalize を使う。

提供するもの:
    validate_cards(raw, sound_groups, anims)  保存前の検証（不正なら ValueError）
    load_cards(raw, sound_groups, anims)      読み込み用（壊れたカードは捨てる）
    load_values(raw, cards)                   値ファイルの読み込み（範囲外は丸める）
    GambitEngine                              出来事 → Effect のリスト

Effect:
    {"kind": "value", "id": str, "value": int, "delta": int}
    {"kind": "action", "action": {"type": "scene"/"box"/"preset", "id": str},
     "card": str}
    {"kind": "action", "action": {"type": "stamp", "group": str}, "card": str}
    （card は節目を迎えたカードの id。失敗の通知でどのカードかを示すため）

話者は呼び出し側で "self"（自分）/ "guest"（相手）に解決して渡す。
"""
import time

import voicecmd

MAX_CARDS = 10
MAX_TRIGGERS = 5        # カード1枚あたり
MAX_LINKS = 5           # カード1枚あたり
MAX_WORDS = 5           # きっかけ1つあたり
MAX_WORD_LEN = 30
MAX_NAME_LEN = 30
MAX_ID_LEN = 32
MAX_DEPTH = 3           # つなぎの連鎖の段数
MAX_VALUE = 99999
MAX_STEP = 100
MAX_LINK_VALUE = 1000
MAX_COOLDOWN = 60.0
MIN_SILENCE = 5           # しばらく黙っている（秒）
MAX_SILENCE = 600
MIN_LABEL_SCALE = 0.2
MAX_LABEL_SCALE = 2.0
MAX_UNIT_LEN = 10

SPEAKERS = ("self", "guest", "any")
RESETS = ("start", "manual")
LINK_WHENS = ("every", "reach")
LINK_VALUE_OPS = ("add", "reset")              # 下のカードの値を動かす
LINK_SWITCH_OPS = ("scene", "box", "preset")   # 既存の切替（run_action へ渡す）
LINK_OPS = LINK_VALUE_OPS + LINK_SWITCH_OPS + ("stamp",)


def _clamp_value(v):
    return min(MAX_VALUE, max(0, int(v)))


def _int_in(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{what} は {lo}〜{hi} の整数です")
    return v


def _str_in(v, max_len, what):
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > max_len:
        raise ValueError(f"{what} は1〜{max_len}文字です")
    return v.strip()


def _clean_pos(pos):
    pos = pos if isinstance(pos, dict) else {}
    return {"x": min(1.0, max(0.0, float(pos.get("x", 0.9)))),
            "y": min(1.0, max(0.0, float(pos.get("y", 0.1))))}


def _clean_trigger(t, sound_groups):
    if not isinstance(t, dict):
        raise ValueError("きっかけの形が不正です")
    speaker = t.get("speaker", "any")
    if speaker not in SPEAKERS:
        raise ValueError("話者の指定が不正です")
    typ = t.get("type")
    if typ == "word":
        words = t.get("words")
        if not isinstance(words, list) or not 1 <= len(words) <= MAX_WORDS:
            raise ValueError(f"語は1〜{MAX_WORDS}個です")
        words = [_str_in(w, MAX_WORD_LEN, "語") for w in words]
        if any(not voicecmd.normalize(w) for w in words):
            raise ValueError("記号だけの語は数えられません")
        return {"type": "word", "words": words, "speaker": speaker}
    if typ == "sound":
        group = t.get("group")
        if not isinstance(group, str) or (sound_groups is not None
                                          and group not in sound_groups):
            raise ValueError("音イベントの種類が不正です")
        cd = t.get("cooldown", 0)
        if isinstance(cd, bool) or not isinstance(cd, (int, float)):
            raise ValueError("クールダウンは数値です")
        return {"type": "sound", "group": group, "speaker": speaker,
                "cooldown": min(MAX_COOLDOWN, max(0.0, float(cd)))}
    if typ == "silence":
        return {"type": "silence", "speaker": speaker,
                "seconds": _int_in(t.get("seconds", 30), MIN_SILENCE, MAX_SILENCE,
                                   "黙っている秒数")}
    raise ValueError("未知のきっかけです")


def _clean_link(lk, later_ids, sound_groups):
    if not isinstance(lk, dict):
        raise ValueError("つなぎの形が不正です")
    when = lk.get("when")
    if when not in LINK_WHENS:
        raise ValueError("つなぎの条件が不正です")
    out = {"when": when, "n": _int_in(lk.get("n"), 1, MAX_VALUE, "節目の数"),
           "op": lk.get("op")}
    op = out["op"]
    if op not in LINK_OPS:
        raise ValueError("つなぎの行動が不正です")
    if op in LINK_VALUE_OPS:
        # 自分より下のカードにだけつなげる（循環をデータの形で防ぐ）
        if lk.get("target") not in later_ids:
            raise ValueError("つなぐ先は自分より下のカードだけです")
        out["target"] = lk["target"]
        if op == "add":
            v = _int_in(lk.get("value", 1), -MAX_LINK_VALUE, MAX_LINK_VALUE,
                        "増やす数")
            if v == 0:
                raise ValueError("増やす数に0は使えません")
            out["value"] = v
    elif op in LINK_SWITCH_OPS:
        out["id"] = _str_in(lk.get("id"), 64, "切替先")
    else:   # stamp: 音イベント演出のスタンプ設定を借りて出す
        group = lk.get("group")
        if not isinstance(group, str) or (sound_groups is not None
                                          and group not in sound_groups):
            raise ValueError("スタンプの種類が不正です")
        out["group"] = group
    return out


def _clean_display(d, anims):
    d = d if isinstance(d, dict) else {}
    w = d.get("widget")
    if not isinstance(w, dict):
        return {}
    if w.get("type", "counter") != "counter":
        raise ValueError("未知の部品です")
    anim = w.get("anim", "pop")
    if anims is not None and anim not in anims:
        raise ValueError("増えたときの動きが不正です")
    style = w.get("style", "")
    label_style = w.get("label_style", "")
    unit_style = w.get("unit_style", "")
    for v in (style, label_style, unit_style):
        if not isinstance(v, str) or len(v) > 64:
            raise ValueError("見た目の指定が不正です")
    unit = w.get("unit", "")
    if not isinstance(unit, str) or len(unit.strip()) > MAX_UNIT_LEN:
        raise ValueError(f"単位は{MAX_UNIT_LEN}文字までです")
    return {"widget": {
        "type": "counter",
        "visible": w.get("visible", True) is True,
        "pos": _clean_pos(w.get("pos")),
        "size": min(1.0, max(0.02, float(w.get("size", 0.12)))),
        "style": style,
        "label": w.get("label", True) is True,
        # ラベル（名前）は数字と別の見た目・大きさにできる。
        # label_style "" = 数字と同じ見た目。label_scale = 数字に対する大きさ
        "label_style": label_style,
        "label_scale": min(MAX_LABEL_SCALE, max(MIN_LABEL_SCALE,
                                                float(w.get("label_scale", 0.55)))),
        # 単位（「回」など）は数字のすぐ後ろ。"" = 付けない。見た目・大きさは名前と同じ考え方
        "unit": unit.strip(),
        "unit_style": unit_style,
        "unit_scale": min(MAX_LABEL_SCALE, max(MIN_LABEL_SCALE,
                                               float(w.get("unit_scale", 0.55)))),
        "anim": anim,
    }}


def _clean_card(c, later_ids, sound_groups, anims):
    if not isinstance(c, dict):
        raise ValueError("カードの形が不正です")
    cid = _str_in(c.get("id"), MAX_ID_LEN, "カードID")
    reset = c.get("reset", "start")
    if reset not in RESETS:
        raise ValueError("リセットの指定が不正です")
    triggers = c.get("triggers") or []
    links = c.get("links") or []
    if not isinstance(triggers, list) or len(triggers) > MAX_TRIGGERS:
        raise ValueError(f"きっかけは{MAX_TRIGGERS}個までです")
    if not isinstance(links, list) or len(links) > MAX_LINKS:
        raise ValueError(f"つなぎは{MAX_LINKS}個までです")
    # 黙っているは1カードに1つまで（話したら0に戻すので、2つあると数え方が定まらない）
    if sum(isinstance(t, dict) and t.get("type") == "silence" for t in triggers) > 1:
        raise ValueError("「黙っている」は1つのカードに1つまでです")
    return {
        "id": cid,
        "name": _str_in(c.get("name"), MAX_NAME_LEN, "カード名"),
        "enabled": c.get("enabled", True) is True,
        "step": _int_in(c.get("step", 1), 1, MAX_STEP, "1回で増やす数"),
        "reset": reset,
        "triggers": [_clean_trigger(t, sound_groups) for t in triggers],
        "display": _clean_display(c.get("display"), anims),
        "links": [_clean_link(lk, later_ids, sound_groups) for lk in links],
    }


def _ids(raw):
    return [c.get("id") if isinstance(c, dict) else None for c in raw]


def validate_cards(raw, sound_groups=None, anims=None):
    """保存前の検証。1つでも不正なら ValueError（書き込む前に弾く）。

    sound_groups / anims は呼び出し側が渡す（soundfx は numpy を引くため
    ここでは import しない）。None なら種類の照合を省く。
    """
    if not isinstance(raw, list) or len(raw) > MAX_CARDS:
        raise ValueError(f"カードは{MAX_CARDS}枚までです")
    ids = _ids(raw)
    if len(set(ids)) != len(ids):
        raise ValueError("カードIDが重複しています")
    return [_clean_card(c, set(ids[i + 1:]), sound_groups, anims)
            for i, c in enumerate(raw)]


def load_cards(raw, sound_groups=None, anims=None):
    """読み込み用。壊れたカードだけ捨てて残りは使う（古いデータでも起動を止めない）。

    捨てたカードを指していたつなぎは、つなぎだけ捨てる。
    """
    if not isinstance(raw, list):
        return []
    raw = raw[:MAX_CARDS]
    ids = _ids(raw)
    out, seen = [], set()
    for i, c in enumerate(raw):
        if not isinstance(c, dict) or c.get("id") in seen:
            continue
        later = set(ids[i + 1:])
        links = c.get("links") if isinstance(c.get("links"), list) else []
        kept = []
        for lk in links[:MAX_LINKS]:
            try:
                kept.append(_clean_link(lk, later, sound_groups))
            except (TypeError, ValueError, AttributeError):
                continue
        triggers = c.get("triggers") if isinstance(c.get("triggers"), list) else []
        first = next((t for t in triggers
                      if isinstance(t, dict) and t.get("type") == "silence"), None)
        triggers = [t for t in triggers if not (isinstance(t, dict)
                    and t.get("type") == "silence" and t is not first)]
        try:
            card = _clean_card({**c, "links": [], "triggers": triggers}, later,
                               sound_groups, anims)
        except (TypeError, ValueError, AttributeError):
            continue
        card["links"] = kept
        seen.add(card["id"])
        out.append(card)
    # 下のカードが捨てられていたら、そこを指すつなぎも外す
    alive = {c["id"] for c in out}
    for c in out:
        c["links"] = [lk for lk in c["links"]
                      if lk["op"] not in LINK_VALUE_OPS or lk["target"] in alive]
    return out


def load_values(raw, cards):
    """値ファイルの読み込み。知らないカードは捨て、値は 0〜MAX_VALUE に丸める"""
    raw = raw if isinstance(raw, dict) else {}
    vals = raw.get("values") if isinstance(raw.get("values"), dict) else {}
    reached = raw.get("reached") if isinstance(raw.get("reached"), dict) else {}
    ids = {c["id"] for c in cards}
    values = {}
    for cid in ids:
        try:
            values[cid] = _clamp_value(vals.get(cid, 0))
        except (TypeError, ValueError):
            values[cid] = 0
    reached = {k: True for k, v in reached.items()
               if v is True and isinstance(k, str) and k.split(":", 1)[0] in ids}
    return {"values": values, "reached": reached}


def count_words(text, words):
    """正規化した text に words が何回出たか（重ならない最長一致で数える）。

    「やば」と「やばい」を両方登録しても「やばい」1回は1回と数える。
    """
    t = voicecmd.normalize(text)
    forms = sorted({voicecmd.normalize(w) for w in words} - {""},
                   key=len, reverse=True)
    n, i = 0, 0
    while i < len(t):
        for f in forms:
            if t.startswith(f, i):
                n += 1
                i += len(f)
                break
        else:
            i += 1
    return n


def _reach_key(card_id, index):
    return f"{card_id}:{index}"


class GambitEngine:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.cards = []
        self._by_id = {}
        self.values = {}
        self.reached = {}
        self._last_sound = {}   # (card_id, trigger index) → 最後に発火した時刻
        # しばらく黙っている: 話者ごとの最後に声を聞いた時刻と、きっかけごとの
        # (どの「聞いた時刻」からの沈黙か, その沈黙で何回数えたか)
        self._heard = {"self": clock(), "guest": clock()}
        self._silence_fired = {}
        self._paused = True     # エンジンが止まっている間は沈黙を数えない

    def load(self, cards, values=None):
        """検証済みのカードと値を読み込む（load_cards / load_values の結果を渡す）"""
        self.cards = list(cards)
        self._by_id = {c["id"]: c for c in self.cards}
        v = load_values(values or {}, self.cards)
        self.values, self.reached = v["values"], v["reached"]
        self._last_sound = {}
        # 組み方を保存した時点から数え直す（設定中の沈黙ですぐ発火させない）
        now = self._clock()
        self._heard = {"self": now, "guest": now}
        self._silence_fired = {}

    def has_silence(self):
        """「しばらく黙っている」のきっかけを持つカードがあるか（毎秒の見回りの要否）"""
        return any(t["type"] == "silence" for c in self.cards if c["enabled"]
                   for t in c["triggers"])

    def snapshot(self):
        """値ファイルに書く形"""
        return {"values": dict(self.values), "reached": dict(self.reached)}

    # ---- 出来事 ----

    def on_final(self, text, speaker):
        """確定字幕1行。speaker は "self" / "guest" """
        effects = []
        for c in self.cards:
            if not c["enabled"]:
                continue
            n = sum(count_words(text, t["words"]) for t in c["triggers"]
                    if t["type"] == "word" and _speaker_ok(t, speaker))
            if n:
                self._change(c, n * c["step"], effects, 0)
        return effects

    def on_sound(self, group, speaker):
        """音イベント1回。カードごと・きっかけごとにクールダウンを見る"""
        effects = []
        now = self._clock()
        for c in self.cards:
            if not c["enabled"]:
                continue
            hits = 0
            for i, t in enumerate(c["triggers"]):
                if (t["type"] != "sound" or t["group"] != group
                        or not _speaker_ok(t, speaker)):
                    continue
                key = (c["id"], i)
                last = self._last_sound.get(key)
                if last is not None and now - last < t["cooldown"]:
                    continue
                self._last_sound[key] = now
                hits += 1
            if hits:
                self._change(c, hits * c["step"], effects, 0)
        return effects

    def heard(self, speaker):
        """声を聞いた（途中字幕・確定字幕・音イベント）。沈黙の数え直し。

        この沈黙で数えていたカードは0に戻す（「いま何秒黙っているか」を出すため）
        """
        effects = []
        for c in self.cards:
            fired = False
            for i, t in enumerate(c["triggers"]):
                if t["type"] == "silence" and _speaker_ok(t, speaker):
                    fired |= self._silence_fired.pop((c["id"], i), None) is not None
            if fired and c["enabled"]:
                self._reset(c, effects)
        self._heard[speaker] = self._clock()
        return effects

    def on_tick(self, running, collab):
        """毎秒の見回り。running=False（エンジン停止中）は沈黙を数えない。

        再び動き出したら、そこから数え直す。黙り続けている間は seconds ごとに
        数える（5秒なら 5・10・15秒で1回ずつ）。見回りが遅れても、過ぎた分は
        まとめて数える。collab でなければ相手の沈黙は数えない。
        """
        if not running:
            self._paused = True
            return []
        now = self._clock()
        if self._paused:
            # 止めていた間は数えない。続きはここから（数えた印は残し、話したら0に戻す）
            self._paused = False
            self._heard = {"self": now, "guest": now}
            return []
        effects = []
        for c in self.cards:
            if not c["enabled"]:
                continue
            hits = 0
            for i, t in enumerate(c["triggers"]):
                if t["type"] != "silence":
                    continue
                if t["speaker"] == "guest" and not collab:
                    continue
                ref = (max(self._heard.values()) if t["speaker"] == "any"
                       else self._heard[t["speaker"]])
                key = (c["id"], i)
                total = int((now - ref) // t["seconds"])
                if total <= 0:
                    continue
                prev_ref, prev = self._silence_fired.get(key, (None, 0))
                done = prev if prev_ref == ref else 0
                if total > done:
                    self._silence_fired[key] = (ref, total)
                    hits += total - done
            if hits:
                self._change(c, hits * c["step"], effects, 0)
        return effects

    def bump(self, card_id, op="add", value=1):
        """手動・ボタン・Stream Deck・MCP から。無効なカードでも手直しはできる"""
        c = self._by_id.get(card_id)
        if c is None:
            raise KeyError(card_id)
        effects = []
        if op == "reset":
            self._reset(c, effects)
        elif op == "add":
            self._change(c, int(value), effects, 0)
        else:
            raise ValueError("未知の操作です")
        return effects

    def reset_on_start(self):
        """エンジン起動時。「起動ごと」のカードを0に戻す"""
        effects = []
        for c in self.cards:
            if c["reset"] == "start":
                self._reset(c, effects)
        return effects

    # ---- 内部 ----

    def _reset(self, c, effects):
        old = self.values.get(c["id"], 0)
        self.values[c["id"]] = 0
        for i in range(len(c["links"])):
            self.reached.pop(_reach_key(c["id"], i), None)
        effects.append({"kind": "value", "id": c["id"], "value": 0,
                        "delta": -old})

    def _change(self, c, delta, effects, depth):
        old = self.values.get(c["id"], 0)
        new = _clamp_value(old + delta)
        if new == old:
            return
        self.values[c["id"]] = new
        effects.append({"kind": "value", "id": c["id"], "value": new,
                        "delta": new - old})
        if new < old or depth >= MAX_DEPTH:
            return     # 減ったときは節目を発火しない／連鎖は MAX_DEPTH 段まで
        for i, lk in enumerate(c["links"]):
            if lk["when"] == "every":
                times = new // lk["n"] - old // lk["n"]
            else:
                key = _reach_key(c["id"], i)
                times = int(old < lk["n"] <= new and key not in self.reached)
                if times:
                    self.reached[key] = True
            if times:
                self._fire(c, lk, times, effects, depth + 1)

    def _fire(self, c, lk, times, effects, depth):
        op = lk["op"]
        if op in LINK_VALUE_OPS:
            target = self._by_id.get(lk["target"])
            if target is None or not target["enabled"]:
                return
            if op == "add":
                # 節目を複数またいだら、またいだ数だけ増やす
                self._change(target, lk["value"] * times, effects, depth)
            else:
                self._reset(target, effects)
        elif op in LINK_SWITCH_OPS:
            # 切替は何度やっても同じなので、またいだ数によらず1回
            effects.append({"kind": "action", "card": c["id"],
                            "action": {"type": op, "id": lk["id"]}})
        else:
            effects.append({"kind": "action", "card": c["id"],
                            "action": {"type": "stamp", "group": lk["group"]}})


def _speaker_ok(trigger, speaker):
    return trigger["speaker"] in ("any", speaker)
