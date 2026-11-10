"""
字幕デザイン（プリセット: presets.json）と字幕枠（ボックス: boxes.json）の項目検証

デザインと枠は OBS のブラウザソース（overlay.html）の見た目にそのまま使われ、
mojipack で他の人と共有もされる。wordstore.validate_effect と同じ方針で、
項目の許可リスト・型・色（#rrggbb）・フォント名・数値の範囲を確かめる。

- 保存（POST /api/presets・/api/boxes）: validate_items … 1項目でも不正なら ValueError
- 読み込み・取り込み: sanitize_item … 不正な項目だけを捨てて残りを使う。
  元のファイルは書き換えない（load_effects と同じ）

許可リストは defaults/presets.json・defaults/boxes.json、デザイン画面（ui/style.html・
ui/legacy/style.html）が書き出す項目、overlay / ui/fx.js が読む項目、AI 連携
（tools/mcp）の項目から作った。数値の範囲は画面のスライダーより広く取っている
（画面・AI 連携・過去の版で作られた値を落とさないため。目的は壊れた値・極端な値を弾くこと）。
項目を足すときはここにも足すこと（tests/test_style_schema.py が defaults と画面の項目を突き合わせる）。
"""
import math
import re

HEX = re.compile(r"#[0-9a-fA-F]{6}")
# 列挙値（アニメーション名・表示モードなど）は英小文字の識別子だけ。
# 値の一覧そのものは overlay 側にあり、知らない値は overlay が既定に倒す
TOKEN = re.compile(r"[a-z][a-z0-9_-]{0,39}")
LANG = re.compile(r"[a-z]{2,3}(?:_[a-z]{2,4})?")
# CSS の font-family として、名前と「,」区切りの代替だけを許す（関数・宣言・タグは不可）。
# validate_effect のフォントと同じ規則
_FONT_BAD = re.compile(r"[;:{}()<>\\\x00-\x1f\x7f]")

MAX_ITEMS = 500


def _num(lo, hi):
    def check(v):
        return (type(v) in (int, float) and math.isfinite(v) and lo <= v <= hi)
    return check


def _bool(v):
    return isinstance(v, bool)


def _color(v):
    """#rrggbb、または空文字（なし・本文と同じ、の意味で使う項目がある）"""
    return isinstance(v, str) and (v == "" or HEX.fullmatch(v) is not None)


def _font(v):
    return isinstance(v, str) and len(v) <= 512 and not _FONT_BAD.search(v)


def _token(v):
    # 空文字も通す: 旧UI（ui/legacy/style.html）は選択肢に無い値（例: 吹き出しモード）を
    # 開くと select が空になり "" で保存する。overlay は空を既定に倒す（従来どおり）
    return isinstance(v, str) and (v == "" or TOKEN.fullmatch(v) is not None)


def _text(maxlen, required=False):
    # required: 空文字は不可（従来の保存時の確認「id と name があること」と同じ強さ）
    def check(v):
        return (isinstance(v, str) and len(v) <= maxlen
                and (not required or v != "")
                and not any(ord(c) < 32 or ord(c) == 0x7f for c in v))
    return check


# 訳文の書式（プリセット直下の共通値と、trStyles[言語] の上書きで共通）
TR_FIELDS = {
    "enScale": _num(0.05, 3),
    "enOpacity": _num(0, 1),
    "enColor": _color,
    "enFont": _font,
    "enDeco": _bool,
    "enOutlineColor": _color,
    "enOutlineWidth": _num(0, 50),
    "enGlowColor": _color,
    "enGlowSize": _num(0, 200),
}


def _tr_styles(v):
    if not isinstance(v, dict) or len(v) > 50:
        return False
    for lang, st in v.items():
        if not (isinstance(lang, str) and LANG.fullmatch(lang) and isinstance(st, dict)):
            return False
        for k, x in st.items():
            if k not in TR_FIELDS or not TR_FIELDS[k](x):
                return False
    return True


def _clean_tr_styles(v):
    """読み込み用: 言語ごとの上書きのうち、正しい項目だけを残す"""
    if not isinstance(v, dict):
        return None
    out = {}
    for lang, st in list(v.items())[:50]:
        if not (isinstance(lang, str) and LANG.fullmatch(lang) and isinstance(st, dict)):
            continue
        out[lang] = {k: x for k, x in st.items() if k in TR_FIELDS and TR_FIELDS[k](x)}
    return out


def _spots(v):
    """吹き出しの置き場所 [{x, y}, ...]（% 単位）"""
    return (isinstance(v, list) and len(v) <= 50
            and all(isinstance(s, dict) and set(s) <= {"x", "y"}
                    and _num(-100, 200)(s.get("x", 0)) and _num(-100, 200)(s.get("y", 0))
                    for s in v))


_COMMON = {
    "id": _text(200, required=True),
    "name": _text(500, required=True),
    "desc": _text(2000),
}

