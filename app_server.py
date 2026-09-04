"""
Caption Studio のHTTP/SSEサーバ

1ポートで全部を配信する:
  GET  /            overlay.html（OBSブラウザソース用）
  GET  /ui/<name>   GUIページ（cockpit / words）
  GET  /events      SSE: init / partial / clear_partial / final / level / state / style / clear / warn
  GET/POST /api/... 設定・単語帳・エフェクト・プリセット・エンジン制御

認識エンジン(engine.CaptionEngine)はこのモジュールが保持し、
コールバックをSSEへ中継する。
"""
import os
import sys
import json
import base64
import queue
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from apppaths import BASE, DATA_BASE
import platform_compat
import vrcchat
import wordstore

APP_VERSION = "0.9.7"

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
    "final_only": False,    # 途中経過（薄文字）を出さず確定字幕だけ表示する
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
    # GUI窓の拡大率。"auto"=起動モニタから自動判定 / 0.75〜1.5=利用者の明示指定。
    # overlayは対象外（OBSに映る字幕の大きさは変わらない）
    "ui_scale": "auto",
    # 窓ごとの大きさ・位置（閉じた時点の値を次回起動で復元）。
    # {"cockpit": {"x","y","w","h","scale"}, ...}。書き換えは save_window_geometry 経由
    "window_geometry": {},
    # 1対1コラボ（案A改・出力キャプチャ）。collab=Trueで②の入力を相手話者として取り込む
    # collab_source: "process"=アプリ音声を直接取り込み（方式2・推奨）/ "device"=仮想ケーブル
    "collab": False, "collab_source": "process",
    "collab_process": "", "collab_device": None,
    "self_name": "自分", "guest_name": "ゲスト",
    "guest_preset": "collab", "guest_box": "half-left",   # 相手の見た目割当
    # ボイスコマンド（「〈ウェイクワード〉、レイアウトを〇〇に」で操作）
    "vc_enabled": False,
    "vc_wake": [],          # ウェイクワード（ゆれ表記を含む複数形。空=無効）
    # VRChat連携: 確定字幕をOSCチャットボックスへ転送（エンジン再起動不要）
    "vrchat": False,
    "vrchat_source": "ja",  # 送信内容（ja=文字起こし / tr=訳文）
    "vrchat_port": 9000,    # VRChatのOSC受信ポート（既定9000）
    # 音イベント演出（笑い・拍手等 → 画像/パーティクル）。既定OFF
    # （ONで初回27MB DL＋CPU約5%。低スペック機に黙って足さない）
    "sound_fx": False,
    "sound_fx_rules": {},   # グループ名 → 演出ルール（soundfx_settings.html が編集）
}

_clients = []
_clients_lock = threading.Lock()
_engine = None
_engine_lock = threading.Lock()
_engine_state = {"state": "stopped", "detail": ""}
_engine_state_lock = threading.Lock()

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024
MAX_SSE_CLIENTS = 64
SSE_QUEUE_MAX_ITEMS = 200
SSE_QUEUE_RECOVER_ITEMS = 100


class _RequestBodyTooLarge(ValueError):
    pass


def _parse_authority(value):
    """Host/Origin の authority を (hostname, port) に正規化する。

    Host は ``localhost:8765`` のように scheme を持たないため ``//`` を補う。
    不正なポート表記などは (None, None) として拒否側へ倒す。
    """
    try:
        parsed = urlparse(value if "://" in value else "//" + value)
        host = (parsed.hostname or "").lower().rstrip(".")
        return host, parsed.port
    except (TypeError, ValueError):
        return None, None


def _is_loopback_host(value, port):
    """Host が、このMojicastサーバー自身を指すものか。"""
    if not value:
        # HTTP/1.0 の診断クライアント等との互換用。ブラウザのHTTP/1.1要求は
        # 必ずHostを送るため、DNSリバインディング対策を弱めることはない。
        return True
    host, requested_port = _parse_authority(value)
    return (host in _LOOPBACK_HOSTS
            and requested_port in (None, port))


def _is_loopback_origin(value, port):
    """Origin が同じPC上のMojicast（localhost/127.0.0.1・同一ポート）か。"""
    if not value or value == "null":
        return False
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        origin_port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "http"
        and host in _LOOPBACK_HOSTS
        and origin_port == port
        and parsed.username is None
        and parsed.password is None
        and parsed.path in ("", "/")
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


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


# GUI窓の拡大率の許容範囲。下限は既存の自動判定と揃え、上限はFullHDでも
# コックピット（1100x800基準）が作業領域に収まる範囲として1.5とする。
UI_SCALE_MIN = 0.75
UI_SCALE_MAX = 1.5


