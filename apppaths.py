"""実行時パス解決（開発実行 / PyInstaller凍結 両対応）

- BASE: 設定・単語帳・アセット(overlay.html, ui/, silero_vad.onnx)を置く基準ディレクトリ。
        開発時はこのファイルのフォルダ、凍結(exe)時はexeのあるフォルダ。
- 凍結時は Hugging Face キャッシュを exe 隣の models/ に固定する。
  ・同梱版      : models/ にモデルが入った状態で配布 → 各ローダがローカルから読む（DL無し）
  ・軽量版(非同梱): models/ が空 → 各ローダのローカル優先読み込みが失敗し初回だけDL、
                    ダウンロード先も同じ models/ なので初回以降はポータブルに自己完結する。
  ローカル優先読み込み（local_files_only→失敗時DL）は各モジュール側で行うため、
  ここで OFFLINE を強制はしない（＝新しいモデルの追加DLも妨げない）。

凍結時は、pywebview(clr) を読み込む前に MOTW(ダウンロード印) を自己除去する。
これを最初に import すること（app.py の先頭で読む）。
"""
import os
import sys


def _unblock_downloaded_files(base):
    """ダウンロードZip展開時に付く MOTW(Zone.Identifier) を自分自身から除去する。

    これが残っていると .NET が pythonnet / WebView2 の管理DLLの読み込みを拒否し、
    clr 初期化に失敗して起動できない。exe は SmartScreen を越えれば起動できるので、
    clr を読む前のこの時点で剥がせば、利用者は手動のブロック解除が不要になる。
    要のDLLに MOTW がある時だけ全走査する（＝実質DL後の初回のみ・数秒）。
    """
    probe = os.path.join(base, "_internal", "pythonnet", "runtime",
                         "Python.Runtime.dll")
    try:
        with open(probe + ":Zone.Identifier"):
            pass                      # MOTWあり → 除去へ
    except OSError:
        return                        # MOTWなし（2回目以降）→ 即終了
    for root, _dirs, files in os.walk(base):
        for f in files:
            try:
                os.remove(os.path.join(root, f) + ":Zone.Identifier")
            except OSError:
                pass                  # 使用中/元々無し等は無視


if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    _unblock_downloaded_files(BASE)   # clr 読み込み前に MOTW を剥がす
    # DL先も参照先も exe 隣の models/ に集約（HF系を import する前に確定させる）
    os.environ.setdefault("HF_HOME", os.path.join(BASE, "models"))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
