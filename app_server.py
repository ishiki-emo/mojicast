"""
Caption Studio のHTTP/SSEサーバ

1ポートで全部を配信する:
  GET  /            overlay.html（OBSブラウザソース用）
  GET  /ui/<name>   GUIページ（cockpit / words）
  GET  /events      SSE: init / partial / final / level / state / style / clear
  GET/POST /api/... 設定・単語帳・エフェクト・プリセット・エンジン制御

認識エンジン(engine.CaptionEngine)はこのモジュールが保持し、
コールバックをSSEへ中継する。
"""
import os
import json
import queue
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from apppaths import BASE

APP_VERSION = "0.2.0"

CONFIG_PATH = os.path.join(BASE, "config.json")
EFFECTS_PATH = os.path.join(BASE, "effects.json")
PRESETS_PATH = os.path.join(BASE, "presets.json")
BOXES_PATH = os.path.join(BASE, "boxes.json")
HOTWORDS_PATH = os.path.join(BASE, "hotwords.txt")
BANNED_PATH = os.path.join(BASE, "banned.txt")
GLOSSARY_PATH = os.path.join(BASE, "glossary.txt")

DEFAULT_CONFIG = {
    "silence_ms": 300, "interval": 0.4, "max_utt": 12.0,
    "device": None, "precision": "int8-fp32", "punctuate": True,
    "use_hotwords": True, "hotwords_score": 2.0, "translate": False,
    "save_log": True, "mask_char": "○",
    "preset": "standard", "box": "none", "port": 8765,
}

_clients = []
_clients_lock = threading.Lock()
_engine = None
_engine_state = {"state": "stopped", "detail": ""}


# ---------------- 設定・データの読み書き ----------------

def _read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read_json(CONFIG_PATH, {}))
    return cfg


def save_config(cfg):
    _write_json(CONFIG_PATH, cfg)


def load_hotwords():
    """hotwords.txt → [{surface, reading, score}]"""
    from vocab import parse_vocab
    if not os.path.exists(HOTWORDS_PATH):
        return []
    return [{"surface": s, "reading": r, "score": sc or ""}
            for s, r, sc in parse_vocab(HOTWORDS_PATH)]