def normalize_ui_scale(value):
    """UI倍率を "auto"（自動判定）か UI_SCALE_MIN〜UI_SCALE_MAX の数値へ正規化する。

    壊れた値やレンジ外で窓が作れなくなるのを防ぐため、保存前と読み出し後の
    両方をここに通す。解釈できない値は "auto"（従来動作）へ倒す。
    """
    if value is None or value == "" or value == "auto":
        return "auto"
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return "auto"
    if scale != scale:   # NaN。比較が常に偽になりクランプをすり抜ける
        return "auto"
    return round(min(UI_SCALE_MAX, max(UI_SCALE_MIN, scale)), 2)


def normalize_window_geometry(value):
    """窓の記憶値を {キー: {"x","y","w","h","scale"}} の形へ正規化する。

    壊れている項目だけを落とし、他の窓の記憶は残す。w/h が無い要素は
    復元に使えないため捨てる（位置だけ復元しても大きさが決まらない）。
    """
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for key, geom in value.items():
        if not isinstance(key, str) or not isinstance(geom, dict):
            continue
        entry = {}
        for field in ("x", "y", "w", "h", "scale"):
            raw = geom.get(field)
            # bool は int の派生。True が座標1として通らないよう先に弾く
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            if raw != raw:   # NaN
                continue
            entry[field] = round(float(raw), 2) if field == "scale" else int(raw)
        if entry.get("w") and entry.get("h"):
            cleaned[key[:32]] = entry
    return cleaned


def save_window_geometry(key, geom):
    """窓の大きさ・位置を次回起動用に記録する。

    複数の窓がほぼ同時に閉じても、後着の read-modify-write が先着の記録を
    巻き戻さないよう一連を排他する。
    """
    with _config_lock:
        cfg = load_config()
        store = dict(cfg.get("window_geometry") or {})   # DEFAULT_CONFIG を汚さない
        store[str(key)] = geom
        cfg["window_geometry"] = normalize_window_geometry(store)
        save_config(cfg)


def resolve_ui_scale(cfg=None):
    """実際にGUI窓へ適用する拡大率。明示指定があればそれを、なければ自動判定を返す。"""
    if cfg is None:
        cfg = load_config()
    scale = normalize_ui_scale(cfg.get("ui_scale", "auto"))
    return platform_compat.ui_scale() if scale == "auto" else scale


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


def _scenes_path():
    return wordstore.data_path("scenes.json")


def _vc_commands_path():
    return wordstore.data_path("vc_commands.json")


# カスタムコマンドの動作カタログ（保存時の検証と実行の対応表）
_VC_ACTION_TYPES = ("scene", "box", "preset", "random",
                    "translate_lang", "translate_on", "translate_off", "clear")
_VC_RANDOM_TARGETS = ("scene", "box", "preset")
_VC_TRANS_LANGS = ("en", "zh", "zh_tw", "zh_hk", "id", "ja", "ko")


def _valid_vc_command(c):
    """カスタムコマンド1件の形を検証する（言い回し最大5件・各30文字）"""
    if not (isinstance(c, dict) and c.get("id")
            and isinstance(c.get("phrases"), list)):
        return False
    phrases = [str(p).strip() for p in c["phrases"] if str(p).strip()]
    if not phrases or len(phrases) > 5 or any(len(p) > 30 for p in phrases):
        return False
    a = c.get("action")
    if not (isinstance(a, dict) and a.get("type") in _VC_ACTION_TYPES):
        return False
    t = a["type"]
    if t in ("scene", "box", "preset"):
        return isinstance(a.get("id"), str) and a["id"] != ""
    if t == "random":
        return a.get("target") in _VC_RANDOM_TARGETS
    if t == "translate_lang":
        return a.get("lang") in _VC_TRANS_LANGS
    return True    # translate_on / translate_off / clear は追加項目なし


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
    return platform_compat.os_ui_lang()


def _cpu_name():
    return platform_compat.cpu_name()


def cpu_tier(name):
    """CPU名 → 適性帯 'best'|'ok'|'delta'|'x'|None（判定不能）

    マニュアル2章「CPUの向き・不向き / 世代の見方」のコード化。
    誤った提案は無提案より悪いため、確信のない型番は None（＝何も提案しない）。
    """
    import re
    n = name.lower()
    if n.startswith("apple m"):
        return "best"                                # Apple Silicon（int8推論が得意）
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


def _translating(cfg):
    """「実際に翻訳が動いているか」。エンジン稼働中はエンジンの実態を優先する。

    設定は translate=True でも、停止→開始の再起動待ち・翻訳モデルのロード失敗・
    原文=翻訳先ではエンジンは翻訳しない。cfg の値だけを信じると、翻訳のみ表示が
    「…」のまま何も出なくなるため、稼働中は engine.translating() を返す。
    """
    with _engine_state_lock:
        running = _engine_state.get("state") == "running"
    if running and _engine is not None:
        return _engine.translating()
    return bool(cfg.get("translate"))


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
           "hotwords": hot_surfaces,
           # 翻訳のみ表示（style.displayMode="en"）のフォールバック判定用。
           # 翻訳が実際に動いていなければ overlay 側が併記（日本語）表示に戻る
           "translate": _translating(cfg),
           # 音イベント演出のルール。マスタースイッチOFFでも配る
           # （設定UIの［テスト発火］はエンジン停止中でも効かせるため）
           "sound_rules": cfg.get("sound_fx_rules", {})}
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
    _fonts_cache = platform_compat.list_system_fonts()
    return _fonts_cache


