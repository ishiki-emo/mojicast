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


def _box_forms(box):
    """レイアウト1件の照合形（正規化済み・長い順）。括弧注釈を外した形も含む"""
    name = box.get("name") or ""
    forms = {normalize(name), normalize(_PAREN_RE.sub("", name))}
    return sorted((f for f in forms if f), key=len, reverse=True)


def _match_box(rest, boxes):
    """rest に名前が含まれるレイアウトを探す（最長一致）"""
    best, best_len = None, 0
    for b in boxes or []:
        for form in _box_forms(b):
            if form in rest and len(form) > best_len:
                best, best_len = b, len(form)
                break   # このboxはこれ以上長い形は無い（長い順のため）
    return best


def parse(text, wakes, boxes):
    """確定テキストをコマンド解釈する。

    返り値:
        None                                   … ウェイクワードなし（普通の字幕）
        {"action": "box", "id", "name"}        … レイアウト切替
        {"action": "unknown", "rest": str}     … ウェイクは合ったが解釈不能
    """
    rest = strip_wake(text, wakes)
    if rest is None:
        return None

    # 番号指定: 「れいあうと2」「2番」（数字は認識側でアラビア数字化済み）
    m = (re.search(r"れいあうと(?:を)?(\d{1,2})", rest)
         or re.search(r"(\d{1,2})(?:ばん|番)", rest))
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < len(boxes or []):
            b = boxes[i]
            return {"action": "box", "id": b.get("id"), "name": b.get("name")}

    # 名前指定: レイアウト名が含まれていれば切替（ウェイクワードが門番なので
    # 文法は厳密に要求しない。「〜にして」「〜に変更」等の語尾は自然に無視される）
    b = _match_box(rest, boxes)
    if b is not None:
        return {"action": "box", "id": b.get("id"), "name": b.get("name")}

    return {"action": "unknown", "rest": rest}