PRESET_FIELDS = {
    **_COMMON,
    "font": _font,
    "weight": _num(1, 1000),
    "size": _num(1, 400),
    "color": _color,
    "hotColor": _color,
    "outlineColor": _color,
    "outlineWidth": _num(0, 50),
    "extrude": _num(0, 50),
    "extrudeDir": _num(0, 360),
    "glow": _color,
    "glowSize": _num(0, 200),
    "animIn": _token,
    "shadow": _bool,
    "partialOpacity": _num(0, 1),
    "partialFx": _bool,
    "letterSpacing": _num(-1, 2),
    "displayMode": _token,
    "trStyles": _tr_styles,
    **TR_FIELDS,
}

BOX_FIELDS = {
    **_COMMON,
    # 位置・大きさ（ブラウザソースに対する %）
    "x": _num(-100, 200), "y": _num(-100, 200),
    "w": _num(0, 200), "h": _num(0, 200),
    "mode": _token,
    "align": _token,
    "maxLines": _num(0, 200),
    "padding": _num(0, 500),
    "bg": _color,
    "bgOpacity": _num(0, 1),
    "radius": _num(0, 500),
    "borderColor": _color,
    "borderWidth": _num(0, 100),
    "smooth": _bool,
    "smoothMs": _num(0, 10000),
    "autoClear": _bool,
    "clearSec": _num(0, 3600),
    "fadePx": _num(0, 1000),
    # 3D の傾き
    "tiltX": _num(-90, 90), "tiltY": _num(-90, 90), "tiltZ": _num(-180, 180),
    "perspective": _num(50, 10000),
    # 縦書き・リリック
    "lifeSec": _num(0, 600),
    "maxChunks": _num(0, 200),
    "vertRate": _num(0, 1000),
    "rotate": _num(-180, 180),
    "sizeJitter": _num(0, 5),
    "stagger": _num(0, 10000),
    "lyricPartial": _bool,
    "lyricSplit": _token,
    "lyricScale": _num(0.05, 10),
    "lyricMood": _token,
    "lyricMaxScenes": _num(0, 20),
    "lyricPattern": _token,
    # 吹き出し
    "bubbleDir": _token,
    "bubbleFill": _color,
    "bubbleFillOpacity": _num(0, 1),
    "bubbleInk": _color,
    "bubbleInkOn": _bool,
    "bubbleLife": _num(0, 600),
    "bubbleMinScale": _num(0, 5),
    "bubblePartial": _bool,
    "bubbleScale": _num(0.05, 10),
    "bubbleShape": _token,
    "bubbleStroke": _color,
    "bubbleStrokeWidth": _num(0, 100),
    "bubbleAlign": _token,
    "bubbleArrange": _token,
    "bubbleHistory": _num(0, 50),
    "bubbleSpotW": _num(0, 200),
    "bubbleSpotH": _num(0, 200),
    "bubbleSpots": _spots,
    "tailOn": _bool,
    "tailLen": _num(0, 2000),
    "tailX": _num(-100, 200), "tailY": _num(-100, 200),
    "guestTailX": _num(-100, 200), "guestTailY": _num(-100, 200),
}

FIELDS = {"preset": PRESET_FIELDS, "box": BOX_FIELDS}
_LABEL = {"preset": "デザイン", "box": "レイアウト"}


def validate_item(kind, item):
    """保存用: 1件を検証する。不正なら ValueError（項目名を含む）"""
    fields = FIELDS[kind]
    label = _LABEL[kind]
    if not isinstance(item, dict):
        raise ValueError(f"{label}はオブジェクトで指定してください")
    for k in ("id", "name"):
        if k not in item:
            raise ValueError(f"{label}の {k} がありません")
    for k, v in item.items():
        if k not in fields:
            raise ValueError(f"{label}に対応していない項目があります: {str(k)[:40]}")
        if not fields[k](v):
            raise ValueError(f"{label}の {k} の値が正しくありません")
    return dict(item)


def validate_items(kind, items):
    """保存用: 一覧全体を検証する（空・件数超過も不可）。
    id の重複は従来どおり通す（既に重複している人の保存を止めないため）"""
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ValueError(f"{_LABEL[kind]}は1〜{MAX_ITEMS}件の配列で指定してください")
    return [validate_item(kind, it) for it in items]


def sanitize_item(kind, item):
    """読み込み・取り込み用: 不正な項目を捨てた写しを返す。id か name が不正なら None"""
    if not isinstance(item, dict):
        return None
    fields = FIELDS[kind]
    out = {}
    for k, v in item.items():
        check = fields.get(k)
        if check is None:
            continue
        if k == "trStyles":
            v = _clean_tr_styles(v)
            if v is None:
                continue
        elif not check(v):
            continue
        out[k] = v
    if "id" not in out or "name" not in out:
        return None
    return out


def sanitize_items(kind, items):
    """読み込み用: 一覧から不正な項目・件を除く（元のリストは変えない）"""
    if not isinstance(items, list):
        return []
    out = []
    for it in items[:MAX_ITEMS]:
        clean = sanitize_item(kind, it)
        if clean is not None:
            out.append(clean)
    return out