# ---------------- SSE ----------------

def broadcast(event: dict):
    data = json.dumps(event, ensure_ascii=False)
    with _clients_lock:
        for q in list(_clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                # 切断寸前の遅いクライアントへ古い字幕を溜め続けず、
                # 半分まで整理して最新状態へ追いつけるようにする。
                while q.qsize() > SSE_QUEUE_RECOVER_ITEMS:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass


def _init_event():
    cfg = load_config()
    ev = {"type": "init"}
    ev.update(resolve_style(cfg))
    with _engine_state_lock:
        ev["state"] = dict(_engine_state)
    # 新しく開いたGUI窓がlocalStorageや次の変更イベントに依存せず、
    # 現在のテーマへ即座に揃えられるよう初期イベントにも含める。
    ev["theme"] = cfg.get("theme", "light")
    ev["ui_lang"] = cfg.get("ui_lang", "ja")
    ev["ui_scale"] = resolve_ui_scale(cfg)
    return ev


# ---------------- 音イベント演出（soundfx） ----------------

# ユーザー画像の上限。リクエスト全体の上限(4MB)内にbase64(+33%)で収める
SOUNDFX_IMAGE_MAX_BYTES = int(2.5 * 1024 * 1024)
_SOUNDFX_IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg",
                      ".jpeg": "image/jpeg", ".gif": "image/gif",
                      ".webp": "image/webp"}


def _soundfx_dir():
    d = wordstore.data_path("soundfx")
    os.makedirs(d, exist_ok=True)
    return d


def _soundfx_image_name(raw):
    """アップロード/配信で使う画像名の検証。パス区切りや隠しファイルを拒否し、
    安全な basename だけを返す（不正は None）"""
    name = str(raw or "").strip()
    if (not name or name != os.path.basename(name)
            or name.startswith(".") or "/" in name or "\\" in name):
        return None
    if os.path.splitext(name)[1].lower() not in _SOUNDFX_IMAGE_EXT:
        return None
    return name


def _sanitize_sound_rules(raw):
    """sound_fx_rules の保存前検証。未知グループ・不正型を落とし数値を丸める"""
    import soundfx
    if not isinstance(raw, dict):
        return {}
    out = {}
    for group, rule in raw.items():
        if group not in soundfx.GROUPS or not isinstance(rule, dict):
            continue
        try:
            clean = _clean_sound_rule(rule)
        except (TypeError, ValueError, AttributeError):
            continue                 # 型が壊れたルールは黙って捨てる
        out[group] = clean
    return out


def _clean_pos(pos):
    pos = pos or {}
    return {"x": min(1.0, max(0.0, float(pos.get("x", 0.5)))),
            "y": min(1.0, max(0.0, float(pos.get("y", 0.3))))}


def _clean_size(v, default=0.2):
    return min(1.0, max(0.02, float(v if v is not None else default)))


def _clean_sound_rule(rule):
    # variants: 画像ごとの配置（image・pos・size のセット）。ランダム表示は
    # バリアント単位で選ぶので、画像それぞれに別の位置・大きさを持てる
    variants = []
    for v in (rule.get("variants") or [])[:10]:
        if not isinstance(v, dict):
            continue
        name = _soundfx_image_name(v.get("image"))
        if name is None:
            continue
        variants.append({"image": name, "pos": _clean_pos(v.get("pos")),
                         "size": _clean_size(v.get("size")),
                         "rot": min(180.0, max(-180.0,
                                               float(v.get("rot", 0))))})
    if not variants:
        # 旧形式（images配列＋共通pos/size）からの引き継ぎ
        shared_pos = _clean_pos(rule.get("pos"))
        shared_size = _clean_size(rule.get("size"))
        variants = [{"image": n, "pos": dict(shared_pos), "size": shared_size,
                     "rot": 0.0}
                    for n in map(_soundfx_image_name, rule.get("images") or [])
                    if n][:10]
    return {
        "on": bool(rule.get("on")),
        "variants": variants,
        "particle": str(rule.get("particle") or "none")[:20],
        # pos/size はパーティクルのみ（バリアント無し）のときの表示位置
        "pos": _clean_pos(rule.get("pos")),
        "size": _clean_size(rule.get("size")),
        "jitter": min(0.5, max(0.0, float(rule.get("jitter", 0.0)))),
        "enter": str(rule.get("enter") or "pop")[:20],
        "anim": str(rule.get("anim") or "none")[:20],
        "duration": min(10000, max(200, int(rule.get("duration", 1500)))),
        "scale_by_score": bool(rule.get("scale_by_score", True)),
    }


