"""以前の Mojicast（Zip 版の別フォルダ）から設定・単語・モデルを引き継ぐ。

インストーラ版は %LOCALAPPDATA%\\Programs\\Mojicast に入るため、Zip 版で使っていた
フォルダの data\\（設定・単語・字幕デザイン）と models\\（AIモデル）が見えない。
初回起動時にコックピットから旧フォルダを選んでもらい、ここでコピーする。

- data\\ は丸ごと差し替える（初回起動で作られた既定データは退避し、失敗したら戻す）
- models\\ は数GBあるので裏スレッドでコピーし、進捗をバイト数で返す。
  同じファイルが既にあれば飛ばす（途中で止まっても再実行で続きから埋まる）
- 旧フォルダは読むだけで変更しない
"""
import os
import shutil
import threading
import time

import wordstore

# 旧フォルダとして認める印。data\ の中身のどれか（旧々版はフォルダ直下に置いていた）
_DATA_MARKERS = ("config.json", "presets.json", "hotwords.txt")

# 候補を探す場所（Zip を展開しがちな所）。深さ2まで（例: Desktop\Mojicast\Mojicast.exe、
# Downloads\Mojicast-v0.9.8-win-x64\Mojicast\Mojicast.exe）
_SEARCH_DEPTH = 2


def _data_dir_of(folder):
    """旧フォルダの設定の場所 → (パス, 旧々版の直下配置か) / 無ければ (None, False)"""
    d = os.path.join(folder, "data")
    if any(os.path.isfile(os.path.join(d, m)) for m in _DATA_MARKERS):
        return d, False
    if any(os.path.isfile(os.path.join(folder, m)) for m in _DATA_MARKERS):
        return folder, True
    return None, False


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _same_place(a, b):
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False


def inspect(folder, current_base):
    """選ばれたフォルダが引き継ぎ元として使えるかを調べる。

    戻り値: {ok, error?, path, models_mb}
    folder には Mojicast.exe のあるフォルダ（data\\ の親）か、data\\ そのものを受け付ける。
    """
    if not folder or not os.path.isdir(folder):
        return {"ok": False, "error": "not_found"}
    folder = os.path.abspath(folder)
    if os.path.basename(folder).lower() == "data" and _data_dir_of(
            os.path.dirname(folder))[0]:
        folder = os.path.dirname(folder)      # data\ を直接選んだ場合は親へ
    if _same_place(folder, current_base):
        return {"ok": False, "error": "same_folder"}
    data_dir, _legacy = _data_dir_of(folder)
    if data_dir is None:
        return {"ok": False, "error": "no_data"}
    models = os.path.join(folder, "models")
    mb = _dir_size(models) / 1048576 if os.path.isdir(models) else 0
    return {"ok": True, "path": folder, "models_mb": round(mb)}


def find_candidates(current_base, roots=None):
    """デスクトップ・ダウンロード・ドキュメントから旧 Mojicast フォルダを探す（新しい順）"""
    if roots is None:
        home = os.path.expanduser("~")
        roots = [os.path.join(home, n) for n in ("Desktop", "Downloads", "Documents")]
        od = os.environ.get("OneDrive")
        if od:
            roots += [os.path.join(od, n) for n in ("Desktop", "Documents",
                                                    "デスクトップ", "ドキュメント")]
    found = {}

    def visit(path, depth):
        if os.path.isfile(os.path.join(path, "Mojicast.exe")) and _data_dir_of(path)[0]:
            if not _same_place(path, current_base):
                key = os.path.normcase(os.path.realpath(path))
                found.setdefault(key, path)
            return
        if depth >= _SEARCH_DEPTH:
            return
        try:
            entries = list(os.scandir(path))
        except OSError:
            return
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                    visit(e.path, depth + 1)
            except OSError:
                pass

    for r in roots:
        if os.path.isdir(r):
            visit(r, 0)

    def modified(p):
        d, _ = _data_dir_of(p)
        try:
            return max(os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d))
        except (OSError, ValueError):
            return 0
    return sorted(found.values(), key=modified, reverse=True)[:5]


