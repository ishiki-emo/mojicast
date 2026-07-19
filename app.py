"""
Mojicast — 配信用リアルタイム字幕アプリ

起動方法:
    reazonspeech-env\\Scripts\\python.exe app.py

- コックピット窓（メイン）: エンジン制御・設定・字幕モニタ・プリセット
- 単語スタジオ窓（別窓）  : ホットワード登録 / エフェクト単語の管理
- OBS連携                : ブラウザソースに http://localhost:8765 を指定
"""
import sys
import socket

import apppaths  # noqa: F401  HF系より先に読み込み、凍結時のパス/オフライン設定を確定

import webview

import app_server


def _ui_scale() -> float:
    """起動モニタの解像度からUIの拡大率を決める。
    QHD(2560)幅以上は等倍、狭い画面ほど縮小（下限0.75 / FullHDで約0.8）。
    overlay(OBS表示)には効かせず、コックピット等のGUI窓だけに適用する。"""
    try:
        import ctypes
        w = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN（論理px）
        return max(0.75, min(1.0, round(w / 2400, 2)))
    except Exception:
        return 1.0


UI_SCALE = _ui_scale()


class JsApi:
    """コックピットの JS から呼べるネイティブAPI"""

    def __init__(self):
        self._windows = {}   # key -> webview.Window

    def _open(self, key, title, path, width, height):
        if self._windows.get(key) is not None:
            return
        port = app_server.load_config().get("port", 8765)
        sep = "&" if "?" in path else "?"
        w = webview.create_window(
            f"{title} — Mojicast",
            f"http://127.0.0.1:{port}{path}{sep}s={UI_SCALE}",
            width=int(width * UI_SCALE), height=int(height * UI_SCALE),
            background_color="#0d1117")
        self._windows[key] = w
        w.events.closed += lambda: self._windows.pop(key, None)

    def open_studio(self, query=""):
        """統合スタジオ（文字スタイル/レイアウト/単語/共有）を別窓で開く

        query 例: "tab=words" / "tab=box" / "preset=cute" / "tab=box&box=lower"
        """
        path = "/ui/studio" + (f"?{query}" if query else "")
        self._open("studio", "スタジオ", path, 1120, 840)

    # 後方互換: 旧エントリはすべて統合スタジオへ委譲
    def open_words(self):
        self.open_studio("tab=words")

    def open_style(self, query=""):
        self.open_studio(query)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _fatal(msg: str):
    """起動不能エラーの通知。windowed exe ではコンソールが無いので MessageBox で出す"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "Mojicast", 0x10)  # MB_ICONERROR
    except Exception:
        print(msg)
    sys.exit(1)


def main():
    cfg = app_server.load_config()
    port = cfg.get("port", 8765)
    if not (isinstance(port, int) and 1024 <= port <= 65535):
        port = 8765   # 設定が壊れていても起動は守る
    if not _port_free(port):
        _fatal(f"ポート {port} は使用中のため起動できません。\n\n"
               "・Mojicast が既に起動していないか確認してください\n"
               "　（タスクバーやタスクマネージャの Mojicast / msedgewebview2）\n"
               "・別のアプリがこのポートを使っている場合は、コックピットの\n"
               "　「OBS 連携 → ポート」で変更できます（設定ファイルは data\\config.json）")

    app_server.start(port)

    api = JsApi()
    window = webview.create_window(
        "Mojicast",
        f"http://127.0.0.1:{port}/ui/cockpit?s={UI_SCALE}",
        width=int(1100 * UI_SCALE), height=int(720 * UI_SCALE),
        min_size=(int(900 * UI_SCALE), int(600 * UI_SCALE)),
        background_color="#0d1117", js_api=api)

    def on_closed():
        # メイン窓を閉じたら認識も止める
        try:
            eng = app_server.get_engine()
            eng.stop()
        except Exception:
            pass

    window.events.closed += on_closed
    webview.start()  # 窓が全部閉じるまでブロック


if __name__ == "__main__":
    main()