# ---------------- ボイスコマンド ----------------

def _try_voice_command(text, spk=""):
    """確定テキストがボイスコマンドなら実行して True（字幕には出さない）。

    コラボ中は自分の声だけ受け付ける（相手の声からは発動しない）。
    """
    cfg = load_config()
    if not (cfg.get("vc_enabled") and cfg.get("vc_wake")):
        return False
    if cfg.get("collab"):
        self_name = (cfg.get("self_name") or "自分").strip() or "自分"
        if spk not in ("", self_name):
            return False
    import voicecmd
    boxes = _read_json(_boxes_path(), {"boxes": []})["boxes"]
    presets = _read_json(_presets_path(), {"presets": []})["presets"]
    scenes = _read_json(_scenes_path(), {"scenes": []})["scenes"]
    commands = _read_json(_vc_commands_path(), {"commands": []})["commands"]
    cmd = voicecmd.parse(text, cfg.get("vc_wake"), boxes, presets, scenes,
                         commands)
    if cmd is None:
        return False
    suffix = ""
    if cmd["action"] == "custom":
        # 登録済みの動作を組み込みのアクション形へ解決する
        act = (cmd["command"].get("action") or {})
        t = act.get("type")
        if t in ("scene", "box", "preset"):
            items = {"scene": scenes, "box": boxes, "preset": presets}[t]
            it = next((x for x in items if x.get("id") == act.get("id")), None)
            if it is None:
                broadcast({"type": "vc", "ok": False,
                           "message": "コマンドの切替先が見つかりません"
                                      "（削除された可能性があります）"})
                return True
            cmd = {"action": t, "id": it["id"], "name": it.get("name")}
        elif t == "random":
            cmd = {"action": act.get("target", "box") + "_random"}
        elif t == "translate_lang":
            labels = {code: label for code, label, _k in voicecmd._TRANS_LANGS}
            cmd = {"action": "translate_lang", "lang": act.get("lang"),
                   "label": labels.get(act.get("lang"), act.get("lang"))}
        elif t in ("translate_on", "translate_off"):
            cmd = {"action": t}
        elif t == "clear":
            broadcast({"type": "clear"})
            broadcast({"type": "vc", "ok": True,
                       "message": "字幕をクリアしました"})
            return True
        else:
            cmd = {"action": "unknown"}
    if cmd["action"] == "scene_random":
        # 今の組み合わせと違うシーンからおまかせで選ぶ
        cur = (cfg.get("preset"), cfg.get("box"))
        candidates = [s for s in scenes
                      if (s.get("preset"), s.get("box")) != cur]
        if candidates:
            s = random.choice(candidates)
            cmd = {"action": "scene", "id": s.get("id"), "name": s.get("name")}
            suffix = "（おまかせ）"
        else:
            cmd = {"action": "unknown"}
    if cmd["action"] == "box_random":
        # 現在と違うレイアウトからおまかせで選ぶ
        candidates = [b for b in boxes if b.get("id") != cfg.get("box")]
        if candidates:
            b = random.choice(candidates)
            cmd = {"action": "box", "id": b.get("id"), "name": b.get("name")}
            suffix = "（おまかせ）"
        else:
            cmd = {"action": "unknown"}
    elif cmd["action"] == "preset_random":
        candidates = [p for p in presets if p.get("id") != cfg.get("preset")]
        if candidates:
            p = random.choice(candidates)
            cmd = {"action": "preset", "id": p.get("id"), "name": p.get("name")}
            suffix = "（おまかせ）"
        else:
            cmd = {"action": "unknown"}
    if cmd["action"] == "scene":
        s = next((x for x in scenes if x.get("id") == cmd["id"]), None)
        valid = (s is not None
                 and any(p.get("id") == s.get("preset") for p in presets)
                 and any(b.get("id") == s.get("box") for b in boxes))
        if not valid:
            broadcast({"type": "vc", "ok": False,
                       "message": f"シーン「{cmd['name']}」の中身"
                                  "（デザインかレイアウト）が見つかりません"})
            return True
        with _config_lock:
            cfg = load_config()
            cfg["preset"] = s["preset"]
            cfg["box"] = s["box"]
            save_config(cfg)
        ev = {"type": "style"}
        ev.update(resolve_style(load_config()))
        broadcast(ev)
        broadcast({"type": "vc", "ok": True,
                   "message": f"シーン「{cmd['name']}」に切り替えました{suffix}"})
    elif cmd["action"] in ("box", "preset"):
        key = "box" if cmd["action"] == "box" else "preset"
        label = "レイアウト" if key == "box" else "字幕デザイン"
        with _config_lock:
            cfg = load_config()
            cfg[key] = cmd["id"]
            save_config(cfg)
        ev = {"type": "style"}
        ev.update(resolve_style(load_config()))
        broadcast(ev)
        broadcast({"type": "vc", "ok": True,
                   "message": f"{label}を「{cmd['name']}」に切り替えました{suffix}"})
    elif cmd["action"] in ("translate_on", "translate_off", "translate_lang"):
        with _config_lock:
            cfg = load_config()
            prev = (cfg.get("translate"), cfg.get("translate_lang"))
            if cmd["action"] == "translate_off":
                cfg["translate"] = False
                msg = "翻訳をオフにしました"
            else:
                cfg["translate"] = True
                if cmd["action"] == "translate_lang":
                    cfg["translate_lang"] = cmd["lang"]
                    msg = f"翻訳を{cmd['label']}に切り替えました"
                else:
                    msg = "翻訳をオンにしました"
            changed = prev != (cfg.get("translate"), cfg.get("translate_lang"))
            if changed:
                save_config(cfg)
        if not changed:
            broadcast({"type": "vc", "ok": True,
                       "message": "翻訳はすでにその設定です"})
            return True
        ev = {"type": "style"}
        ev.update(resolve_style(load_config()))
        broadcast(ev)
        if _engine is not None and _engine.running:
            msg += "（反映のため再起動しています…）"
            _restart_engine_async()
        broadcast({"type": "vc", "ok": True, "message": msg})
    else:
        broadcast({"type": "vc", "ok": False,
                   "message": "コマンドを聞き取れませんでした"})
    return True


