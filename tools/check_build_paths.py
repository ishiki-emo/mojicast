"""配布物にビルドした環境のパスが残っていないか調べる（Mac 版）。

PyInstaller の PYZ は中身が圧縮されているので、grep だけでは見つからない。
.app 内のファイルをそのまま調べたうえで、実行ファイルに埋め込まれた PYZ を
1モジュールずつ展開して調べる。

    python tools/check_build_paths.py dist/Mojicast.app

見つかったら 1 で終わる。Mojicast.spec の _sysconfigdata の置き換えが効いているかの確認を兼ねる。

調べる文字列は、このビルドに使った Python・仮想環境・リポジトリの場所と、手元なら
ホームフォルダ。CI（GitHub Actions）ではホームフォルダは調べない。PyPI の wheel
（opencc・sherpa-onnx など）も GitHub の Mac 上でビルドされていて同じ /Users/runner を含むため
"""
import marshal
import os
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader


def main(app):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    needles = {sys.base_prefix, sys.prefix, repo}
    if not os.environ.get("GITHUB_ACTIONS"):
        needles.add(os.path.expanduser("~"))
    needles = [n.encode() for n in sorted(needles)]

    def leaked(data):
        return any(n in data for n in needles)

    hits = []
    for root, _dirs, files in os.walk(app):
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            with open(path, "rb") as f:
                if leaked(f.read()):
                    hits.append(os.path.relpath(path, app))

    exe = os.path.join(app, "Contents", "MacOS", os.path.basename(app)[:-len(".app")])
    car = CArchiveReader(exe)
    modules = 0
    for entry in car.toc:
        data = car.extract(entry)
        if not entry.endswith(".pyz"):
            if isinstance(data, bytes) and leaked(data):
                hits.append(f"{entry}（実行ファイル内）")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            pyz = os.path.join(tmp, "x.pyz")
            with open(pyz, "wb") as f:
                f.write(data)
            reader = ZlibArchiveReader(pyz)
            for mod in reader.toc:
                code = reader.extract(mod, raw=True)
                if code is None:
                    continue    # パッケージの名前だけの項目
                modules += 1
                if not isinstance(code, bytes):
                    code = marshal.dumps(code)
                if leaked(code):
                    hits.append(f"{mod}（PYZ 内のモジュール）")

    print("調べた文字列: " + ", ".join(n.decode() for n in needles))
    print(f"PYZ のモジュール {modules} 個を確認")
    for h in hits:
        print(f"  残っている: {h}")
    if hits:
        print(f"NG: {len(hits)} か所にビルドした環境のパスが残っています")
        return 1
    if modules == 0:
        print("NG: PYZ が見つかりません（確認できていません）")
        return 1
    print("OK: ビルドした環境のパスは残っていません")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/")))