def save_hotwords(entries):
    lines = ["# 表記,読み,スコア  （読み・スコアは省略可 / #行はコメント）",
             "# 漢字を含む語は「読み」を必ず書く（読みで認識を誘導し、表記へ置換する）"]
    for e in entries:
        surface = (e.get("surface") or "").strip()
        if not surface:
            continue
        reading = (e.get("reading") or "").strip()
        score = str(e.get("score") or "").strip()
        line = surface
        if reading and reading != surface:
            line += f",{reading}"
            if score:
                line += f",{score}"
        elif score:
            line += f",{surface},{score}"
        lines.append(line)
    with open(HOTWORDS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def load_banned():
    """banned.txt → 禁止ワードのリスト（#行・空行は除外）"""
    try:
        with open(BANNED_PATH, encoding="utf-8") as f:
            return [ln.strip() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return []


def save_banned(words):
    lines = ["# 放送禁止ワードなどを1行に1語（#行はコメント）",
             "# ここの語は認識中・確定・ログ・英訳のすべてで伏せ字になります"]
    for w in words:
        w = (w or "").strip()
        if w:
            lines.append(w)
    with open(BANNED_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def load_glossary():
    """glossary.txt → [{ja, en}]（#行・不正行は除外）"""
    entries = []
    try:
        with open(GLOSSARY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "," not in line:
                    continue
                ja, en = line.split(",", 1)
                if ja.strip() and en.strip():
                    entries.append({"ja": ja.strip(), "en": en.strip()})
    except OSError:
        pass
    return entries


def save_glossary(entries):
    lines = ["# 英訳辞書: 字幕の表記,英訳  （#行はコメント）",
             "# 例: 癒色えも,ISHIKI Emo  → 英訳時にこの語の訳が固定されます"]
    for e in entries:
        ja = (e.get("ja") or "").strip()
        en = (e.get("en") or "").strip()
        if ja and en:
            lines.append(f"{ja},{en}")
    with open(GLOSSARY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def resolve_style(cfg):
    """現在のプリセット＋ボックス＋エフェクト＋ハイライト単語をまとめて返す"""
    presets = _read_json(PRESETS_PATH, {"presets": []})["presets"]
    style = next((p for p in presets if p["id"] == cfg.get("preset")),
                 presets[0] if presets else {})
    boxes = _read_json(BOXES_PATH, {"boxes": []})["boxes"]
    box = next((b for b in boxes if b["id"] == cfg.get("box")),
               boxes[0] if boxes else {})
    effects = _read_json(EFFECTS_PATH, {"effects": []})["effects"]
    hot_surfaces = [e["surface"] for e in load_hotwords()]
    return {"style": style, "box": box, "effects": effects,
            "hotwords": hot_surfaces}


# ---------------- システムフォント列挙（Windows GDI） ----------------

_fonts_cache = None


def list_system_fonts():
    """インストール済みフォントのファミリー名一覧（キャッシュあり）"""
    global _fonts_cache
    if _fonts_cache is not None:
        return _fonts_cache
    import ctypes
    from ctypes import wintypes

    class LOGFONTW(ctypes.Structure):
        _fields_ = [
            ("lfHeight", wintypes.LONG), ("lfWidth", wintypes.LONG),
            ("lfEscapement", wintypes.LONG), ("lfOrientation", wintypes.LONG),
            ("lfWeight", wintypes.LONG), ("lfItalic", ctypes.c_byte),
            ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte),
            ("lfCharSet", ctypes.c_byte), ("lfOutPrecision", ctypes.c_byte),
            ("lfClipPrecision", ctypes.c_byte), ("lfQuality", ctypes.c_byte),
            ("lfPitchAndFamily", ctypes.c_byte),
            ("lfFaceName", ctypes.c_wchar * 32),
        ]

    gdi32 = ctypes.WinDLL("gdi32")
    user32 = ctypes.WinDLL("user32")
    names = set()
    PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(LOGFONTW),
                              ctypes.c_void_p, wintypes.DWORD, wintypes.LPARAM)

    def cb(lf, tm, ftype, lparam):
        name = lf.contents.lfFaceName
        if name and not name.startswith("@"):   # @付きは縦書き用
            names.add(name)
        return 1

    hdc = user32.GetDC(0)
    lf = LOGFONTW()
    lf.lfCharSet = 1  # DEFAULT_CHARSET: 全キャラセットを列挙
    gdi32.EnumFontFamiliesExW(hdc, ctypes.byref(lf), PROC(cb), 0, 0)
    user32.ReleaseDC(0, hdc)
    _fonts_cache = sorted(names)
    return _fonts_cache


# ---------------- SSE ----------------

def broadcast(event: dict):
    data = json.dumps(event, ensure_ascii=False)
    with _clients_lock:
        for q in list(_clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


def _init_event():
    cfg = load_config()
    ev = {"type": "init"}
    ev.update(resolve_style(cfg))
    ev["state"] = _engine_state
    return ev


# ---------------- エンジン連携 ----------------

def get_engine():
    global _engine
    if _engine is None:
        from engine import CaptionEngine
        _engine = CaptionEngine(
            on_partial=lambda t: broadcast({"type": "partial", "text": t}),
            on_final=lambda t, fid: broadcast({"type": "final", "text": t, "id": fid}),
            on_level=lambda v: broadcast({"type": "level", "value": round(v, 3)}),
            on_state=_on_state,
            on_translation=lambda fid, en: broadcast(
                {"type": "translation", "id": fid, "text": en}),
        )
    return _engine


def _on_state(state, detail=""):
    _engine_state.update({"state": state, "detail": detail})
    broadcast({"type": "state", "state": state, "detail": detail})


# ---------------- HTTPハンドラ ----------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    # --- helpers ---
    def _send_body(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send_body(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    _MIME = {".html": "text/html; charset=utf-8",
             ".js": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8"}

    def _file(self, path, ctype=None):
        ext = os.path.splitext(path)[1]
        ctype = ctype or self._MIME.get(ext, "application/octet-stream")
        try:
            with open(path, "rb") as f:
                self._send_body(200, f.read(), ctype)
        except OSError:
            self._send_body(404, b"not found", "text/plain")

    def _body_json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    # --- GET ---
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/overlay"):
            self._file(os.path.join(BASE, "overlay.html"))
        elif path == "/events":
            self._events()
        elif path.startswith("/ui/"):
            name = os.path.basename(path[4:]) or "cockpit"
            if "." not in name:
                name += ".html"
            if os.path.splitext(name)[1] not in self._MIME:
                self._send_body(404, b"not found", "text/plain")
                return
            self._file(os.path.join(BASE, "ui", name))
        elif path == "/api/config":
            cfg = load_config()
            cfg["version"] = APP_VERSION   # 表示用（保存はされない: POSTでは既知キーのみ更新）
            self._json(cfg)
        elif path == "/api/hotwords":
            self._json({"entries": load_hotwords()})
        elif path == "/api/banned":
            self._json({"words": load_banned(),
                        "mask_char": load_config().get("mask_char", "○")})
        elif path == "/api/glossary":
            self._json({"entries": load_glossary()})
        elif path == "/api/effects":
            self._json(_read_json(EFFECTS_PATH, {"effects": []}))
        elif path == "/api/presets":
            self._json(_read_json(PRESETS_PATH, {"presets": []}))
        elif path == "/api/boxes":
            self._json(_read_json(BOXES_PATH, {"boxes": []}))
        elif path == "/api/fonts":
            try:
                self._json({"fonts": list_system_fonts()})
            except Exception as e:
                self._json({"fonts": [], "error": str(e)})
        elif path == "/api/devices":
            from engine import list_input_devices
            try:
                self._json({"devices": list_input_devices()})
            except Exception as e:
                self._json({"devices": [], "error": str(e)})
        elif path == "/api/status":
            self._json(_engine_state)
        else:
            self._send_body(404, b"not found", "text/plain")

    # --- POST ---
    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body_json()
        except (json.JSONDecodeError, ValueError):
            self._json({"ok": False, "error": "bad json"}, 400)
            return

        if path == "/api/config":
            cfg = load_config()
            cfg.update({k: v for k, v in body.items() if k in DEFAULT_CONFIG})
            save_config(cfg)
            # プリセット変更は表示側へ即反映
            ev = {"type": "style"}
            ev.update(resolve_style(cfg))
            broadcast(ev)
            self._json({"ok": True, "config": cfg})
        elif path == "/api/hotwords":
            save_hotwords(body.get("entries", []))
            ev = {"type": "style"}
            ev.update(resolve_style(load_config()))
            broadcast(ev)
            self._json({"ok": True})
        elif path == "/api/banned":
            save_banned(body.get("words", []))
            cfg = load_config()
            cfg["mask_char"] = (body.get("mask_char") or "○").strip() or "○"
            save_config(cfg)
            self._json({"ok": True})
        elif path == "/api/glossary":
            save_glossary(body.get("entries", []))
            self._json({"ok": True})
        elif path == "/api/effects":
            _write_json(EFFECTS_PATH,
                        {"effects": body.get("effects", [])})
            ev = {"type": "style"}
            ev.update(resolve_style(load_config()))
            broadcast(ev)
            self._json({"ok": True})
        elif path == "/api/presets":
            presets = body.get("presets", [])
            if not (isinstance(presets, list) and presets
                    and all(p.get("id") and p.get("name") for p in presets)):
                self._json({"ok": False, "error": "invalid presets"}, 400)
                return
            _write_json(PRESETS_PATH, {"presets": presets})
            cfg = load_config()
            if not any(p["id"] == cfg.get("preset") for p in presets):
                cfg["preset"] = presets[0]["id"]   # 使用中プリセットが消えたら先頭へ
                save_config(cfg)
            ev = {"type": "style"}
            ev.update(resolve_style(cfg))
            broadcast(ev)
            self._json({"ok": True})
        elif path == "/api/boxes":
            boxes = body.get("boxes", [])
            if not (isinstance(boxes, list) and boxes
                    and all(b.get("id") and b.get("name") for b in boxes)):
                self._json({"ok": False, "error": "invalid boxes"}, 400)
                return
            _write_json(BOXES_PATH, {"boxes": boxes})
            cfg = load_config()
            if not any(b["id"] == cfg.get("box") for b in boxes):
                cfg["box"] = boxes[0]["id"]
                save_config(cfg)
            ev = {"type": "style"}
            ev.update(resolve_style(cfg))
            broadcast(ev)
            self._json({"ok": True})
        elif path == "/api/engine":
            action = body.get("action")
            eng = get_engine()
            if action == "start":
                eng.start(load_config())
                self._json({"ok": True})
            elif action == "stop":
                eng.stop()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "unknown action"}, 400)
        elif path == "/api/clear":
            broadcast({"type": "clear"})
            self._json({"ok": True})
        else:
            self._send_body(404, b"not found", "text/plain")

    # --- SSE ---
    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q: "queue.Queue[str]" = queue.Queue(maxsize=200)
        with _clients_lock:
            _clients.append(q)
        try:
            self._sse_send(json.dumps(_init_event(), ensure_ascii=False))
            while True:
                try:
                    self._sse_send(q.get(timeout=15))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)

    def _sse_send(self, data: str):
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


def seed_defaults():
    """データファイルが無ければ defaults/ から複製する。

    個人用の単語帳等(hotwords.txt/effects.json/…)はgit管理外なので、
    git clone 直後の新規環境でも既定データで起動できるようにする。
    既存ファイルは上書きしない（利用者の編集を保持）。
    """
    src = os.path.join(BASE, "defaults")
    if not os.path.isdir(src):
        return
    for name in ("hotwords.txt", "effects.json", "presets.json", "boxes.json",
                 "banned.txt", "glossary.txt"):
        dst = os.path.join(BASE, name)
        if not os.path.exists(dst):
            try:
                shutil.copyfile(os.path.join(src, name), dst)
            except OSError:
                pass


def start(port: int = 8765):
    seed_defaults()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
