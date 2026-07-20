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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from apppaths import BASE
import wordstore

APP_VERSION = "0.3.1"

DEFAULT_CONFIG = {
    "silence_ms": 300, "interval": 0.4, "max_utt": 12.0,
    "device": None, "precision": "int8-fp32", "punctuate": True,
    "use_hotwords": True, "hotwords_score": 2.0, "translate": False,
    "save_log": True, "mask_char": "○", "num_arabic": True,
    "preset": "standard", "box": "none", "port": 8765,
    "word_profile": "",     # 使用中の単語プロファイル（"" = 共通のみ）
    "theme": "dark",        # GUI窓のテーマ（dark / light）。overlayは対象外
    # 1対1コラボ（案A改・出力キャプチャ）。collab=Trueで②の入力を相手話者として取り込む
    # collab_source: "process"=アプリ音声を直接取り込み（方式2・推奨）/ "device"=仮想ケーブル
    "collab": False, "collab_source": "process",
    "collab_process": "", "collab_device": None,
    "self_name": "自分", "guest_name": "ゲスト",
    "guest_preset": "collab", "guest_box": "half-left",   # 相手の見た目割当
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
    wordstore.ensure_data()
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read_json(wordstore.data_path("config.json"), {}))
    return cfg


def save_config(cfg):
    _write_json(wordstore.data_path("config.json"), cfg)


def _presets_path():
    return wordstore.data_path("presets.json")


def _boxes_path():
    return wordstore.data_path("boxes.json")


def _seed_style_defaults():
    """アップデートで増えた既定プリセット/ボックスを既存環境へ一度だけ追加する。

    defaults/ は新規インストール時にしか複製されないため、後から足した既定は
    ここで既存の data/ にマージする。提供済みIDは config の seeded_styles に
    記録し、ユーザーが意図して消したものは二度と復活させない。
    """
    cfg = load_config()
    seeded = set(cfg.get("seeded_styles", []))
    changed = False
    for fname, key, path_fn in (("presets.json", "presets", _presets_path),
                                ("boxes.json", "boxes", _boxes_path)):
        defaults = _read_json(os.path.join(BASE, "defaults", fname),
                              {}).get(key, [])
        cur = _read_json(path_fn(), {key: []})[key]
        have = {x.get("id") for x in cur}
        added = False
        for item in defaults:
            mark = f"{key}:{item.get('id')}"
            if mark in seeded:
                continue
            if item.get("id") not in have:
                cur.append(item)
                added = True
            seeded.add(mark)
            changed = True
        if added:
            _write_json(path_fn(), {key: cur})
    if changed:
        cfg["seeded_styles"] = sorted(seeded)
        save_config(cfg)


# ---------------- mojipack（スタイルのエクスポート/インポート） ----------------

EXPORT_DIR_NAME = "export"


def _clean_str(v, maxlen):
    """インポート値の無害化: 文字列化・制御文字除去・長さ制限"""
    s = str(v) if isinstance(v, (str, int, float)) else ""
    s = "".join(c for c in s if ord(c) >= 32).strip()
    return s[:maxlen]


def _merge_pack_items(items, existing, kind, stamp):
    """パック内アイテムを既存リストへマージ形式で追加（上書きしない・ID再生成・
    名前衝突は「〜 (imported)」）。追加した件数を返す。"""
    if not isinstance(items, list):
        return 0
    names = {x.get("name") for x in existing}
    added = 0
    for i, item in enumerate(items[:100]):          # 件数上限（暴走ファイル対策）
        if not isinstance(item, dict):
            continue
        if len(json.dumps(item, ensure_ascii=False)) > 20000:
            continue                                 # 異常に大きい定義は捨てる
        name = _clean_str(item.get("name"), 60)
        if not name:
            continue
        if name in names:
            name += " (imported)"
        n = 2
        while name in names:                         # (imported) 同士の衝突も回避
            name = _clean_str(item.get("name"), 60) + f" (imported {n})"
            n += 1
        new = dict(item)
        new["id"] = f"imp-{kind}-{stamp}-{i}"
        new["name"] = name
        new["desc"] = _clean_str(item.get("desc"), 200)
        existing.append(new)
        names.add(name)
        added += 1
    return added


def import_mojipack(data):
    """mojipack をプリセット/ボックスへマージする。(結果dict, エラー文字列) を返す"""
    if not isinstance(data, dict) or "mojipack" not in data:
        return None, "mojipackファイルではありません"
    if len(json.dumps(data, ensure_ascii=False)) > 2 * 1024 * 1024:
        return None, "ファイルが大きすぎます"
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    presets = _read_json(_presets_path(), {"presets": []})["presets"]
    boxes = _read_json(_boxes_path(), {"boxes": []})["boxes"]
    np_ = _merge_pack_items(data.get("presets"), presets, "p", stamp)
    nb = _merge_pack_items(data.get("boxes"), boxes, "b", stamp)
    if np_ == 0 and nb == 0:
        return None, "取り込める定義がありませんでした"
    if np_:
        _write_json(_presets_path(), {"presets": presets})
    if nb:
        _write_json(_boxes_path(), {"boxes": boxes})
    return {"presets": np_, "boxes": nb}, None


