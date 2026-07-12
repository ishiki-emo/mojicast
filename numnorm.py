"""
漢数字 → 算用数字の正規化（逆テキスト正規化・ヒューリスティック）

k2 は文字ベースASRのため数値を漢数字で出しがち（三十五 → 35 にしたい）。
確定テキストの後処理として変換する。誤爆（一緒→1緒 等）を避けるため:

  1) 2文字以上の漢数字連続（三十五・二千二十六 …）はほぼ確実に数値 → 変換
  2) 1文字の漢数字は「助数詞が続くときだけ」変換（一人→1人 / 一緒はそのまま）
  3) 紛らわしい語はガードで除外（十分=じゅうぶん、三日月、一時的、何十 …）

完璧な判定は形態素解析なしには不可能なので、誤爆を見つけたらガードに追記する。
"""
import re

_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SMALL = {"十": 10, "百": 100, "千": 1000}
_BIG = {"万": 10 ** 4, "億": 10 ** 8, "兆": 10 ** 12}
_NUMCHARS = set(_DIGITS) | set(_SMALL) | set(_BIG)

# 1文字の漢数字でも変換してよい後続の助数詞・単位
_COUNTERS = set(
    "円個人回時分秒歳才本枚匹頭台冊話曲位級勝敗年月日週点倍度つ杯"
    "個件語文字行列品種色戦球打席発問門番組"
) - {"番"}   # 一番(いちばん)の誤爆が多いため 番 は除外（番組は下で個別対応）

# (直前の1文字) がこれなら変換しない（何十人・数百人・幾千 など概数表現）
_PREV_GUARD = set("何数幾")


def _parse(run: str):
    """漢数字の連続を整数へ。解釈できなければ None"""
    # 位取りなしの並び（二〇二六 → 2026）
    if all(c in _DIGITS for c in run):
        return int("".join(str(_DIGITS[c]) for c in run))
    total = section = digit = 0
    prev_small = 10 ** 4
    for c in run:
        if c in _DIGITS:
            if digit:
                return None            # 「一二十」のような混在は不正
            digit = _DIGITS[c]
        elif c in _SMALL:
            unit = _SMALL[c]
            if unit >= prev_small:
                return None            # 「十百」のような逆順は不正
            prev_small = unit
            section += (digit or 1) * unit
            digit = 0
        else:                          # 万・億・兆
            section += digit
            total += (section or 1) * _BIG[c]
            section = digit = 0
            prev_small = 10 ** 4
    return total + section + digit


def _format(val: int) -> str:
    """放送字幕風の表記にする: 15000 → 1万5000（万/億/兆の単位を保持）"""
    if val < 10000:
        return str(val)
    units = [("兆", 10 ** 12), ("億", 10 ** 8), ("万", 10 ** 4)]
    out = ""
    rest = val
    for name, u in units:
        q, rest = divmod(rest, u)
        if q:
            out += f"{q}{name}"
    if rest:
        out += str(rest)
    return out


def _guarded(run, prev, nxt, nxt2):
    """変換してはいけないパターン（誤爆ガード）"""
    if prev in _PREV_GUARD:
        return True                    # 何十人・数百年 など概数
    if run == "十" and nxt == "分":
        return True                    # 十分(じゅうぶん) ※三十分などは変換される
    if len(run) == 1:
        if nxt == "日" and nxt2 in ("月", "酔"):
            return True                # 三日月・二日酔い
        if run == "一" and nxt == "時" and nxt2 in ("的", "期"):
            return True                # 一時的・一時期
        if run == "一" and nxt == "杯":
            return True                # 一杯(いっぱい=たくさん)の誤爆防止
        if nxt == "人" and nxt2 == "前" and run == "一":
            return True                # 一人前(いちにんまえ=一人分/一人前の実力)
    return False


def normalize_numbers(text: str) -> str:
    """テキスト中の漢数字を算用数字へ変換して返す（変換なしなら原文のまま）"""
    if not text:
        return text
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c not in _NUMCHARS:
            out.append(c)
            i += 1
            continue
        j = i
        while j < n and text[j] in _NUMCHARS:
            j += 1
        run = text[i:j]
        prev = text[i - 1] if i > 0 else ""
        nxt = text[j:j + 1]
        nxt2 = text[j + 1:j + 2]
        val = _parse(run)

        convert = (val is not None
                   and (len(run) >= 2 or nxt in _COUNTERS or
                        text[j:j + 2] == "番組")
                   and not _guarded(run, prev, nxt, nxt2))
        if not convert:
            out.append(run)
            i = j
            continue

        # 小数: 「三点五」→ 3.5（点の両側が数字のときだけ）
        if nxt == "点" and text[j + 1:j + 2] in _DIGITS:
            k = j + 1
            while k < n and text[k] in _DIGITS:
                k += 1
            frac = "".join(str(_DIGITS[ch]) for ch in text[j + 1:k])
            out.append(f"{val}.{frac}")
            i = k
        else:
            out.append(_format(val))
            i = j
    return "".join(out)


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    CASES = [
        # (入力, 期待)
        ("レベルは三十五です", "レベルは35です"),
        ("二千二十六年七月十三日", "2026年7月13日"),
        ("三十分待って", "30分待って"),
        ("十分に楽しんだ", "十分に楽しんだ"),          # じゅうぶん は変換しない
        ("あと十分で始めます", "あと十分で始めます"),   # 曖昧なので安全側（変換しない）
        ("一緒に行こう", "一緒に行こう"),
        ("一番好きなやつ", "一番好きなやつ"),
        ("一人で来た", "1人で来た"),
        ("視聴者が三人います", "視聴者が3人います"),
        ("一時的なエラー", "一時的なエラー"),
        ("一時から配信", "1時から配信"),
        ("三日月がきれい", "三日月がきれい"),
        ("三日後にリベンジ", "3日後にリベンジ"),
        ("二日酔いがつらい", "二日酔いがつらい"),
        ("何十人も来た", "何十人も来た"),
        ("数百年前の話", "数百年前の話"),
        ("一万円課金した", "1万円課金した"),
        ("一万五千円もした", "1万5000円もした"),
        ("登録者が十二万人いった", "登録者が12万人いった"),
        ("万が一のとき", "万が一のとき"),
        ("三点五倍です", "3.5倍です"),
        ("スパチャ一件", "スパチャ1件"),
        ("いっぱいある", "いっぱいある"),
        ("もう一杯飲む", "もう一杯飲む"),              # 曖昧なので安全側
        ("三杯目です", "3杯目です"),
        ("第二百五十回", "第250回"),
        ("一つずつやる", "1つずつやる"),
    ]
    ok = 0
    for src, want in CASES:
        got = normalize_numbers(src)
        mark = "ok" if got == want else "NG"
        if got == want:
            ok += 1
        print(f"  {mark}: {src} → {got}" + ("" if got == want else f"  (期待: {want})"))
    print(f"\n{ok}/{len(CASES)} pass")
