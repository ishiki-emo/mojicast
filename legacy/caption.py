"""
リアルタイム字幕の表示コンポーザ
認識テキストを次々に追記し、約 width 文字＋文の区切りのいい所で改行して確定する。

- 未確定の行は \r で上書きしながらライブ表示（ばんばん文字が増える体験）
- width 文字を超えたら、近くの区切り（。！？を優先、無ければ、）で改行
- 区切りが見つからないまま overflow を超えたら強制改行
"""
import sys

STRONG = "。！？"   # 文末（強い区切り）
WEAK = "、"         # 読点（弱い区切り）


class LiveCaption:
    def __init__(self, width: int = 30, overflow: int = 15,
                 min_line: int | None = None, stream=None):
        """
        Args:
            width: 1行の目安文字数
            overflow: 区切りを探して許容する超過分（width+overflow で強制改行）
            min_line: これ未満の位置では改行しない（短すぎる行を防ぐ）
            stream: 出力先（既定 sys.stdout）
        """
        self.width = width
        self.overflow = overflow
        self.min_line = min_line if min_line is not None else max(1, width - 12)
        self.pending = ""       # 確定済みだがまだ改行していない通常色テキスト
        self.partial = ""       # 認識途中の薄字テキスト（確定で置き換わる）
        self.stream = stream or sys.stdout

    def _take_line(self, s: str):
        """s から1行分を切り出せれば (line, rest) を返す。まだなら None。"""
        if len(s) < self.width:
            return None
        lo, hi = self.min_line, self.width + self.overflow
        strong = [i + 1 for i, ch in enumerate(s) if ch in STRONG]
        weak = [i + 1 for i, ch in enumerate(s) if ch in WEAK]

        def pick(cands):
            good = [c for c in cands if lo <= c <= hi]
            return min(good, key=lambda c: abs(c - self.width)) if good else None

        cut = pick(strong) or pick(weak)
        if cut is None:
            if len(s) >= self.width + self.overflow:
                cut = self.width  # 区切りが無ければ強制改行
            else:
                return None       # もう少し文字がたまるのを待つ
        return s[:cut], s[cut:]

    def feed(self, text: str):
        """確定テキストを追記し、確定できる行があれば改行して表示する"""
        self.partial = ""   # 確定テキストが途中経過(薄字)を置き換える
        self.pending += text
        while True:
            r = self._take_line(self.pending)
            if r is None:
                break
            line, rest = r
            self._commit(line)
            self.pending = rest.lstrip("　 ")
        self._redraw()

    def set_partial(self, text: str):
        """認識途中のテキストを薄字で上書き表示する"""
        self.partial = text
        self._redraw()

    def _commit(self, line: str):
        # 現在行を消して確定行を改行付きで出す
        self.stream.write("\r\x1b[K" + line + "\n")
        self.stream.flush()

    def _redraw(self):
        # 現在行 = 確定pending(通常色) + 途中partial(薄字) を \r で上書き
        line = self.pending
        if self.partial:
            line += "\x1b[2m" + self.partial + "\x1b[0m"
        self.stream.write("\r\x1b[K" + line)
        self.stream.flush()

    def close(self):
        """残った未確定テキストを最後に確定して出す"""
        self.partial = ""
        if self.pending:
            self._commit(self.pending)
            self.pending = ""