def _restart_engine_async():
    """設定反映のための停止→開始を裏スレッドで行う（ボイスコマンド用）。

    確定コールバック（エンジン自身のスレッド）から呼ばれるため、ここでは
    joinせず、別スレッドで停止完了を待ってから開始し直す。
    """
    def work():
        import time
        eng = get_engine()
        eng.stop(timeout=30)
        deadline = time.time() + 30
        while time.time() < deadline:
            if eng.start(load_config()):
                return
            time.sleep(0.3)

    threading.Thread(target=work, daemon=True, name="vc-restart").start()


def _engine_on_final(text, fid, spk=""):
    if _try_voice_command(text, spk):
        # コマンド発話は字幕に出さず、発話中に見えていた薄文字だけ消す
        _engine_on_partial("", spk)
        return
    broadcast({"type": "final", "text": text, "id": fid, "speaker": spk})
    vrcchat.on_final(text, fid, spk)


def _engine_on_partial(text, spk=""):
    broadcast({"type": "partial", "text": text, "speaker": spk})
    vrcchat.on_partial(text, spk)   # VRChatのタイピング中表示


def _engine_on_translation(fid, text, fallback=False):
    # fallback=True は「訳を出せなかったので原文を渡している」印。表示側は
    # 翻訳のみ表示でもこの行だけ原文へ切り替える（空にすると字幕が消える）。
    broadcast({"type": "translation", "id": fid, "text": text,
               "fallback": bool(fallback)})
    if not fallback:      # VRChatへ原文を訳文として送らない
        vrcchat.on_translation(fid, text)


def _engine_on_warn(kind, message="", active=True):
    """配信を止めない異常（英訳の連続失敗・音声の途絶）をGUIへ流す。

    停止せずに字幕だけが出なくなる状態は配信者が気づけない。状態表示とは
    別枠の警告として送り、解除時は active=False で消す。
    """
    broadcast({"type": "warn", "kind": kind, "message": message,
               "active": bool(active)})


# ---------------- エンジン連携 ----------------

def get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from engine import CaptionEngine
            _engine = CaptionEngine(
                on_partial=_engine_on_partial,
                on_final=_engine_on_final,
                on_level=lambda v, spk="": broadcast(
                    {"type": "level", "value": round(v, 3), "speaker": spk}),
                on_state=_on_state,
                on_translation=_engine_on_translation,
                on_warn=_engine_on_warn,
                on_sound_event=lambda g, s, spk="": broadcast(
                    {"type": "sound", "group": g, "score": round(s, 2),
                     "speaker": spk}),
            )
        return _engine


def _on_state(state, detail=""):
    with _engine_state_lock:
        _engine_state.update({"state": state, "detail": detail})
    ev = {"type": "state", "state": state, "detail": detail}
    # 起動完了時に「翻訳が実際に動いているか」を通知（翻訳のみ表示の判定を
    # cfg 由来の値から実態へ更新する。再起動で翻訳ONが反映された瞬間に切り替わる）
    if state == "running" and _engine is not None:
        ev["translate"] = _engine.translating()
    broadcast(ev)


