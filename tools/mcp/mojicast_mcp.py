# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]   # 2.x は FastMCP が MCPServer に改名され API も変わった
# ///
"""Mojicast の字幕デザイン・レイアウトを AI から調整する MCP サーバ（試作）

起動中の Mojicast（既定 http://127.0.0.1:8765）の HTTP API を中継するだけで、
Mojicast 本体には組み込まない（配布物の依存を増やさない）。

役割分担:
- OBS MCP:      ブラウザソース（字幕レイヤー全体）の位置・大きさ、画面のスクリーンショット
- このサーバ:   レイヤーの中での字幕枠の位置（レイアウト）、フォント・色などのデザイン、
                試し表示（話さずに見た目を確かめる）

変更は配信画面に即反映される。直前の変更は undo で戻せる（このサーバの起動中のみ）。

登録例（Claude Code）:
  claude mcp add mojicast -s user -- uv run --script E:\\AIProject\\ReazonSpeech\\tools\\mcp\\mojicast_mcp.py
ポートを変えている場合は環境変数 MOJICAST_PORT で指定する。
"""
import json
import os
import re
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP

PORT = int(os.environ.get("MOJICAST_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"

mcp = FastMCP("mojicast")

# ---------------- 項目の定義（AI に意味を渡し、書き間違いを弾く） ----------------

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LINE_ANIMS = ["slide", "fade", "pop", "bounce", "drop", "flip", "blur", "wipe",
              "typewriter", "none"]

# (型, 下限, 上限 or 選択肢, 説明)。型: num / int / bool / color / color? / str / enum
DESIGN_FIELDS = {
    "font": ("str", None, None, "CSS の font-family。例 \"'BIZ UDPGothic', sans-serif\"。名前は list_fonts で確認"),
    "weight": ("int", 100, 900, "文字の太さ（400=標準, 700=太字, 800〜900=極太）"),
    "size": ("int", 12, 160, "文字サイズ px（ブラウザソースの実寸。1080p で 40 前後が標準）"),
    "color": ("color", None, None, "文字色 #rrggbb"),
    "outlineColor": ("color", None, None, "縁取りの色"),
    "outlineWidth": ("num", 0, 8, "縁取りの太さ px（0=なし）"),
    "glow": ("color?", None, None, "光彩の色（\"\"=なし）"),
    "glowSize": ("num", 0, 40, "光彩の広がり px"),
    "shadow": ("bool", None, None, "下向きの影を付ける"),
    "hotColor": ("color", None, None, "ハイライト単語の色"),
    "animIn": ("enum", None, LINE_ANIMS, "行が出るときの動き"),
    "partialOpacity": ("num", 0, 1, "認識途中（薄文字）の濃さ"),
    "partialFx": ("bool", None, None, "認識途中にも単語エフェクトを付ける"),
    "letterSpacing": ("num", -0.1, 0.5, "字間 em"),
    "displayMode": ("enum", None, ["both", "en"], "翻訳ON時: both=原文＋訳を併記 / en=訳だけ表示"),
    "enScale": ("num", 0.3, 1, "訳文の大きさ（本文比）"),
    "enOpacity": ("num", 0, 1, "訳文の濃さ"),
    "enColor": ("color?", None, None, "訳文の色（\"\"=本文と同じ）"),
    "enFont": ("str", None, None, "訳文のフォント（\"\"=本文と同じ）"),
    "enDeco": ("bool", None, None, "訳文に専用の縁取り・光彩を使う"),
    "enOutlineWidth": ("num", 0, 8, "訳文の縁取りの太さ（enDeco=true のとき）"),
    "enOutlineColor": ("color", None, None, "訳文の縁取りの色（enDeco=true のとき）"),
    "enGlowSize": ("num", 0, 40, "訳文の光彩の広がり（enDeco=true のとき）"),
    "enGlowColor": ("color", None, None, "訳文の光彩の色（enDeco=true のとき）"),
    "name": ("str", None, None, "表示名"),
    "desc": ("str", None, None, "説明文"),
}

LAYOUT_FIELDS = {
    "x": ("num", 0, 100, "枠の左端。ブラウザソースの幅に対する %"),
    "y": ("num", 0, 100, "枠の上端。ブラウザソースの高さに対する %"),
    "w": ("num", 1, 100, "枠の幅 %"),
    "h": ("num", 1, 100, "枠の高さ %"),
    "mode": ("enum", None, ["flow", "vertical", "lyric", "bubble"],
             "表示のしかた: flow=下から積み上げ / vertical=縦書き / lyric=リリック風に散らす / bubble=吹き出し"),
    "align": ("enum", None, ["left", "center", "right"], "文字揃え"),
    "maxLines": ("int", 1, 30, "同時に出す最大行数"),
    "padding": ("num", 0, 200, "枠の内側の余白 px"),
    "bg": ("color", None, None, "枠の背景色"),
    "bgOpacity": ("num", 0, 1, "背景の濃さ（0=透明）"),
    "radius": ("num", 0, 200, "角の丸み px"),
    "borderColor": ("color", None, None, "枠線の色"),
    "borderWidth": ("num", 0, 20, "枠線の太さ px（0=なし）"),
    "smooth": ("bool", None, None, "行が増えるときに滑らかにスクロール"),
    "smoothMs": ("int", 0, 2000, "スクロールの時間 ms"),
    "autoClear": ("bool", None, None, "しばらく話さないと字幕を消す"),
    "clearSec": ("num", 1, 120, "自動で消すまでの秒数"),
    "name": ("str", None, None, "表示名"),
    "desc": ("str", None, None, "説明文"),
}
# リリック・吹き出しなどモード専用の細かい項目。意味は get_layout の現在値を見て判断する。
# 型は現在値（無ければ同梱の値）と同じものだけ受け付ける
LAYOUT_ADVANCED = {
    "lifeSec", "maxChunks", "vertRate", "rotate", "sizeJitter", "stagger",
    "lyricPartial", "lyricSplit", "lyricScale", "lyricMood", "lyricMaxScenes",
    "bubbleDir", "bubbleFill", "bubbleFillOpacity", "bubbleInk", "bubbleInkOn",
    "bubbleLife", "bubbleMinScale", "bubblePartial", "bubbleScale", "bubbleShape",
    "bubbleStroke", "bubbleStrokeWidth", "bubbleAlign", "bubbleArrange",
    "bubbleHistory", "bubbleSpotW", "bubbleSpotH", "tailOn", "tailLen", "tailX",
    "tailY", "guestTailX", "guestTailY",
}


def _check(fields, key, value, current=None):
    """1項目を検証して正規化した値を返す。不正なら ValueError"""
    if key not in fields:
        if fields is LAYOUT_FIELDS and key in LAYOUT_ADVANCED:
            if current is not None and type(value) is not type(current) \
                    and not (isinstance(current, (int, float)) and isinstance(value, (int, float))
                             and not isinstance(value, bool)):
                raise ValueError(f"{key}: 現在値 {current!r} と同じ型で指定してください")
            return value
        raise ValueError(f"未知の項目: {key}（使える項目は get_design / get_layout の fields を参照）")
    kind, lo, hi, _ = fields[key]
    if kind in ("num", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key}: 数値で指定してください")
        if not lo <= value <= hi:
            raise ValueError(f"{key}: {lo}〜{hi} の範囲で指定してください（{value}）")
        return int(round(value)) if kind == "int" else value
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key}: true / false で指定してください")
        return value
    if kind in ("color", "color?"):
        if kind == "color?" and value == "":
            return ""
        if not isinstance(value, str) or not HEX.match(value):
            raise ValueError(f"{key}: #rrggbb 形式で指定してください（{value!r}）")
        return value.lower()
    if kind == "enum":
        if value not in hi:
            raise ValueError(f"{key}: {hi} のどれかで指定してください")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{key}: 文字列で指定してください")
    return value[:300]


def _field_guide(fields):
    out = {}
    for k, (kind, lo, hi, desc) in fields.items():
        if kind in ("num", "int"):
            out[k] = f"{desc}（{lo}〜{hi}）"
        elif kind == "enum":
            out[k] = f"{desc}（{' / '.join(hi)}）"
        else:
            out[k] = desc
    return out


# ---------------- Mojicast への HTTP ----------------

def _api(path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error") or str(e)
        except ValueError:
            msg = str(e)
        if e.code == 404 and path == "/api/sample-caption":
            msg = "この Mojicast は試し表示に未対応です（新しい版で使えます）"
        raise RuntimeError(f"Mojicast が拒否しました: {msg}") from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError(
            f"Mojicast に接続できません（{BASE}）。Mojicast を起動しているか、"
            "ポートを変えているなら環境変数 MOJICAST_PORT を確認してください") from None


def _load():
    cfg = _api("/api/config")
    return cfg,_api("/api/presets")["presets"], _api("/api/boxes")["boxes"]


def _find(items, wanted, label):
    for i, x in enumerate(items):
        if x.get("id") == wanted:
            return i
    names = ", ".join(f"{x.get('id')}({x.get('name')})" for x in items)
    raise ValueError(f"{label}「{wanted}」が見つかりません。候補: {names}")


# 直前の状態（undo 用）。サーバの起動中だけ保持する
_history = []
HISTORY_MAX = 30


def _remember(label, cfg, presets, boxes):
    _history.append({"label": label, "presets": json.loads(json.dumps(presets)),
                     "boxes": json.loads(json.dumps(boxes)),
                     "preset": cfg.get("preset"), "box": cfg.get("box")})
    del _history[:-HISTORY_MAX]


def _summary(item):
    return {"id": item.get("id"), "name": item.get("name"), "desc": item.get("desc", "")}


# ---------------- ツール ----------------

@mcp.tool()
def mojicast_status() -> dict:
    """Mojicast の現在の状態。最初に呼ぶ。

    使用中のデザイン（preset）とレイアウト（box）、選べる一覧、OBS 用の URL を返す。
    OBS 側で字幕のブラウザソースを探すときは、URL が overlay_url で始まるソースを探す。
    レイアウトの x/y/w/h はそのブラウザソースの大きさに対する % なので、字幕レイヤー
    全体を動かしたいときは OBS MCP でソースの位置を変え、レイヤーの中で枠を動かしたい
    ときは update_layout を使う。
    """
    cfg, presets, boxes = _load()
    status = _api("/api/status")
    return {
        "overlay_url": f"http://127.0.0.1:{PORT}/",
        "engine": status.get("state"),
        "active_design": cfg.get("preset"),
        "active_layout": cfg.get("box"),
        "collab": bool(cfg.get("collab")),
        "translate": bool(cfg.get("translate")),
        "designs": [_summary(p) for p in presets],
        "layouts": [_summary(b) for b in boxes],
        "note": "変更は配信画面に即反映される。Mojicast の『字幕デザイン』編集窓を開いたまま"
                "保存すると、その窓の内容で上書きされるので、調整中は閉じておくこと。",
    }


@mcp.tool()
def get_design(design_id: str = "") -> dict:
    """デザイン（フォント・文字色・縁取り・光彩・登場アニメ・訳文の見た目）を読む。

    design_id を省くと使用中のデザイン。fields に各項目の意味と範囲が入っている。
    """
    cfg, presets, _ = _load()
    i = _find(presets, design_id or cfg.get("preset"), "デザイン")
    return {"design": presets[i], "active": presets[i]["id"] == cfg.get("preset"),
            "fields": _field_guide(DESIGN_FIELDS)}


@mcp.tool()
def update_design(changes: dict, design_id: str = "") -> dict:
    """デザインの項目を変える（変えたい項目だけ渡す）。使用中なら配信画面へ即反映。

    例: {"font": "'BIZ UDPGothic', sans-serif", "size": 48, "outlineWidth": 3}
    「もう少し大きく」なら現在値から 10〜20% 程度、のように get_design の値を基準に決める。
    フォント名は list_fonts で実在を確かめてから使う（無いフォントは別の字体で出る）。
    """
    cfg, presets, boxes = _load()
    i = _find(presets, design_id or cfg.get("preset"), "デザイン")
    before = {k: presets[i].get(k) for k in changes}
    clean = {k: _check(DESIGN_FIELDS, k, v) for k, v in changes.items()}
    _remember(f"デザイン「{presets[i].get('name')}」の変更", cfg, presets, boxes)
    presets[i].update(clean)
    _api("/api/presets", {"presets": presets})
    return {"ok": True, "design": presets[i]["id"], "before": before, "after": clean,
            "live": presets[i]["id"] == cfg.get("preset")}


@mcp.tool()
def get_layout(layout_id: str = "") -> dict:
    """レイアウト（字幕枠の位置・大きさ・背景・行数・表示のしかた）を読む。

    layout_id を省くと使用中のレイアウト。x/y/w/h は OBS のブラウザソースに対する %。
    fields に主な項目の意味と範囲、advanced にモード専用の項目名が入っている。
    """
    cfg, _, boxes = _load()
    i = _find(boxes, layout_id or cfg.get("box"), "レイアウト")
    return {"layout": boxes[i], "active": boxes[i]["id"] == cfg.get("box"),
            "fields": _field_guide(LAYOUT_FIELDS), "advanced": sorted(LAYOUT_ADVANCED)}


@mcp.tool()
def update_layout(changes: dict, layout_id: str = "") -> dict:
    """レイアウトの項目を変える（変えたい項目だけ渡す）。使用中なら配信画面へ即反映。

    例: 画面下に横長の帯 → {"x": 5, "y": 72, "w": 90, "h": 24, "bgOpacity": 0.5}
    枠が画面からはみ出さないよう x+w ≤ 100、y+h ≤ 100 に収める。
    位置を決めるときは OBS MCP のスクリーンショットでアバターやゲーム画面の
    重要な部分を確かめ、そこを避ける。
    """
    cfg, presets, boxes = _load()
    i = _find(boxes, layout_id or cfg.get("box"), "レイアウト")
    cur = boxes[i]
    clean = {k: _check(LAYOUT_FIELDS, k, v, cur.get(k)) for k, v in changes.items()}
    merged = {**cur, **clean}
    for a, b in (("x", "w"), ("y", "h")):
        if merged.get(a, 0) + merged.get(b, 100) > 100.001:
            raise ValueError(f"{a}+{b} が 100 を超えて画面からはみ出します"
                             f"（{a}={merged.get(a)}, {b}={merged.get(b)}）")
    before = {k: cur.get(k) for k in changes}
    _remember(f"レイアウト「{cur.get('name')}」の変更", cfg, presets, boxes)
    cur.update(clean)
    _api("/api/boxes", {"boxes": boxes})
    return {"ok": True, "layout": cur["id"], "before": before, "after": clean,
            "live": cur["id"] == cfg.get("box")}


@mcp.tool()
def use_design(design_id: str) -> dict:
    """使用するデザインを切り替える（配信画面へ即反映）"""
    cfg, presets, boxes = _load()
    _find(presets, design_id, "デザイン")
    _remember("デザインの切り替え", cfg, presets, boxes)
    _api("/api/config", {"preset": design_id})
    return {"ok": True, "active_design": design_id}


@mcp.tool()
def use_layout(layout_id: str) -> dict:
    """使用するレイアウトを切り替える（配信画面へ即反映）"""
    cfg, presets, boxes = _load()
    _find(boxes, layout_id, "レイアウト")
    _remember("レイアウトの切り替え", cfg, presets, boxes)
    _api("/api/config", {"box": layout_id})
    return {"ok": True, "active_layout": layout_id}


@mcp.tool()
def duplicate(kind: str, name: str, source_id: str = "", use: bool = True) -> dict:
    """デザインかレイアウトを複製して新しく作る。同梱の見本を壊さずに試したいとき用。

    kind: "design" か "layout"。source_id を省くと使用中のものを複製する。
    use=true なら作ったものに切り替える。
    """
    if kind not in ("design", "layout"):
        raise ValueError('kind は "design" か "layout"')
    cfg, presets, boxes = _load()
    items, key, plural = (presets, "preset", "presets") if kind == "design" \
        else (boxes, "box", "boxes")
    src = items[_find(items, source_id or cfg.get(key), "複製元")]
    base = re.sub(r"[^a-z0-9-]", "", (src["id"] or "item").lower()) or "item"
    ids = {x.get("id") for x in items}
    n = 2
    while f"{base}-ai{n}" in ids:
        n += 1
    new = {**json.loads(json.dumps(src)), "id": f"{base}-ai{n}", "name": name[:40]}
    _remember(f"{kind} の複製", cfg, presets, boxes)
    items.append(new)
    _api(f"/api/{plural}", {plural: items})
    if use:
        _api("/api/config", {key: new["id"]})
    return {"ok": True, "id": new["id"], "active": use}


@mcp.tool()
def list_fonts(query: str = "") -> dict:
    """この PC にインストールされているフォント名を探す（query で部分一致・大文字小文字無視）。

    font には \"'名前', sans-serif\" の形で指定する。日本語フォントは日本語名と英語名の
    両方で出ることがある。ユーザー単位でインストールしたフォントは OBS のブラウザソースで
    使えないことがある（そのときは Windows の「すべてのユーザーにインストール」が必要）。
    """
    fonts = _api("/api/fonts").get("fonts", [])
    if query:
        q = query.lower()
        fonts = [f for f in fonts if q in f.lower()]
    return {"count": len(fonts), "fonts": fonts[:200], "truncated": len(fonts) > 200}


@mcp.tool()
def show_sample_caption(text: str = "", translation: str = "") -> dict:
    """話さずに字幕を1行出して見た目を確かめる（ログや翻訳には流れない）。

    調整のあとにこれを呼び、OBS MCP のスクリーンショットで仕上がりを見る。
    行数・折り返しを確かめたいときは、長めの文を何回か送る。translation を渡すと
    訳文の併記も出る。
    """
    text = text or "みなさんこんばんは！今日もゆったり配信していきます。"
    return _api("/api/sample-caption", {"text": text, "translation": translation})


@mcp.tool()
def clear_captions() -> dict:
    """表示中の字幕をすべて消す（試し表示の後片付け）"""
    return _api("/api/clear", {})


@mcp.tool()
def undo() -> dict:
    """直前の変更（update_* / use_* / duplicate）を1つ取り消す。このサーバの起動中だけ有効"""
    if not _history:
        return {"ok": False, "message": "取り消せる変更がありません"}
    snap = _history.pop()
    _api("/api/presets", {"presets": snap["presets"]})
    _api("/api/boxes", {"boxes": snap["boxes"]})
    _api("/api/config", {"preset": snap["preset"], "box": snap["box"]})
    return {"ok": True, "undone": snap["label"], "remaining": len(_history)}


if __name__ == "__main__":
    mcp.run()
