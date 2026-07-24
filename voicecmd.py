"""ボイスコマンド（音声によるアプリ操作）

「〈ウェイクワード〉、レイアウトを下部バーに」のような発話を確定字幕から
横取りして解釈する。認識テキストは表記ゆれ（カタカナ/ひらがな・句読点・
空白）が大きいため、両辺を正規化してから照合する。

ウェイクワードはユーザー定義（複数可）。「モジキャスト」が「文字キャスト」と
認識されるような誤変換形も、ゆれ表記として並べて登録すれば拾える。

MVPはレイアウト切替のみ:
    〈ウェイク〉レイアウトを〈レイアウト名〉に   … 名前で指定
    〈ウェイク〉レイアウト2                      … 一覧の並び順（1始まり）で指定
名前はレイアウト一覧（boxes.json）の name と照合する。「フリー（枠なし）」の
ような括弧付き注釈は外した形でもマッチする。
"""
import re
import unicodedata

# 照合時に無視する文字（句読点・空白・記号類）
_IGNORE_RE = re.compile(r"[\s、。，．,.!！?？・:：;；「」『』()（）\-〜~…]")
# レイアウト名の括弧付き注釈「（枠なし)」等
_PAREN_RE = re.compile(r"（[^）]*）|\([^)]*\)")

# 漢数字→アラビア数字（1〜99。「レイアウト二番」等の取りこぼし対策。
# 数字正規化(numnorm)を通らない経路・モデルでも番号指定が効くようにする）
_KANJI_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_NUM_RE = re.compile(r"[一二三四五六七八九]?十[一二三四五六七八九]?"
                           r"|[一二三四五六七八九]")


def _arabicize(text):
    """テキスト中の漢数字（1〜99）をアラビア数字へ置き換える"""
    def conv(m):
        s = m.group(0)
        if "十" not in s:
            return str(_KANJI_DIGITS[s])
        tens, _, ones = s.partition("十")
        return str((_KANJI_DIGITS[tens] if tens else 1) * 10
                   + (_KANJI_DIGITS[ones] if ones else 0))
    return _KANJI_NUM_RE.sub(conv, text)

# 「おまかせで変えて」系の言い回し（正規化後の形で照合）
_RANDOM_WORDS = ("かえて", "かえよう", "かえろ", "変えて", "変えよう",
                 "へんこう", "変更", "ちぇんじ", "しゃっふる",
                 "おまかせ", "らんだむ")

# 翻訳先の言い回し → translate_lang コード。特定的な言い方（繁体・広東等）を
# 先に置き、「中国語」単独は簡体へ落とす
_TRANS_LANGS = (
    ("zh_tw", "中国語（繁体字）", ("繁体", "はんたい", "台湾", "たいわん")),
    ("zh_hk", "広東語", ("広東", "かんとん", "香港", "ほんこん")),
    ("zh", "中国語（簡体字）", ("中国語", "ちゅうごくご", "簡体", "かんたい")),
    ("id", "インドネシア語", ("いんどねしあ",)),
    ("ko", "韓国語", ("韓国", "かんこく", "はんぐる")),
    ("ja", "日本語", ("日本語", "にほんご")),
    ("en", "英語", ("英語", "えいご")),
)
# オフ判定を先に行う（「翻訳オフにして」の「して」でオンに誤爆しないよう）
_TRANS_OFF = ("おふ", "きって", "切って", "けして", "消して", "やめて",
              "停止", "ていし", "なし")
_TRANS_ON = ("おん", "つけて", "付けて", "開始", "かいし", "あり", "して")


def normalize(text):
    """照合用の正規化: NFKC → 記号除去 → カタカナ→ひらがな → 小文字化"""
    t = unicodedata.normalize("NFKC", text or "")
    t = _IGNORE_RE.sub("", t)
    t = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in t)
    return t.lower()


def strip_wake(text, wakes):
    """先頭のウェイクワードを剥がす。無ければ None（＝コマンドではない）"""
    nt = normalize(text)
    for w in wakes or []:
        nw = normalize(w)
        if nw and nt.startswith(nw):
            return nt[len(nw):]
    return None


def match_custom(rest, commands):
    """カスタムコマンドの言い回し照合（正規化して包含・最長一致）"""
    best, best_len = None, 0
    for c in commands or []:
        for ph in c.get("phrases") or []:
            np = normalize(ph)
            if np and np in rest and len(np) > best_len:
                best, best_len = c, len(np)
    return best


def _name_forms(item):
    """名前つき項目の照合形（正規化済み・長い順）。括弧注釈を外した形も含む"""
    name = item.get("name") or ""
    forms = {normalize(name), normalize(_PAREN_RE.sub("", name))}
    return sorted((f for f in forms if f), key=len, reverse=True)


