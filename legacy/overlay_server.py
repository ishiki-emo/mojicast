"""
字幕オーバーレイ用のローカルHTTPサーバ（OBSブラウザソース向け）

- GET /          : overlay.html を返す（OBSのブラウザソースで開く）
- GET /events    : SSE(Server-Sent Events)で認識テキストを push し続ける

Python標準ライブラリのみ（websocket等の追加インストール不要）。
認識側から push_partial() / push_final() を呼ぶと、接続中の全ブラウザへ配信する。
"""
import os
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_clients = []                      # 接続中SSEクライアントの queue 一覧
_clients_lock = threading.Lock()
_hot_words = []                    # ハイライトする登録単語（表記）
_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overlay.html")


def set_hotwords(words):
    """ハイライト対象の単語リストを設定"""
    global _hot_words
    _hot_words = sorted(set(w for w in words if w), key=len, reverse=True)


def _broadcast(event: dict):
    data = json.dumps(event, ensure_ascii=False)
    with _clients_lock:
        for q in list(_clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


def push_partial(text: str):
    """認識途中テキスト（薄字表示）を配信"""
    _broadcast({"type": "partial", "text": text})


def push_final(text: str):
    """確定テキストを配信"""
    _broadcast({"type": "final", "text": text})


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # アクセスログは出さない

    def do_GET(self):
        if self.path.startswith("/events"):
            self._serve_events()
        elif self.path.split("?")[0] == "/ui/fx.js":
            # overlay.html が参照する共有描画モジュール
            self._serve_file(
                os.path.join(os.path.dirname(_HTML_PATH), "ui", "fx.js"),
                "text/javascript; charset=utf-8")
        else:
            self._serve_file(_HTML_PATH, "text/html; charset=utf-8")

    def _serve_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q: "queue.Queue[str]" = queue.Queue(maxsize=100)
        with _clients_lock:
            _clients.append(q)
        try:
            # 接続直後にハイライト単語を送る
            init = json.dumps({"type": "init", "hotwords": _hot_words},
                              ensure_ascii=False)
            self._send(init)
            while True:
                try:
                    data = q.get(timeout=15)
                    self._send(data)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # 接続維持のハートビート
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # ブラウザ切断
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)

    def _send(self, data: str):
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


def start(port: int = 8765):
    """サーバをバックグラウンドスレッドで起動して返す"""
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
