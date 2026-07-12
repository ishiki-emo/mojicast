"""
単語系データの保管とプロファイル合成

ユーザーデータは data/ 配下に集約する（バックアップ = data フォルダのコピー）:
    data/
      config.json                 全設定
      hotwords.txt  effects.json  banned.txt  glossary.txt   ← 「共通」の単語セット
      presets.json  boxes.json                               ← スタイル定義
      profiles/<名前>/
        hotwords.txt  effects.json  banned.txt  glossary.txt ← プロファイルの単語セット

プロファイルは「共通」との合成で使う（上書きでなく加算・同じ単語はプロファイル優先）。
名前・チャンネル名は共通に置けば全プロファイルで有効になる。

旧レイアウト（BASE 直下にデータファイル）からは ensure_data() が自動移行する。
"""
import os
import json
import shutil

from apppaths import BASE

DATA = os.path.join(BASE, "data")
PROFILES_DIR = os.path.join(DATA, "profiles")

# プロファイル毎に持てる単語系ファイル
WORD_FILES = ("hotwords.txt", "effects.json", "banned.txt", "glossary.txt")
# data/ 直下に置く全ユーザーデータ（旧: BASE 直下 → 自動移行の対象）
DATA_FILES = ("config.json", "presets.json", "boxes.json") + WORD_FILES

_ready = False


def ensure_data():
    """data/ を用意する: フォルダ作成 → 旧ルート配置からの移行 → 既定データの複製。

    冪等（何度呼んでも安全）。サーバ起動・設定読み込みの前に必ず通す。
    """
    global _ready
    if _ready:
        return
    os.makedirs(PROFILES_DIR, exist_ok=True)

    # --- 旧レイアウトからの自動移行（既存ユーザー対策）---
    for name in DATA_FILES:
        old = os.path.join(BASE, name)
        new = os.path.join(DATA, name)
        if os.path.exists(old) and not os.path.exists(new):
            try:
                shutil.move(old, new)
            except OSError:
                pass                    # 移行失敗時は defaults からの seed に任せる
    # 生成ファイルの残骸は消すだけ（次回開始時に data/ 側へ再生成される）
    try:
        os.remove(os.path.join(BASE, "_hotwords_gen.txt"))
    except OSError:
        pass

    # --- 無いファイルは defaults/ から複製（新規環境の初期データ）---
    src = os.path.join(BASE, "defaults")
    if os.path.isdir(src):
        for name in WORD_FILES + ("presets.json", "boxes.json"):
            dst = os.path.join(DATA, name)
            if not os.path.exists(dst):
                try:
                    shutil.copyfile(os.path.join(src, name), dst)
                except OSError:
                    pass
    _ready = True


def data_path(name: str) -> str:
    """data/ 直下のファイルパス（config.json / presets.json 等）"""
    return os.path.join(DATA, name)


# ---------------- プロファイル ----------------

_INVALID_CHARS = set('\\/:*?"<>|')


def valid_profile_name(name) -> bool:
    """フォルダ名として安全なプロファイル名か（日本語OK・記号と予約名を拒否）"""
    if not isinstance(name, str) or not name or name != name.strip():
        return False
    if len(name) > 40 or name in (".", "..") or name.endswith("."):
        return False
    return not any(c in _INVALID_CHARS or ord(c) < 32 for c in name)


def list_profiles():
    """プロファイル名の一覧（名前順）"""
    try:
        return sorted(d for d in os.listdir(PROFILES_DIR)
                      if os.path.isdir(os.path.join(PROFILES_DIR, d)))
    except OSError:
        return []


def profile_exists(name) -> bool:
    return (valid_profile_name(name)
            and os.path.isdir(os.path.join(PROFILES_DIR, name)))


def create_profile(name, copy_from=""):
    """プロファイルを作成する。copy_from に既存プロファイル名を渡すと
    その単語ファイル一式を複製したテンプレート開始になる（空 = ゼロから）。"""
    if not valid_profile_name(name):
        raise ValueError("使えない名前です（記号 \\ / : * ? \" < > | は不可）")
    d = os.path.join(PROFILES_DIR, name)
    os.makedirs(d, exist_ok=True)
    if copy_from and profile_exists(copy_from):
        src = os.path.join(PROFILES_DIR, copy_from)
        for f in WORD_FILES:
            p = os.path.join(src, f)
            if os.path.exists(p):
                try:
                    shutil.copyfile(p, os.path.join(d, f))
                except OSError:
                    pass


def delete_profile(name):
    if not profile_exists(name):
        return
    shutil.rmtree(os.path.join(PROFILES_DIR, name), ignore_errors=True)


