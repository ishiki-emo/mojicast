"""実行時パス解決（開発実行 / PyInstaller凍結 両対応）

- BASE: 設定・単語帳・アセット(overlay.html, ui/, silero_vad.onnx)を置く基準ディレクトリ。
        開発時はこのファイルのフォルダ、凍結(exe)時はexeのあるフォルダ。
- 凍結時は Hugging Face キャッシュを exe 隣の models/ に固定する。
  ・同梱版      : models/ にモデルが入った状態で配布 → 各ローダがローカルから読む（DL無し）
  ・軽量版(非同梱): models/ が空 → 各ローダのローカル優先読み込みが失敗し初回だけDL、
                    ダウンロード先も同じ models/ なので初回以降はポータブルに自己完結する。
  ローカル優先読み込み（local_files_only→失敗時DL）は各モジュール側で行うため、
  ここで OFFLINE を強制はしない（＝新しいモデルの追加DLも妨げない）。

このモジュールは HF 系ライブラリより先に import すること（app.py の先頭で読む）。
"""
import os
import sys

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    # DL先も参照先も exe 隣の models/ に集約（HF系を import する前に確定させる）
    os.environ.setdefault("HF_HOME", os.path.join(BASE, "models"))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
