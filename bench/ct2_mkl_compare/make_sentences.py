# -*- coding: utf-8 -*-
"""配信ログ（logs/*/*_transcript.txt）から比較用の文を等間隔に抜き出して sentences.txt を作る。

出力は配信の書き起こしなので git には入れない（.gitignore 済み）。配布 Zip には入れてよい（えもさん本人の配信）。
使い方: python make_sentences.py <リポジトリ直下> <出力先> [文の数=200]
"""
import glob
import os
import re
import sys

_STRIP = re.compile(r"^\s*(\[[^\]]*\]|\d{1,2}:\d{2}(:\d{2})?)\s*")

root, out = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 200
seen, lines = set(), []
for p in sorted(glob.glob(os.path.join(root, "logs", "*", "*_transcript.txt"))):
    with open(p, encoding="utf-8", errors="replace") as f:
        for raw in f:
            s = _STRIP.sub("", raw).strip()
            if len(s) >= 4 and s not in seen:
                seen.add(s)
                lines.append(s)
if not lines:
    sys.exit("logs/*/*_transcript.txt が見つかりません")
step = max(1, len(lines) // n)
picked = lines[::step][:n]
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(picked) + "\n")
print(f"{len(picked)} 文 / 候補 {len(lines)} 文 → {out}")