class Job:
    """引き継ぎ1回ぶん（裏スレッド）。状態は snapshot() で取る"""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {"running": False, "done": False, "error": "",
                      "phase": "", "copied_mb": 0, "total_mb": 0}

    def snapshot(self):
        with self._lock:
            return dict(self.state)

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def start(self, src_folder, with_models, current_data, current_models, on_done):
        with self._lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "done": False, "error": "",
                          "phase": "data", "copied_mb": 0, "total_mb": 0}
        threading.Thread(target=self._run, daemon=True,
                         args=(src_folder, with_models, current_data,
                               current_models, on_done)).start()
        return True

    def _run(self, src_folder, with_models, current_data, current_models, on_done):
        try:
            copy_data(src_folder, current_data)
            if with_models:
                src_models = os.path.join(src_folder, "models")
                if os.path.isdir(src_models):
                    self._set(phase="models")
                    self._copy_models(src_models, current_models)
            on_done()
            self._set(running=False, done=True, phase="done")
        except Exception as e:                 # 失敗は画面に出す（黙らない）
            self._set(running=False, done=False, error=str(e) or type(e).__name__)

    def _copy_models(self, src, dst):
        """models\\ を合流コピー（同じ大きさのファイルが既にあれば飛ばす）"""
        plan, total = [], 0
        for root, _dirs, files in os.walk(src):
            for f in files:
                s = os.path.join(root, f)
                d = os.path.join(dst, os.path.relpath(s, src))
                try:
                    size = os.path.getsize(s)
                except OSError:
                    continue
                if os.path.isfile(d) and os.path.getsize(d) == size:
                    continue
                plan.append((s, d, size))
                total += size
        self._set(total_mb=round(total / 1048576))
        copied = 0
        for s, d, size in plan:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            tmp = d + ".migrating"
            with open(s, "rb") as fi, open(tmp, "wb") as fo:
                while True:
                    buf = fi.read(8 * 1048576)
                    if not buf:
                        break
                    fo.write(buf)
                    copied += len(buf)
                    self._set(copied_mb=round(copied / 1048576))
            os.replace(tmp, d)       # 途中で止まっても壊れたモデルを残さない


def _clear_dir(path):
    """フォルダの中身だけを消す（フォルダ自体は残す）"""
    for e in os.scandir(path):
        if e.is_dir(follow_symlinks=False):
            shutil.rmtree(e.path)
        else:
            os.remove(e.path)


def copy_data(src_folder, current_data):
    """旧フォルダの設定一式で data\\ の中身を差し替える。失敗したら元に戻す。

    data\\ フォルダ自体の名前変更はしない。Windows では中のファイルを一瞬でも
    誰か（ウイルス対策・検索インデックス）が開いていると拒否されるため
    （WinError 5）。いまの中身を写しで退避し、中身だけ入れ替える。
    """
    src, legacy = _data_dir_of(src_folder)
    if src is None:
        raise FileNotFoundError("引き継ぎ元に設定（data）が見つかりません")
    os.makedirs(current_data, exist_ok=True)
    backup = current_data + ".before-migrate-" + time.strftime("%Y%m%d%H%M%S")
    shutil.copytree(current_data, backup)
    try:
        _clear_dir(current_data)
        if legacy:
            # 旧々版（フォルダ直下にデータ）: 既知のデータファイルだけ拾う
            for name in wordstore.DATA_FILES:
                p = os.path.join(src, name)
                if os.path.isfile(p):
                    shutil.copy2(p, os.path.join(current_data, name))
        else:
            # 生成物（_hotwords_gen.txt）は開始時に作り直されるので持ってこない
            shutil.copytree(src, current_data, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("_hotwords_gen.txt"))
    except Exception:
        try:
            _clear_dir(current_data)
        except OSError:
            pass
        shutil.copytree(backup, current_data, dirs_exist_ok=True)
        shutil.rmtree(backup, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)   # 退避したのは初回起動の既定データだけ