# ---------------- HTTPハンドラ ----------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    # --- helpers ---
    def _allow_request(self, path, state_changing=False):
        """ローカル利用を保ったまま、外部OriginとDNSリバインディングを拒否する。

        - WebView/OBS: http://127.0.0.1:<port> と http://localhost:<port> を許可
        - curl/PowerShell等: Originなしの直接アクセスを許可
        - 外部Webページ: OriginまたはSec-Fetch-Siteで拒否
        - DNSリバインディング: Hostがloopback名でなければ拒否
        """
        port = self.server.server_address[1]
        if not _is_loopback_host(self.headers.get("Host", ""), port):
            self._send_body(421, b"misdirected request",
                            "text/plain; charset=utf-8")
            return False

        origin = self.headers.get("Origin")
        if origin and not _is_loopback_origin(origin, port):
            self._send_body(403, b"forbidden origin",
                            "text/plain; charset=utf-8")
            return False

        protected = state_changing or path == "/events" or path.startswith("/api/")
        fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
        if protected and not origin and fetch_site == "cross-site":
            self._send_body(403, b"cross-site request blocked",
                            "text/plain; charset=utf-8")
            return False
        return True

    def _send_body(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Content-Type-Options", "nosniff")
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

    def _soundfx_image_file(self, name):
        """演出画像の配信。_send_body は全応答 no-store（GUI更新の都合）だが、
        画像は発火のたびに <img> が取り直すため、それだと毎回フルDLになり
        表示が遅れる。更新時刻で再検証させ、変わっていなければ 304 で済ませる"""
        path = os.path.join(_soundfx_dir(), name)
        try:
            mtime = os.path.getmtime(path)
            etag = f'"{int(mtime)}-{os.path.getsize(path)}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self._send_body(404, b"not found", "text/plain")
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         _SOUNDFX_IMAGE_EXT[os.path.splitext(name)[1].lower()])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("ETag", etag)
        # 同名アップロードで差し替わるため max-age は短く、以後は ETag 再検証
        self.send_header("Cache-Control", "private, max-age=60")
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if n < 0 or n > MAX_REQUEST_BODY_BYTES:
            raise _RequestBodyTooLarge
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
        if not self._allow_request(path):
            return
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if path in ("/", "/overlay"):
            self._file(os.path.join(BASE, "overlay.html"))
        elif path == "/CREDITS.md":
            # サードパーティのクレジット（帰属表示にUIから到達できるように公開）
            self._file(os.path.join(BASE, "CREDITS.md"),
                       "text/plain; charset=utf-8")
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
        elif path.startswith("/soundfx/"):
            # 音イベント演出のユーザー画像（overlay が <img> で読む）。
            # 日本語ファイル名は encodeURIComponent で届くため復号してから検証する
            name = _soundfx_image_name(unquote(path[len("/soundfx/"):]))
            if name is None:
                self._send_body(404, b"not found", "text/plain")
                return
            self._soundfx_image_file(name)
        elif path == "/api/soundfx/images":
            d = _soundfx_dir()
            names = sorted(n for n in os.listdir(d)
                           if _soundfx_image_name(n)
                           and os.path.isfile(os.path.join(d, n)))
            self._json({"images": names})
        elif path == "/api/soundfx/groups":
            import soundfx
            self._json({"groups": list(soundfx.GROUPS)})
        elif path == "/api/config":
            cfg = load_config()
            cfg["version"] = APP_VERSION   # 表示用（保存はされない: POSTでは既知キーのみ更新）
            # OS能力（表示用）。UIはこれでコラボ欄をグレーアウトする
            cfg["collab_supported"] = platform_compat.collab_supported()
            # 同じくVRChat欄の表示可否（macはクライアントが無いので欄ごと隠す）
            cfg["vrchat_supported"] = platform_compat.vrchat_supported()
            # 表示・適用用。resolved=実際に効いている倍率、auto=「自動」を選んだ場合の倍率
            cfg["ui_scale_resolved"] = resolve_ui_scale(cfg)
            cfg["ui_scale_auto"] = platform_compat.ui_scale()
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
        elif path == "/api/scenes":
            self._json(_read_json(_scenes_path(), {"scenes": []}))
        elif path == "/api/vc-commands":
            self._json(_read_json(_vc_commands_path(), {"commands": []}))
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
            if not platform_compat.collab_supported():
                self._json({"supported": False, "apps": []})
                return
            try:
                import proc_loopback
                self._json({"supported": proc_loopback.is_supported(),
                            "apps": proc_loopback.list_audio_apps()})
            except Exception as e:
                self._json({"supported": False, "apps": [], "error": str(e)})
        elif path == "/api/status":
            with _engine_state_lock:
                state = dict(_engine_state)
            self._json(state)
        elif path == "/api/perf":
            # リモート切り分け用: デコード回数・平均所要時間（今セッション累計）
            p = getattr(_engine, "perf", None) if _engine else None
            with _engine_state_lock:
                state = dict(_engine_state)
            if not p:
                self._json({"state": state, "perf": None})
                return
            import time as _t
            self._json({
                "state": state,
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
        if not self._allow_request(path, state_changing=True):
            return
        try:
            body = self._body_json()
        except _RequestBodyTooLarge:
            self._json({"ok": False, "error": "request too large"}, 413)
            return
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
            if "ui_scale" in body:
                body["ui_scale"] = normalize_ui_scale(body.get("ui_scale"))
            if "window_geometry" in body:
                body["window_geometry"] = normalize_window_geometry(
                    body.get("window_geometry"))
            if ("collab_source" in body
                    and body.get("collab_source") not in ("process", "device")):
                body["collab_source"] = "process"   # 未知値は推奨方式へ
            if body.get("collab") and not platform_compat.collab_supported():
                body["collab"] = False   # このOSでは未対応（UI側もグレーアウト）
            if "vc_wake" in body:
                # 文字列リストへ正規化（最大10件・各30文字。不正型は無視）
                raw = body.get("vc_wake")
                if not isinstance(raw, list):
                    raw = []
                body["vc_wake"] = [str(w).strip()[:30] for w in raw[:10]
                                   if str(w).strip()]
            if "vc_enabled" in body:
                body["vc_enabled"] = bool(body.get("vc_enabled"))
            if "vrchat" in body:
                body["vrchat"] = bool(body.get("vrchat"))
            if "vrchat_source" in body and body["vrchat_source"] not in ("ja", "tr"):
                body["vrchat_source"] = "ja"
            if "vrchat_port" in body:
                try:
                    body["vrchat_port"] = min(65535, max(1024, int(body["vrchat_port"])))
                except (TypeError, ValueError):
                    body["vrchat_port"] = 9000
            if "sound_fx" in body:
                body["sound_fx"] = bool(body.get("sound_fx"))
            if "sound_fx_rules" in body:
                body["sound_fx_rules"] = _sanitize_sound_rules(
                    body.get("sound_fx_rules"))
            # ThreadingHTTPServer上で複数の設定窓が同時保存しても、後着の
            # read-modify-write が先着変更を巻き戻さないよう一連を排他する。
            with _config_lock:
                cfg = load_config()
                cfg.update({k: v for k, v in body.items() if k in DEFAULT_CONFIG})
                save_config(cfg)
            vrcchat.configure(cfg)   # VRChat転送は再起動なしで即反映
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
            if body.get("final_only"):
                # 「確定した字幕だけ表示」に切り替えた瞬間、表示中の薄文字を消す
                # （次の発話が確定するまで残ってしまうのを防ぐ）
                broadcast({"type": "clear_partial"})
            # 拡大率は解決済みの実効値を配る（"auto" は自動判定の数値へ）。
            # 開いている窓の中身は即座に拡縮するが、ネイティブ窓自体の
            # ピクセルサイズは起動時に決まるため次回起動から反映される。
            if "ui_scale" in body:
                broadcast({"type": "ui_scale", "ui_scale": resolve_ui_scale(cfg)})
            self._json({"ok": True, "config": cfg})
        elif path == "/api/profiles":
            self._post_profiles(body)
        elif path == "/api/words/import":
            self._post_words_import(body)
        elif path == "/api/words/export":
            self._post_words_export(body)
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
        elif path == "/api/soundfx/test":
            # 設定UIの［テスト発火］。本物と同じ形の SSE を流すので、
            # 実際に笑わなくても OBS 上の overlay で演出を調整できる
            import soundfx
            group = body.get("group")
            if group not in soundfx.GROUPS:
                self._json({"ok": False, "error": "unknown group"}, 400)
                return
            try:
                score = float(body.get("score", 1.0))
            except (TypeError, ValueError):
                score = 1.0
            broadcast({"type": "sound", "group": group,
                       "score": round(min(3.0, max(0.0, score)), 2),
                       "speaker": "", "test": True})
            self._json({"ok": True})
        elif path == "/api/soundfx/image":
            # 画像アップロード（base64）。data/soundfx/ へ保存して名前を返す
            name = _soundfx_image_name(body.get("name"))
            if name is None:
                self._json({"ok": False,
                            "error": "png/jpg/gif/webp のファイル名を指定してください"}, 400)
                return
            import base64
            try:
                data = base64.b64decode(body.get("data") or "", validate=True)
            except (ValueError, TypeError):
                self._json({"ok": False, "error": "bad data"}, 400)
                return
            if not data or len(data) > SOUNDFX_IMAGE_MAX_BYTES:
                self._json({"ok": False,
                            "error": "画像は2.5MB以下にしてください"}, 400)
                return
            with open(os.path.join(_soundfx_dir(), name), "wb") as f:
                f.write(data)
            self._json({"ok": True, "name": name})
        elif path == "/api/soundfx/image-delete":
            name = _soundfx_image_name(body.get("name"))
            if name is None:
                self._json({"ok": False, "error": "bad name"}, 400)
                return
            try:
                os.remove(os.path.join(_soundfx_dir(), name))
            except OSError:
                pass                     # 既に無ければそれで良い
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
        elif path == "/api/vc-commands":
            commands = body.get("commands", [])
            if not (isinstance(commands, list) and len(commands) <= 50
                    and all(_valid_vc_command(c) for c in commands)):
                self._json({"ok": False, "error": "invalid commands"}, 400)
                return
            for c in commands:   # 言い回しは空白を除去した形へ正規化して保存
                c["phrases"] = [str(p).strip() for p in c["phrases"]
                                if str(p).strip()]
            _write_json(_vc_commands_path(), {"commands": commands})
            self._json({"ok": True})
        elif path == "/api/scenes":
            scenes = body.get("scenes", [])
            ok = (isinstance(scenes, list)
                  and all(isinstance(s, dict) and s.get("id") and s.get("name")
                          and isinstance(s.get("preset"), str)
                          and isinstance(s.get("box"), str) for s in scenes))
            if not ok:
                self._json({"ok": False, "error": "invalid scenes"}, 400)
                return
            _write_json(_scenes_path(), {"scenes": scenes})
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
                platform_compat.open_folder(d)   # OSのファイラで開く
                self._json({"ok": True})
            except OSError as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/logs/open":
            # 文字起こしログは engine.py と同じく BASE/logs に保存される。
            # まだ配信していない場合も、入口としてフォルダを作ってから開く。
            d = os.path.join(DATA_BASE, "logs")
            os.makedirs(d, exist_ok=True)
            try:
                platform_compat.open_folder(d)   # OSのファイラで開く
                self._json({"ok": True, "path": d})
            except OSError as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/clear":
            broadcast({"type": "clear"})
            self._json({"ok": True})
        else:
            self._send_body(404, b"not found", "text/plain")

    def _post_words_import(self, body):
        """単語の一括取り込み（CSV / JSON）。
        {profile, content_b64, mode: preview|add|overwrite, glossary: bool}

        preview は集計だけ返す（取り込む前に件数・重複・弾いた行を見せる）。
        本文は base64 の生バイトで受ける（Excel の Shift_JIS をサーバ側で判別するため）。
        取り込みは保存済みデータへの合成なので、UI 側は未保存の編集を先に保存してから呼ぶ。
        """
        import wordimport
        p = self._profile_arg(body.get("profile"))
        if p is None:
            return
        raw = body.get("content_b64")
        try:
            data = (base64.b64decode(raw, validate=True)
                    if isinstance(raw, str) else b"")
        except ValueError:
            data = b""
        if not data:
            self._json({"ok": False, "error": "ファイルが空です"}, 400)
            return
        entries, skipped = wordimport.parse_words(wordimport.decode_bytes(data))
        hot = wordstore.load_hotwords(p)
        gloss = wordstore.load_glossary(p)
        stats, uniq = wordimport.plan_import(entries, hot, gloss)
        mode = body.get("mode", "preview")
        if mode == "preview":
            self._json({"ok": True, "stats": stats,
                        "skipped": skipped[:50], "skipped_total": len(skipped),
                        "sample": uniq[:20]})
            return
        if mode not in ("add", "overwrite"):
            self._json({"ok": False, "error": "unknown mode"}, 400)
            return
        if not uniq:
            self._json({"ok": False, "error": "取り込める単語がありません"}, 400)
            return
        new_hot, new_gl, counts = wordimport.apply_import(
            uniq, hot, gloss, mode, with_glossary=bool(body.get("glossary", True)))
        wordstore.save_hotwords(new_hot, p)
        if counts["glossary"]:
            wordstore.save_glossary(new_gl, p)
        ev = {"type": "style"}
        ev.update(resolve_style(load_config()))
        broadcast(ev)
        self._json({"ok": True, **counts, "stats": stats})

    def _post_words_export(self, body):
        """認識辞書＋英訳辞書を取り込みと同じ列構成の CSV で data/export/ へ書き出す
        （Excel で開けるよう BOM 付き UTF-8・CRLF）"""
        import wordimport
        p = self._profile_arg(body.get("profile"))
        if p is None:
            return
        text = wordimport.to_csv(wordstore.load_hotwords(p),
                                 wordstore.load_glossary(p))
        d = wordstore.data_path(EXPORT_DIR_NAME)
        os.makedirs(d, exist_ok=True)
        from datetime import datetime
        fname = (f"words_{p or 'common'}_"
                 + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
        with open(os.path.join(d, fname), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        self._json({"ok": True, "file": fname, "path": os.path.join(d, fname)})

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
        q: "queue.Queue[str]" = queue.Queue(maxsize=SSE_QUEUE_MAX_ITEMS)
        with _clients_lock:
            too_many = len(_clients) >= MAX_SSE_CLIENTS
            if not too_many:
                _clients.append(q)
        if too_many:
            self._send_body(503, b"too many event clients",
                            "text/plain; charset=utf-8")
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()

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
    vrcchat.configure(load_config())   # VRChat転送の設定スナップショット
    server = _QuietHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
