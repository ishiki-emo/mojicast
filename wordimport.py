"""
単語の一括取り込み（CSV / JSON）

ゲームの技名のように数百〜数千語をまとめて登録したい要望に応える。
単語スタジオの「📥 ファイルから取り込む」から使う。

受け付ける形式（どちらも同じ列構成で、認識辞書と英訳辞書を同時に埋められる）:

    CSV:  表記,読み,出やすさ,英訳      ← 読み以降は省略可。#行はコメント
          昇龍拳,しょうりゅうけん,,Shoryuken
          （1行目が「表記,読み,…」のような見出しなら読み飛ばす。列名で並び替えも可）
    JSON: {"words": [{"surface": "昇龍拳", "reading": "しょうりゅうけん",
                       "score": "", "en": "Shoryuken"}]}
          （配列そのもの、または {"hotwords": [...], "glossary": [...]} も可）

文字コードは UTF-8（BOM可）と Shift_JIS（Excel の CSV 保存）の両方を受ける。

取り込みは2段階: parse → plan（集計をユーザーに見せる）→ apply（確定）。
LLM に生成させたリストは実在しない語や空欄を平気で混ぜるので、件数・重複・
空欄・異常に長い語を弾いたうえで結果を見せてから入れる（ROADMAP #1）。
"""
import csv
import io
import json
import re

MAX_ENTRIES = 10000      # 暴走ファイル対策（実用上限は数千語）
MAX_LEN = 60             # 表記・読み・英訳の各欄

# 見出し行の列名 → 内部キー（大小文字・全半角空白は無視して比較）
_HEADER_ALIASES = {
    "surface": "surface", "表記": "surface", "単語": "surface", "word": "surface",
    "ja": "surface", "日本語": "surface", "技名": "surface", "name": "surface",
    "reading": "reading", "読み": "reading", "よみ": "reading", "kana": "reading",
    "ひらがな": "reading", "読み方": "reading",
    "score": "score", "出やすさ": "score", "スコア": "score", "strength": "score",
    "en": "en", "英訳": "en", "english": "en", "英語": "en", "英名": "en",
    "type": "type", "種別": "type", "タイプ": "type", "kind": "type",
}
_DEFAULT_COLUMNS = ("surface", "reading", "score", "en", "type")
GLOSS_ONLY = "英訳のみ"   # 種別列: 英訳辞書にだけ登録されている語（書き出し→再取り込みの汚染防止）