def _scope_dir(profile: str) -> str:
    """編集スコープのフォルダ（profile 空 = 共通 = data/ 直下）"""
    if not profile:
        return DATA
    if not valid_profile_name(profile):
        raise ValueError("invalid profile name")
    return os.path.join(PROFILES_DIR, profile)


def _scope_path(profile: str, name: str) -> str:
    return os.path.join(_scope_dir(profile), name)


def _read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_scope(profile, name, write_fn):
    """スコープのフォルダを保証してからファイルを書く"""
    d = _scope_dir(profile)
    os.makedirs(d, exist_ok=True)
    write_fn(os.path.join(d, name))


# ---------------- ホットワード ----------------

def load_hotwords_raw(profile=""):
    """スコープ単体の hotwords.txt → [(表記, 読み, スコア)]"""
    from vocab import parse_vocab
    path = _scope_path(profile, "hotwords.txt")
    if not os.path.exists(path):
        return []
    return parse_vocab(path)


def load_hotwords(profile=""):
    """スコープ単体 → UI用 [{surface, reading, score}]"""
    return [{"surface": s, "reading": r, "score": sc or ""}
            for s, r, sc in load_hotwords_raw(profile)]


def save_hotwords(entries, profile=""):
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

    def write(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    _write_scope(profile, "hotwords.txt", write)


def merged_hotwords(profile=""):
    """共通＋プロファイルの合成 → [(表記, 読み, スコア)]（同じ表記はプロファイル優先）"""
    merged = {s: (s, r, sc) for s, r, sc in load_hotwords_raw("")}
    if profile and profile_exists(profile):
        for s, r, sc in load_hotwords_raw(profile):
            merged[s] = (s, r, sc)
    return list(merged.values())


# ---------------- エフェクト単語 ----------------

def load_effects(profile=""):
    """スコープ単体の effects.json → [dict]"""
    return _read_json(_scope_path(profile, "effects.json"),
                      {"effects": []}).get("effects", [])


def save_effects(effects, profile=""):
    def write(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"effects": effects}, f, ensure_ascii=False, indent=2)
    _write_scope(profile, "effects.json", write)


def merged_effects(profile=""):
    """共通＋プロファイルの合成（同じ単語はプロファイル優先）"""
    merged = {e.get("word"): e for e in load_effects("") if e.get("word")}
    if profile and profile_exists(profile):
        for e in load_effects(profile):
            if e.get("word"):
                merged[e["word"]] = e
    return list(merged.values())


# ---------------- 禁止ワード ----------------

def load_banned(profile=""):
    """スコープ単体の banned.txt → 語のリスト（#行・空行は除外）"""
    try:
        with open(_scope_path(profile, "banned.txt"), encoding="utf-8") as f:
            return [ln.strip() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return []


def save_banned(words, profile=""):
    lines = ["# 放送禁止ワードなどを1行に1語（#行はコメント）",
             "# ここの語は認識中・確定・ログ・英訳のすべてで伏せ字になります"]
    for w in words:
        w = (w or "").strip()
        if w:
            lines.append(w)

    def write(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    _write_scope(profile, "banned.txt", write)


def merged_banned(profile=""):
    """共通＋プロファイルの和集合（順序維持・重複除去）"""
    seen = {}
    for w in load_banned(""):
        seen.setdefault(w, None)
    if profile and profile_exists(profile):
        for w in load_banned(profile):
            seen.setdefault(w, None)
    return list(seen)


# ---------------- 英訳辞書 ----------------

def load_glossary(profile=""):
    """スコープ単体の glossary.txt → [{ja, en}]"""
    entries = []
    try:
        with open(_scope_path(profile, "glossary.txt"), encoding="utf-8") as f:
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


def save_glossary(entries, profile=""):
    lines = ["# 英訳辞書: 字幕の表記,英訳  （#行はコメント）",
             "# 例: 癒色えも,ISHIKI Emo  → 英訳時にこの語の訳が固定されます"]
    for e in entries:
        ja = (e.get("ja") or "").strip()
        en = (e.get("en") or "").strip()
        if ja and en:
            lines.append(f"{ja},{en}")

    def write(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    _write_scope(profile, "glossary.txt", write)


def merged_glossary(profile=""):
    """共通＋プロファイルの合成 → [(表記, 英訳)]（同じ表記はプロファイル優先）"""
    merged = {e["ja"]: e["en"] for e in load_glossary("")}
    if profile and profile_exists(profile):
        for e in load_glossary(profile):
            merged[e["ja"]] = e["en"]
    return list(merged.items())