def _match_named(rest, items):
    """rest に名前が含まれる項目を探す（最長一致）"""
    best, best_len = None, 0
    for it in items or []:
        for form in _name_forms(it):
            if form in rest and len(form) > best_len:
                best, best_len = it, len(form)
                break   # この項目はこれ以上長い形は無い（長い順のため）
    return best


def parse(text, wakes, boxes, presets=None, scenes=None, commands=None):
    """確定テキストをコマンド解釈する。

    返り値:
        None                                   … ウェイクワードなし（普通の字幕）
        {"action": "custom", "command": dict}  … カスタムコマンド（言い回し→動作の登録）
        {"action": "scene", "id", "name"}      … シーン切替（デザイン＋レイアウト一括）
        {"action": "scene_random"}             … シーンをおまかせで切替
        {"action": "box", "id", "name"}        … レイアウト切替
        {"action": "box_random"}               … レイアウトをおまかせで切替
        {"action": "preset", "id", "name"}     … 字幕デザイン切替
        {"action": "preset_random"}            … 字幕デザインをおまかせで切替
        {"action": "translate_lang", "lang", "label"} … 翻訳先の切替（翻訳ONも兼ねる）
        {"action": "translate_on" / "translate_off"}  … 翻訳のオン/オフ
        {"action": "unknown", "rest": str}     … ウェイクは合ったが解釈不能
    """
    rest = strip_wake(text, wakes)
    if rest is None:
        return None

    # カスタムコマンドが最優先（ユーザーが明示的に登録した言い回しなので、
    # 組み込みの解釈より先に拾う。組み込みと同じ言い回しの上書きも可能）
    c = match_custom(rest, commands)
    if c is not None:
        return {"action": "custom", "command": c}

    # 翻訳: 「翻訳を英語に」「翻訳オン/オフ」「翻訳して」（「翻訳」が門番）
    if "翻訳" in rest or "ほんやく" in rest:
        for code, label, keys in _TRANS_LANGS:
            if any(k in rest for k in keys):
                return {"action": "translate_lang", "lang": code, "label": label}
        if any(k in rest for k in _TRANS_OFF):
            return {"action": "translate_off"}
        if any(k in rest for k in _TRANS_ON):
            return {"action": "translate_on"}
        return {"action": "unknown", "rest": rest}

    # 番号指定: 「れいあうと2」「デザイン3」「シーン2」「2番」「レイアウト二番」
    # （漢数字も吸収）。「N番」だけの言い方はレイアウト扱い（従来互換）
    rest_n = _arabicize(rest)
    m = re.search(r"しーん(?:を)?(\d{1,2})", rest_n)
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < len(scenes or []):
            s = scenes[i]
            return {"action": "scene", "id": s.get("id"), "name": s.get("name")}
    m = re.search(r"(?:でざいん|すたいる)(?:を)?(\d{1,2})", rest_n)
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < len(presets or []):
            p = presets[i]
            return {"action": "preset", "id": p.get("id"), "name": p.get("name")}
    m = (re.search(r"れいあうと(?:を)?(\d{1,2})", rest_n)
         or re.search(r"(\d{1,2})(?:ばん|番)", rest_n))
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < len(boxes or []):
            b = boxes[i]
            return {"action": "box", "id": b.get("id"), "name": b.get("name")}

    # 名前指定: シーン名／レイアウト名／デザイン名が含まれていれば切替
    # （ウェイクワードが門番なので文法は厳密に要求しない。「〜にして」
    # 「歌枠モード」等の語尾は自然に無視される）。
    # 同名がある場合の優先順: シーン > レイアウト > デザイン
    # （シーン名はユーザーが意図して付けた呼び名なので最優先）
    s = _match_named(rest, scenes)
    if s is not None:
        return {"action": "scene", "id": s.get("id"), "name": s.get("name")}
    b = _match_named(rest, boxes)
    if b is not None:
        return {"action": "box", "id": b.get("id"), "name": b.get("name")}
    p = _match_named(rest, presets)
    if p is not None:
        return {"action": "preset", "id": p.get("id"), "name": p.get("name")}

    # 指定なしの切替: 「レイアウト変えて」「デザインをチェンジ」→ おまかせ
    if any(w in rest for w in _RANDOM_WORDS):
        if "しーん" in rest:
            return {"action": "scene_random"}
        if "れいあうと" in rest:
            return {"action": "box_random"}
        if "でざいん" in rest or "すたいる" in rest:
            return {"action": "preset_random"}

    return {"action": "unknown", "rest": rest}
