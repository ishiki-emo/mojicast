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
import sys
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from apppaths import BASE
import wordstore

APP_VERSION = "0.5.1"

# 更新チェック用のマニフェスト（GitHub raw）。リリース時に latest.json を更新する。
# 中身: {"version": "0.5.1", "url": "<配布ページ>", "notes": "<一行紹介>"}
UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/ishiki-emo/mojicast/main/latest.json"
)

_config_lock = threading.RLock()
_update_lock = threading.Lock()
_update_cache = None      # 直近の判定結果（成功時のみ）
_update_cache_at = 0.0    # time.monotonic() ベースの取得時刻

DEFAULT_CONFIG = {
    "silence_ms": 300, "interval": 0.4, "max_utt": 12.0,
    "device": None, "precision": "int8-fp32", "punctuate": True,
    "asr_model": "k2-ja",   # 認識モデル（k2-ja=日本語特化 / sensevoice=多言語）
    "asr_lang": "auto",     # sensevoice時の認識言語（auto/ja/zh/en/ko/yue）
    "setup_suggested": False,  # 初回の「おすすめ設定」提案を表示済みか
    "use_hotwords": True, "hotwords_score": 2.0, "translate": False,
    "translate_lang": "en",  # 翻訳先（en/zh/zh_tw/zh_hk/id/ja/ko）
    "save_log": True, "mask_char": "○", "num_arabic": True,
    "word_fx": True,        # 単語エフェクトの表示（OFFでも認識誘導・置換は有効）
    "preset": "standard", "box": "none", "port": 8765,
    "word_profile": "",     # 使用中の単語プロファイル（"" = 共通のみ）
    "theme": "light",       # GUI窓のテーマ（light / dark）。既定ライト。overlayは対象外
    "ui_lang": "ja",        # GUI表示言語（ja / zh / en）。明示選択・既定ja。overlayは対象外
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
    with _config_lock:
        wordstore.ensure_data()
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(_read_json(wordstore.data_path("config.json"), {}))
        # 旧版や一時的な保存競合で JSON null が残っても、単語スコープとして
        # フロント・合成処理へ渡さない。共通のみは常に空文字で表現する。
        profile = cfg.get("word_profile")
        if (not isinstance(profile, str)
                or (profile and not wordstore.profile_exists(profile))):
            cfg["word_profile"] = ""
        return cfg


def save_config(cfg):
    with _config_lock:
        _write_json(wordstore.data_path("config.json"), cfg)


def _version_tuple(s):
    """"v0.5.0" → (0, 5, 0)。数値以外の接尾辞は切り捨てて比較用に正規化。"""
    parts = []
    for chunk in str(s).lstrip("vV").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def _check_update(force=False):
    """latest.json を取得して更新有無を判定。結果は一定時間キャッシュする。

    戻り値: {"current", "latest", "update_available", "url", "notes"}。
    ネットワーク不通・パース失敗時は update_available=False で静かに返す。
    """
    import time
    import urllib.request

    global _update_cache, _update_cache_at
    with _update_lock:
        if (not force and _update_cache is not None
                and (time.monotonic() - _update_cache_at) < 6 * 3600):
            return _update_cache

    result = {
        "current": APP_VERSION, "latest": APP_VERSION,
        "update_available": False, "url": "", "notes": "",
    }
    try:
        req = urllib.request.Request(
            UPDATE_MANIFEST_URL,
            headers={"User-Agent": f"Mojicast/{APP_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = str(data.get("version", "")).strip()
        if latest:
            result["latest"] = latest
            result["url"] = str(data.get("url", "")).strip()
            result["notes"] = str(data.get("notes", "")).strip()
            result["update_available"] = (
                _version_tuple(latest) > _version_tuple(APP_VERSION)
            )
        with _update_lock:
            _update_cache = result
            _update_cache_at = time.monotonic()
    except Exception:
        # オフライン等は通常運用。前回の成功結果があればそれを返す。
        with _update_lock:
            if _update_cache is not None:
                return _update_cache
    return result


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
        # 旧リリックプリセットはユーザーの位置・サイズを保ったまま、名称と
        # 新エンジン用の未設定項目だけを更新する。
        if key == "boxes":
            for item in cur:
                if item.get("id") != "lyric":
                    continue
                if item.get("name") == "リリックビデオ":
                    item["name"] = "リリックビデオ風字幕"
                    item["desc"] = "話した内容をおまかせ演出でリリックビデオ風に表示"
                    changed = True
                if "lyricMood" not in item:
                    item["lyricMood"] = "auto"
                    changed = True
                if "lyricMaxScenes" not in item:
                    item["lyricMaxScenes"] = 2
                    changed = True
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
        if added or (key == "boxes" and changed):
            _write_json(path_fn(), {key: cur})
    if changed:
        cfg["seeded_styles"] = sorted(seeded)
        save_config(cfg)


# ---------------- 環境検出（初回のおすすめ設定用） ----------------

def _os_ui_lang():
    """OSの表示言語 → 'ja'/'zh'/'en'/'ko'/'other'"""
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF
        return {0x11: "ja", 0x04: "zh", 0x09: "en", 0x12: "ko"}.get(lid, "other")
    except Exception:
        return "other"


def _cpu_name():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        return ""


def cpu_tier(name):
    """CPU名 → 適性帯 'best'|'ok'|'delta'|'x'|None（判定不能）

    マニュアル2章「CPUの向き・不向き / 世代の見方」のコード化。
    誤った提案は無提案より悪いため、確信のない型番は None（＝何も提案しない）。
    """
    import re
    n = name.lower()
    if re.search(r"\b(n\d{2,3}\b|celeron|atom|pentium)", n):
        return "x"                                   # 省電力系
    m = re.search(r"ryzen\s*[3579]\s*(\d{4})(x3d|[a-z]{0,2})", n)
    if m:
        num, suf = m.group(1), m.group(2)
        mobile = suf not in ("", "x", "xt", "x3d")   # U/HS/H/G等はモバイル/APU
        if num[0] in "789":
            if mobile:                               # ノート用は中身が混在
                return "best" if num[2] >= "4" else "delta"   # 十の位4以上=Zen4
            return "best"
        if num[0] in "2345":
            return "delta"
        return None
    if "core" in n and "ultra" in n:
        return "ok"                                  # Core Ultra世代
    m = re.search(r"i[3579]-(\d{4,5})(g\d)?", n)
    if m:
        num, gsuf = m.group(1), m.group(2)
        gen = int(num[:2]) if (len(num) == 5 or gsuf) else int(num[0])
        if gen >= 11:
            return "ok"                              # 11世代=AVX-512 / 12以降=AVX-VNNI
        if gen == 10:
            return "ok" if gsuf else "delta"         # G付き=Ice Lake(AVX-512)
        if gen >= 4:
            return "delta"
        return "x"                                   # AVX2非対応世代
    return None


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
    # 単語エフェクトOFF時は表示用の装飾情報だけ空にする（描画側が全ペインで
    # プレーン表示になる）。認識誘導・単語置換・伏せ字はエンジン側の経路なので影響しない
    if cfg.get("word_fx", True):
        effects = wordstore.merged_effects(profile)
        hot_surfaces = [s for s, _r, _sc in wordstore.merged_hotwords(profile)]
    else:
        effects, hot_surfaces = [], []
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
    # 新しく開いたGUI窓がlocalStorageや次の変更イベントに依存せず、
    # 現在のテーマへ即座に揃えられるよう初期イベントにも含める。
    ev["theme"] = cfg.get("theme", "light")
    ev["ui_lang"] = cfg.get("ui_lang", "ja")
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
        # GUIは開発中に同じURLで頻繁に更新される。WebView2の復元キャッシュも含め、
        # 古いHTML/JSを再利用させない。
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
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
        elif path == "/api/update-check":
            # GUIから起動時に1回呼ぶ。force=1 で手動再取得。ネット不通でも安全に返す。
            self._json(_check_update(force=query.get("force") == "1"))
        elif path == "/api/profiles":
            self._json({"profiles": wordstore.list_profiles(),
                        "active": load_config().get("word_profile", "")})
        elif path == "/api/env-suggest":
            # 初回のおすすめ設定用の環境情報。lang/cpu クエリはテスト・サポート用の上書き
            name = query.get("cpu", _cpu_name())
            self._json({"os_lang": query.get("lang", _os_ui_lang()),
                        "cpu": {"name": name, "tier": cpu_tier(name)}})
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
        elif path == "/api/style-defaults":
            # 同梱項目の「初期状態に戻す」と新規作成の基準データ。
            self._json({
                "presets": _read_json(os.path.join(BASE, "defaults", "presets.json"),
                                      {"presets": []}).get("presets", []),
                "boxes": _read_json(os.path.join(BASE, "defaults", "boxes.json"),
                                    {"boxes": []}).get("boxes", []),
            })
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
        elif path == "/api/perf":
            # リモート切り分け用: デコード回数・平均所要時間（今セッション累計）
            p = getattr(_engine, "perf", None) if _engine else None
            if not p:
                self._json({"state": _engine_state, "perf": None})
                return
            import time as _t
            self._json({
                "state": _engine_state,
                "uptime_sec": round(_t.time() - p["since"], 1),
                "partial": {"count": p["partial_n"],
                            "avg_ms": round(p["partial_ms"] / p["partial_n"], 1)
                            if p["partial_n"] else 0},
                "final": {"count": p["final_n"],
                          "avg_ms": round(p["final_ms"] / p["final_n"], 1)
                          if p["final_n"] else 0},
            })
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
                body["theme"] = "light"   # 未知値はライトへ（既定）
            if "ui_lang" in body and body.get("ui_lang") not in ("ja", "zh", "en"):
                body["ui_lang"] = "ja"    # 未知値は日本語へ（既定）
            if ("collab_source" in body
                    and body.get("collab_source") not in ("process", "device")):
                body["collab_source"] = "process"   # 未知値は推奨方式へ
            # ThreadingHTTPServer上で複数の設定窓が同時保存しても、後着の
            # read-modify-write が先着変更を巻き戻さないよう一連を排他する。
            with _config_lock:
                cfg = load_config()
                cfg.update({k: v for k, v in body.items() if k in DEFAULT_CONFIG})
                save_config(cfg)
            # プリセット・プロファイル変更は表示側へ即反映
            ev = {"type": "style"}
            ev.update(resolve_style(cfg))
            broadcast(ev)
            # GUIテーマは開いている全ウインドウへ即時反映する。
            # overlay.html はこのイベントを購読しないためOBS字幕には影響しない。
            if "theme" in body:
                broadcast({"type": "theme", "theme": cfg.get("theme", "light")})
            if "ui_lang" in body:
                broadcast({"type": "ui_lang", "ui_lang": cfg.get("ui_lang", "ja")})
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
                with _config_lock:
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
            with _config_lock:
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
            with _config_lock:
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
        elif path == "/api/logs/open":
            # 文字起こしログは engine.py と同じく BASE/logs に保存される。
            # まだ配信していない場合も、入口としてフォルダを作ってから開く。
            d = os.path.join(BASE, "logs")
            os.makedirs(d, exist_ok=True)
            try:
                os.startfile(d)            # エクスプローラで開く（Windows）
                self._json({"ok": True, "path": d})
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
            with _config_lock:
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


class _QuietHTTPServer(ThreadingHTTPServer):
    """クライアント側の切断を無害なノイズとして扱う。

    テーマ切替の再読込や子ウィンドウを閉じた際、keep-alive 接続が読み取り途中で
    切られると WinError 10053/10054 等のトレースバックが標準の handle_error から
    出る。動作には影響しないため、これらの切断だけ握りつぶし、他の例外は従来通り。
    """

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def start(port: int = 8765):
    wordstore.ensure_data()   # data/ 作成・旧配置からの移行・既定データの複製
    _seed_style_defaults()    # 後から増えた既定スタイルを既存環境へ一度だけ追加
    server = _QuietHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