def decode_bytes(data: bytes) -> str:
    """UTF-8（BOM可）→ Shift_JIS(cp932) の順に試す。どちらでもなければ UTF-8 で
    読めない文字だけ落とす（1文字の化けで全体を拒否しない）。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean(v) -> str:
    """欄の無害化: 文字列化・制御文字除去・前後空白除去"""
    if v is None:
        return ""
    if not isinstance(v, str):
        v = str(v)
    return "".join(c for c in v if ord(c) >= 32).strip()


def _norm_header(cell: str) -> str:
    return re.sub(r"[\s　]", "", cell).lower().lstrip("\ufeff")


def _detect_header(row):
    """先頭行が見出しなら列→キーの対応を返す。見出しでなければ None。
    「先頭セルが既知の列名」を見出しの条件にする（技名が偶然一致することはない）。"""
    if not row:
        return None
    if _HEADER_ALIASES.get(_norm_header(row[0])) != "surface":
        return None
    return [_HEADER_ALIASES.get(_norm_header(cell)) for cell in row]


def _parse_csv(text: str):
    rows = []
    reader = csv.reader(io.StringIO(text))
    columns = None
    for lineno, row in enumerate(reader, 1):
        if not row or not any(c.strip() for c in row):
            continue
        if row[0].lstrip().startswith("#"):
            continue
        if columns is None:
            header = _detect_header(row)
            if header is not None:
                columns = header
                continue
            columns = list(_DEFAULT_COLUMNS)
        item = {"_line": lineno}
        for key, cell in zip(columns, row):
            if key:
                item[key] = cell
        rows.append(item)
    return rows


def _parse_json(text: str):
    data = json.loads(text)
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if isinstance(data.get("words"), list):
            items = data["words"]
        else:
            # mojipack 風: hotwords と glossary を表記で突き合わせる
            hot = data.get("hotwords") if isinstance(data.get("hotwords"), list) else []
            gl = data.get("glossary") if isinstance(data.get("glossary"), list) else []
            by_surface = {}
            for h in hot:
                if isinstance(h, dict):
                    s = _clean(h.get("surface"))
                    if s:
                        by_surface.setdefault(s, dict(h))
            for g in gl:
                if isinstance(g, dict):
                    s = _clean(g.get("surface") or g.get("ja"))
                    if s:
                        by_surface.setdefault(s, {"surface": s})["en"] = g.get("en")
            items = list(by_surface.values())
    rows = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        rows.append({"_line": i,
                     "surface": it.get("surface", it.get("ja", it.get("word"))),
                     "reading": it.get("reading"),
                     "score": it.get("score"),
                     "en": it.get("en")})
    return rows


def parse_words(text: str):
    """ファイル本文 → (整形済みエントリ, 弾いた行)

    エントリ: {surface, reading, score, en, type}（score は数値として妥当なもの以外は空）
              type は「英訳のみ」などのマーカー（v0.9.9〜の再取り込み時の汚染防止用）
    弾いた行: {line, reason, text}  reason は empty / too_long / comma / json / too_many
    """
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped[:1] in ("{", "["):
        try:
            raw = _parse_json(text)
        except (ValueError, TypeError):
            return [], [{"line": 0, "reason": "json", "text": ""}]
    else:
        raw = _parse_csv(text)

    entries, skipped = [], []
    for it in raw[:MAX_ENTRIES]:
        surface = _clean(it.get("surface"))
        reading = _clean(it.get("reading"))
        score = _clean(it.get("score"))
        en = _clean(it.get("en"))
        entry_type = _clean(it.get("type", ""))
        line = it.get("_line", 0)
        if not surface:
            skipped.append({"line": line, "reason": "empty", "text": reading or en})
            continue
        if len(surface) > MAX_LEN or len(reading) > MAX_LEN or len(en) > MAX_LEN:
            skipped.append({"line": line, "reason": "too_long", "text": surface})
            continue
        if "," in surface or "," in reading:
            # hotwords.txt は「,」区切りなので欄の中に「,」は置けない
            skipped.append({"line": line, "reason": "comma", "text": surface})
            continue
        if score:
            try:
                f = float(score)
                score = f"{f:g}" if 0 < f <= 20 else ""
            except ValueError:
                score = ""
        entries.append({"surface": surface, "reading": reading,
                        "score": score, "en": en, "type": entry_type})
    if len(raw) > MAX_ENTRIES:
        skipped.append({"line": MAX_ENTRIES + 1, "reason": "too_many",
                        "text": str(len(raw) - MAX_ENTRIES)})
    return entries, skipped


def parse_glossary(text: str):
    """英訳辞書専用ファイル本文 → (整形済みエントリ, 弾いた行)

    エントリ: {ja, en}
    弾いた行: {line, reason, text}  reason は empty / empty_en / too_long / comma / json / too_many
    """
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped[:1] in ("{", "["):
        try:
            data = json.loads(text)
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict) and isinstance(data.get("glossary"), list):
                items = data["glossary"]
            raw = []
            for i, it in enumerate(items, 1):
                if not isinstance(it, dict):
                    continue
                raw.append({"_line": i,
                           "ja": it.get("ja", it.get("surface", it.get("word"))),
                           "en": it.get("en")})
        except (ValueError, TypeError):
            return [], [{"line": 0, "reason": "json", "text": ""}]
    else:
        reader = csv.reader(io.StringIO(text))
        raw = []
        columns = None
        for lineno, row in enumerate(reader, 1):
            if not row or not any(c.strip() for c in row):
                continue
            if row[0].lstrip().startswith("#"):
                continue
            if columns is None:
                # 見出し行の検出（日本語/ja/表記 などをja列、英訳/en/english などをen列として認識）
                norm_first = _norm_header(row[0])
                if norm_first in ("日本語", "ja", "表記", "japanese", "word", "surface"):
                    columns = []
                    for cell in row:
                        nc = _norm_header(cell)
                        if nc in ("日本語", "ja", "表記", "japanese", "word", "surface"):
                            columns.append("ja")
                        elif nc in ("英訳", "en", "english", "英語"):
                            columns.append("en")
                        else:
                            columns.append(None)
                    continue
                else:
                    columns = ["ja", "en"]  # 見出し無しの場合は順序固定
            item = {"_line": lineno}
            for key, cell in zip(columns, row):
                if key:
                    item[key] = cell
            raw.append(item)

    entries, skipped = [], []
    for it in raw[:MAX_ENTRIES]:
        ja = _clean(it.get("ja"))
        en = _clean(it.get("en"))
        line = it.get("_line", 0)
        if not ja or not en:
            # 日本語側が空なら empty、英訳だけ空なら empty_en（UI で「英訳が空」と出す）
            skipped.append({"line": line, "reason": "empty" if not ja else "empty_en",
                            "text": ja or en})
            continue
        if len(ja) > MAX_LEN or len(en) > MAX_LEN:
            skipped.append({"line": line, "reason": "too_long", "text": ja})
            continue
        if "," in ja:
            skipped.append({"line": line, "reason": "comma", "text": ja})
            continue
        entries.append({"ja": ja, "en": en})
    if len(raw) > MAX_ENTRIES:
        skipped.append({"line": MAX_ENTRIES + 1, "reason": "too_many",
                        "text": str(len(raw) - MAX_ENTRIES)})
    return entries, skipped


def plan_import(entries, existing_hot, existing_gloss):
    """取り込み前の集計。ユーザーに見せて「追加のみ／上書き」を選ばせる。
    (集計dict, ファイル内重複を除いたエントリ) を返す。

    existing_hot:   wordstore.load_hotwords() の形 [{surface, reading, score}]
    existing_gloss: wordstore.load_glossary() の形 [{ja, en}]
    """
    seen = set()
    dup_in_file = 0
    uniq = []
    for e in entries:
        if e["surface"] in seen:
            dup_in_file += 1
            continue
        seen.add(e["surface"])
        uniq.append(e)
    hot_names = {h.get("surface") for h in existing_hot}
    gl_names = {g.get("ja") for g in existing_gloss}
    # 種別「英訳のみ」は認識辞書に入らないので、新規・既存・読みなしの集計から外す
    to_hot = [e for e in uniq if e.get("type") != GLOSS_ONLY]
    exists = sum(1 for e in to_hot if e["surface"] in hot_names)
    with_en = sum(1 for e in uniq if e["en"])
    en_exists = sum(1 for e in uniq if e["en"] and e["surface"] in gl_names)
    no_reading = sum(1 for e in to_hot
                     if not e["reading"] and re.search(r"[\u4e00-\u9fff]", e["surface"]))
    return {
        "total": len(uniq),
        "dup_in_file": dup_in_file,
        "exists": exists,             # 認識辞書に同じ表記がある件数
        "new": len(to_hot) - exists,
        "gloss_only": len(uniq) - len(to_hot),  # 英訳辞書にだけ入る件数
        "with_en": with_en,           # 英訳欄がある件数
        "en_exists": en_exists,       # 英訳辞書に同じ表記がある件数
        "no_reading": no_reading,     # 漢字を含むのに読みが無い（認識誘導が効かない）
    }, uniq


def apply_import(entries, existing_hot, existing_gloss, mode="add",
                 with_glossary=True):
    """entries を既存リストへ反映した新しい (hotwords, glossary, 件数) を返す。

    mode: "add"       … 同じ表記が既にあれば触らない（既存優先）
          "overwrite" … 同じ表記があればその行を置き換える（位置は維持）
    with_glossary: 英訳欄を英訳辞書にも反映するか
    
    v0.9.9〜: 種別「英訳のみ」のエントリは認識辞書に追加せず、英訳辞書のみに入れる
    （再取り込み時の汚染防止）。種別列が無い古い形式は従来通り両方に入れる。
    """
    hot = [dict(h) for h in existing_hot]
    gl = [dict(g) for g in existing_gloss]
    hot_idx = {h.get("surface"): i for i, h in enumerate(hot)}
    gl_idx = {g.get("ja"): i for i, g in enumerate(gl)}
    n_hot = n_gl = 0
    for e in entries:
        s = e["surface"]
        entry_type = e.get("type", "")
        is_gloss_only = entry_type == GLOSS_ONLY
        
        # 認識辞書への追加（英訳のみエントリは除外）
        if not is_gloss_only:
            row = {"surface": s, "reading": e["reading"] or s, "score": e["score"]}
            if s in hot_idx:
                if mode == "overwrite":
                    hot[hot_idx[s]] = row
                    n_hot += 1
            else:
                hot_idx[s] = len(hot)
                hot.append(row)
                n_hot += 1
        
        # 英訳辞書への追加
        if with_glossary and e["en"]:
            grow = {"ja": s, "en": e["en"]}
            if s in gl_idx:
                if mode == "overwrite":
                    gl[gl_idx[s]] = grow
                    n_gl += 1
            else:
                gl_idx[s] = len(gl)
                gl.append(grow)
                n_gl += 1
    return hot, gl, {"hotwords": n_hot, "glossary": n_gl}


def to_csv(hot, gloss) -> str:
    """認識辞書＋英訳辞書 → 取り込みと同じ列構成の CSV 本文（Excel 用に BOM 付き）
    
    5列目「種別」で英訳のみのエントリを区別し、再取り込み時に認識辞書を汚染しないようにする。
    v0.9.8以前との互換性: 種別列が無い/空のファイルは従来通り全行を両方の辞書に入れる。
    """
    en_by = {g.get("ja"): g.get("en", "") for g in gloss}
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["表記", "読み", "出やすさ", "英訳", "種別"])
    seen = set()
    for h in hot:
        s = h.get("surface", "")
        if not s:
            continue
        seen.add(s)
        r = h.get("reading", "")
        w.writerow([s, "" if r == s else r, h.get("score", ""), en_by.get(s, ""), ""])
    for g in gloss:               # 英訳だけ登録されている語も落とさない
        if g.get("ja") and g["ja"] not in seen:
            w.writerow([g["ja"], "", "", g.get("en", ""), GLOSS_ONLY])
    return buf.getvalue()


def to_csv_glossary_only(gloss) -> str:
    """英訳辞書のみ → CSV 本文（Excel 用に BOM 付き）"""
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["日本語", "英訳"])
    for g in gloss:
        ja = g.get("ja", "")
        en = g.get("en", "")
        if ja and en:
            w.writerow([ja, en])
    return buf.getvalue()


def plan_import_glossary(entries, existing_gloss):
    """英訳辞書の取り込み前の集計。ユーザーに見せて「追加のみ／上書き」を選ばせる。
    (集計dict, ファイル内重複を除いたエントリ) を返す。

    entries:        parse_glossary() の結果 [{ja, en}]
    existing_gloss: wordstore.load_glossary() の形 [{ja, en}]
    """
    seen = set()
    dup_in_file = 0
    uniq = []
    for e in entries:
        if e["ja"] in seen:
            dup_in_file += 1
            continue
        seen.add(e["ja"])
        uniq.append(e)
    gl_names = {g.get("ja") for g in existing_gloss}
    exists = sum(1 for e in uniq if e["ja"] in gl_names)
    return {
        "total": len(uniq),
        "dup_in_file": dup_in_file,
        "exists": exists,
        "new": len(uniq) - exists,
    }, uniq


def apply_import_glossary(entries, existing_gloss, mode="add"):
    """英訳辞書のエントリを既存リストへ反映した新しい (glossary, 件数) を返す。

    mode: "add"       … 同じ表記が既にあれば触らない（既存優先）
          "overwrite" … 同じ表記があればその行を置き換える（位置は維持）
    """
    gl = [dict(g) for g in existing_gloss]
    gl_idx = {g.get("ja"): i for i, g in enumerate(gl)}
    n_gl = 0
    for e in entries:
        ja = e["ja"]
        grow = {"ja": ja, "en": e["en"]}
        if ja in gl_idx:
            if mode == "overwrite":
                gl[gl_idx[ja]] = grow
                n_gl += 1
        else:
            gl_idx[ja] = len(gl)
            gl.append(grow)
            n_gl += 1
    return gl, {"glossary": n_gl}
