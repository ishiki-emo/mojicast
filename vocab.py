"""
単語登録（読み付き）

語彙ファイルの各行は  表記,読み,スコア  （読み・スコアは省略可）:
    癒色えも,いろえも
    えもてぃっく              ← 読み省略時は表記をそのまま読みとして使う
    リーゾンスピーチ,りーぞんすぴーち,3.0
    癒色えも,意識へモ／意識エモ   ← 読み（誤変換形）は「／」区切りで複数OK

この語彙から
  1) sherpa-onnx 用ホットワードファイル（"読み" で認識を誘導）を生成
  2) 認識後に  読み→表記  へ置換する関数
を作る。

なぜ読みが要るか:
  k2 の音響モデルは「音」に対応するトークンしか出さない。漢字表記(癒色えも)を
  そのまま登録しても、その音に漢字トークンの音響スコアが無いためブーストしても
  選ばれない。そこで「読み(いろえも)」でホットボーストして認識を安定させ、
  出てきた読みを表記(癒色えも)へ後段で置換する。

なぜ読みが複数要るか:
  誤変換の形は認識モデルごとに違う（k2=「意識へモ」/ SenseVoice=「意識エモ」等）。
  認識誘導が使えない多言語モデルでは置換が唯一の対策なので、モデルごとの
  誤変換形を並記できるようにする。
"""
import os
import re
import tempfile


def _split_readings(reading: str):
    """読み欄を「／」または「/」で複数の形に分割する（空要素は除く）"""
    return [r.strip() for r in re.split(r"[／/]", reading or "") if r.strip()]


def _is_score(s) -> bool:
    """強さ（出やすさ）として使える数値か。sherpa-onnx は数値でないと
    「invalid stof argument」でモデルのロードごと失敗するため、書き出す前に必ず通す"""
    try:
        v = float(str(s).strip())
    except (TypeError, ValueError):
        return False
    return v == v and v not in (float("inf"), float("-inf"))


def parse_vocab(path: str):
    """語彙ファイルを (表記, 読み, スコア) のリストに読み込む

    読み欄に「,」を入れて保存された行（例: 癒色えも,意識エモ,意識えも）は列がずれて
    3列目に読みが入る。数値でない3列目以降は読みの続きとして「／」でつなぎ、
    最後の列が数値ならそれを強さとして使う（壊れた既存ファイルもそのまま読める）。"""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            surface = parts[0]
            rest = parts[1:]
            score = None
            if len(rest) >= 2 and _is_score(rest[-1]):
                score = rest.pop()
            elif len(rest) >= 2 and not rest[-1]:
                rest.pop()                      # 「表記,読み,」の空の強さ
            readings = [r for r in rest if r]
            reading = "／".join(readings) if readings else surface
            entries.append((surface, reading, score))
    return entries


def write_hotwords(entries, out_path: str | None = None) -> str:
    """entries から sherpa-onnx 用ホットワードファイル（読みベース）を書き出す"""
    if out_path is None:
        fd, out_path = tempfile.mkstemp(prefix="hotwords_", suffix=".txt")
        os.close(fd)
    with open(out_path, "w", encoding="utf-8") as f:
        for _surface, reading, score in entries:
            for r in _split_readings(reading):
                line = r
                if score and _is_score(score):   # 数値でない強さはロード失敗の元なので書かない
                    line += f" :{float(score):g}"
                f.write(line + "\n")
    return out_path


def build_replacer(entries):
    """読み→表記 の置換関数を返す（読みが長いものから先に置換）

    - 読み欄の「／」区切り複数形にそれぞれ対応
    - SenseVoice等が単語の途中に句読点を挟むケース（「意識、エモ」）にも
      当たるよう、文字間に句読点・空白を許す寛容マッチも行う
    - 数千語（ゲームの技名一括登録など）でも認識1回あたり1ms未満で済むよう、
      パターンを先頭文字ごとに分けて本文を1文字ずつ走査する。全パターンを
      1本の正規表現に束ねる方式は2500語で1回100msかかり実用にならない（実測）
    """
    pairs = []
    for surface, reading, _ in entries:
        for r in _split_readings(reading):
            if r != surface:
                pairs.append((r, surface))
    if not pairs:
        return lambda text: text
    # 表記そのものも「自分自身への置換」として登録する（保護パターン）。
    # 本文に正しい表記が既に出ているとき、その一部に短い読みがマッチして
    # 壊れるのを防ぐ（例: 本文の「意識エモい系」を読み「意識エモ」が食う）。
    for surface, _reading, _ in entries:
        pairs.append((surface, surface))
    # 「1パスで同時置換」する。逐次replaceだと、置換結果（表記）の中に別エントリの
    # 読みが含まれるとき連鎖して壊れる（例: →意識エモい系 の中の「意識エモ」が
    # 再置換される）。長い読みを先に並べ、同じ開始位置では最長のパターンが勝つ。
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    sep = "[、。，．・\\s]*"
    buckets = {}
    for r, surface in pairs:
        if r:
            buckets.setdefault(r[0], []).append((r, surface))
    table = {}
    for first, lst in buckets.items():
        parts = []
        surfaces = []
        for i, (r, surface) in enumerate(lst):
            parts.append(f"(?P<g{i}>" + sep.join(map(re.escape, r)) + ")")
            surfaces.append(surface)
        table[first] = (re.compile("|".join(parts)), surfaces)

    def replace(text: str) -> str:
        out = []
        i = 0
        n = len(text)
        while i < n:
            hit = table.get(text[i])
            if hit is not None:
                m = hit[0].match(text, i)
                if m:
                    out.append(hit[1][int(m.lastgroup[1:])])
                    i = m.end()
                    continue
            out.append(text[i])
            i += 1
        return "".join(out)

    return replace