def _pick(items, key, wanted):
    """items から key==wanted を探す。無ければ先頭（空なら {}）"""
    return next((x for x in items if x.get(key) == wanted),
                items[0] if items else {})


def resolve_style(cfg):
    """現在のプリセット＋ボックス＋エフェクト＋ハイライト単語をまとめて返す

    エフェクト・ハイライト単語は「共通＋使用中プロファイル」の合成（話者間で共有）。
    コラボON時は speakers に「自分／相手それぞれの style・box」を載せる
    （overlay/字幕ログが speaker で振り分けて描画）。
    """
    presets = _read_json(_presets_path(), {"presets": []})["presets"]
    boxes = _read_json(_boxes_path(), {"boxes": []})["boxes"]
    style = _pick(presets, "id", cfg.get("preset"))
    box = _pick(boxes, "id", cfg.get("box"))
    profile = cfg.get("word_profile", "")
    effects = wordstore.merged_effects(profile)
    hot_surfaces = [s for s, _r, _sc in wordstore.merged_hotwords(profile)]
    out = {"style": style, "box": box, "effects": effects,
           "hotwords": hot_surfaces}
    if cfg.get("collab"):
        self_name = (cfg.get("self_name") or "自分").strip() or "自分"
        guest_name = (cfg.get("guest_name") or "ゲスト").strip() or "ゲスト"
        gstyle = _pick(presets, "id", cfg.get("guest_preset"))
        gbox = _pick(boxes, "id", cfg.get("guest_box"))
        out["speakers"] = {
            self_name: {"style": style, "box": box},
            guest_name: {"style": gstyle, "box": gbox},
        }
    return out


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
            on_partial=lambda t, spk="": broadcast(
                {"type": "partial", "text": t, "speaker": spk}),
            on_final=lambda t, fid, spk="": broadcast(
                {"type": "final", "text": t, "id": fid, "speaker": spk}),
            on_level=lambda v, spk="": broadcast(
                {"type": "level", "value": round(v, 3), "speaker": spk}),
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

    def _profile_arg(self, value):
        """profile 指定を検証して返す（"" = 共通 / None = エラー応答済み）"""
        p = value.strip() if isinstance(value, str) else ""
        if not p:
            return ""
        if not wordstore.valid_profile_name(p):
            self._json({"ok": False, "error": "invalid profile"}, 400)
            return None
        if not wordstore.profile_exists(p):
            self._json({"ok": False, "error": "profile not found"}, 404)
            return None
        return p

    # --- GET ---
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
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
        elif path == "/api/profiles":
            self._json({"profiles": wordstore.list_profiles(),
                        "active": load_config().get("word_profile", "")})
        elif path == "/api/hotwords":
            p = self._profile_arg(query.get("profile"))
            if p is None:
                return
            self._json({"entries": wordstore.load_hotwords(p)})
        elif path == "/api/banned":
            p = self._profile_arg(query.get("profile"))
            if p is None:
                return
            self._json({"words": wordstore.load_banned(p),
                        "mask_char": load_config().get("mask_char", "○")})
        elif path == "/api/glossary":
            p = self._profile_arg(query.get("profile"))
            if p is None:
                return
            self._json({"entries": wordstore.load_glossary(p)})
        elif path == "/api/effects":
            p = self._profile_arg(query.get("profile"))
            if p is None:
                return
            self._json({"effects": wordstore.load_effects(p)})
        elif path == "/api/presets":
            self._json(_read_json(_presets_path(), {"presets": []}))
        elif path == "/api/boxes":
            self._json(_read_json(_boxes_path(), {"boxes": []}))
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
        elif path == "/api/loopback-apps":
            # 音声セッションを持つアプリ一覧（コラボ方式2の対象選択用）
            try:
                import proc_loopback
                self._json({"supported": proc_loopback.is_supported(),
                            "apps": proc_loopback.list_audio_apps()})
            except Exception as e:
                self._json({"supported": False, "apps": [], "error": str(e)})
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
            if "word_profile" in body:
                p = self._profile_arg(body.get("word_profile"))
                if p is None:
                    return
                body["word_profile"] = p
            if "port" in body:
                try:
                    port = int(body["port"])
                    if not (1024 <= port <= 65535):
                        raise ValueError
                    body["port"] = port
                except (TypeError, ValueError):
                    self._json({"ok": False,
                                "error": "ポートは 1024〜65535 の数値で指定してください"}, 400)
                    return
            if "theme" in body and body.get("theme") not in ("dark", "light"):
                body["theme"] = "dark"   # 未知値はダークへ（既定）
            if ("collab_source" in body
                    and body.get("collab_source") not in ("process", "device")):
                body["collab_source"] = "process"   # 未知値は推奨方式へ
            cfg = load_config()
            cfg.update({k: v for k, v in body.items() if k in DEFAULT_CONFIG})
            save_config(cfg)
            # プリセット・プロファイル変更は表示側へ即反映
            ev = {"type": "style"}
            ev.update(resolve_style(cfg))
            broadcast(ev)
            self._json({"ok": True, "config": cfg})
        elif path == "/api/profiles":
            self._post_profiles(body)
        elif path == "/api/hotwords":
            p = self._profile_arg(body.get("profile"))
            if p is None:
                return
            wordstore.save_hotwords(body.get("entries", []), p)
            ev = {"type": "style"}
            ev.update(resolve_style(load_config()))
            broadcast(ev)
            self._json({"ok": True})
        elif path == "/api/banned":
            p = self._profile_arg(body.get("profile"))
            if p is None:
                return
            wordstore.save_banned(body.get("words", []), p)
            if "mask_char" in body:      # 伏せ字文字は全体設定（プロファイル外）
                cfg = load_config()
                cfg["mask_char"] = (body.get("mask_char") or "○").strip() or "○"
                save_config(cfg)
            self._json({"ok": True})
        elif path == "/api/glossary":
            p = self._profile_arg(body.get("profile"))
            if p is None:
                return
            wordstore.save_glossary(body.get("entries", []), p)
            self._json({"ok": True})
        elif path == "/api/effects":
            p = self._profile_arg(body.get("profile"))
            if p is None:
                return
            wordstore.save_effects(body.get("effects", []), p)
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
            _write_json(_presets_path(), {"presets": presets})
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
            _write_json(_boxes_path(), {"boxes": boxes})
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
        elif path == "/api/mojipack/export":
            ids_p = set(body.get("presets") or [])
            ids_b = set(body.get("boxes") or [])
            presets = [p for p in _read_json(_presets_path(), {"presets": []})["presets"]
                       if p.get("id") in ids_p]
            boxes = [b for b in _read_json(_boxes_path(), {"boxes": []})["boxes"]
                     if b.get("id") in ids_b]
            if not presets and not boxes:
                self._json({"ok": False, "error": "エクスポート対象がありません"}, 400)
                return
            pack = {"mojipack": 1, "app": "Mojicast",
                    "presets": presets, "boxes": boxes}
            d = wordstore.data_path(EXPORT_DIR_NAME)
            os.makedirs(d, exist_ok=True)
            from datetime import datetime
            fname = "style_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".mojipack"
            with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
                json.dump(pack, f, ensure_ascii=False, indent=2)
            self._json({"ok": True, "file": fname,
                        "path": os.path.join(d, fname)})
        elif path == "/api/mojipack/import":
            result, err = import_mojipack(body.get("data"))
            if err:
                self._json({"ok": False, "error": err}, 400)
                return
            ev = {"type": "style"}
            ev.update(resolve_style(load_config()))
            broadcast(ev)
            self._json({"ok": True, **result})
        elif path == "/api/mojipack/open":
            d = wordstore.data_path(EXPORT_DIR_NAME)
            os.makedirs(d, exist_ok=True)
            try:
                os.startfile(d)            # エクスプローラで開く（Windows）
                self._json({"ok": True})
            except OSError as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/clear":
            broadcast({"type": "clear"})
            self._json({"ok": True})
        else:
            self._send_body(404, b"not found", "text/plain")

    def _post_profiles(self, body):
        """プロファイルの作成・削除（{action, name}）"""
        action = body.get("action")
        name = (body.get("name") or "").strip()
        if action == "create":
            if not wordstore.valid_profile_name(name):
                self._json({"ok": False,
                            "error": "使えない名前です（記号 \\ / : * ? \" < > | は不可・40文字まで）"}, 400)
                return
            if wordstore.profile_exists(name):
                self._json({"ok": False, "error": "同名のプロファイルがあります"}, 400)
                return
            copy_from = (body.get("copy_from") or "").strip()
            if copy_from and not wordstore.profile_exists(copy_from):
                self._json({"ok": False, "error": "コピー元が見つかりません"}, 404)
                return
            wordstore.create_profile(name, copy_from)
        elif action == "delete":
            if not wordstore.profile_exists(name):
                self._json({"ok": False, "error": "profile not found"}, 404)
                return
            wordstore.delete_profile(name)
            cfg = load_config()
            if cfg.get("word_profile") == name:   # 使用中を消したら共通のみへ
                cfg["word_profile"] = ""
                save_config(cfg)
        else:
            self._json({"ok": False, "error": "unknown action"}, 400)
            return
        # プロファイル一覧・合成結果の変化を全画面へ反映
        ev = {"type": "style"}
        ev.update(resolve_style(load_config()))
        broadcast(ev)
        self._json({"ok": True, "profiles": wordstore.list_profiles()})

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


def start(port: int = 8765):
    wordstore.ensure_data()   # data/ 作成・旧配置からの移行・既定データの複製
    _seed_style_defaults()    # 後から増えた既定スタイルを既存環境へ一度だけ追加
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
