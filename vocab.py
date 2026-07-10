"""
単語登録（読み付き）

語彙ファイルの各行は  表記,読み,スコア  （読み・スコアは省略可）:
    癒色えも,いろえも
    えもてぃっく              ← 読み省略時は表記をそのまま読みとして使う
    リーゾンスピーチ,りーぞんすぴーち,3.0

この語彙から
  1) sherpa-onnx 用ホットワードファイル（"読み" で認識を誘導）を生成
  2) 認識後に  読み→表記  へ置換する関数
を作る。

なぜ読みが要るか:
  k2 の音響モデルは「音」に対応するトークンしか出さない。漢字表記(癒色えも)を
  そのまま登録しても、その音に漢字トークンの音響スコアが無いためブーストしても
  選ばれない。そこで「読み(いろえも)」でホットボーストして認識を安定させ、
  出てきた読みを表記(癒色えも)へ後段で置換する。
"""
import os
import tempfile


def parse_vocab(path: str):
    """語彙ファイルを (表記, 読み, スコア) のリストに読み込む"""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            surface = parts[0]
            reading = parts[1] if len(parts) > 1 and parts[1] else surface
            score = parts[2] if len(parts) > 2 and parts[2] else None
            entries.append((surface, reading, score))
    return entries


def write_hotwords(entries, out_path: str | None = None) -> str:
    """entries から sherpa-onnx 用ホットワードファイル（読みベース）を書き出す"""
    if out_path is None:
        fd, out_path = tempfile.mkstemp(prefix="hotwords_", suffix=".txt")
        os.close(fd)
    with open(out_path, "w", encoding="utf-8") as f:
        for _surface, reading, score in entries:
            line = reading
            if score:
                line += f" :{score}"
            f.write(line + "\n")
    return out_path


def build_replacer(entries):
    """読み→表記 の置換関数を返す（読みが長いものから先に置換）"""
    pairs = [(reading, surface) for surface, reading, _ in entries
             if reading != surface]
    pairs.sort(key=lambda x: len(x[0]), reverse=True)

    def replace(text: str) -> str:
        for reading, surface in pairs:
            if reading in text:
                text = text.replace(reading, surface)
        return text

    return replace
