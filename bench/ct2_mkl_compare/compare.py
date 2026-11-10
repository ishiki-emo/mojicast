# -*- coding: utf-8 -*-
"""翻訳エンジン（CTranslate2）の比較ツール: 公式ビルドの MKL あり/なし ＋ 自前ビルド

SignPath Foundation の署名条件（同梱物がすべて OSI 承認ライセンス）を満たすため、
ctranslate2.dll に静的リンクされた Intel oneMKL と Intel OpenMP（libiomp5md.dll）を外した
自前ビルド（tools/signing/ct2_nomkl・MSVC＋vcomp140）に切り替える案を測る。
切り替えるかどうかはこの結果（Intel 2台＋AMD）を見て決める。

比べる組み合わせ（variant）:
  mkl            : 公式ビルド・MKL あり（CT2_USE_MKL=1）。Intel 機の現行の動き
  dnnl           : 公式ビルド・MKL なし（CT2_USE_MKL=0）。oneDNN ＋ Intel OpenMP。AMD 機の現行
  custom_passive : 自前ビルド（MKL なし・oneDNN ＋ MSVC OpenMP）＋ OMP_WAIT_POLICY=PASSIVE。
                   採用するならこの形（translate.py が vcomp を見つけると PASSIVE にする）
  custom         : 自前ビルドで OMP_WAIT_POLICY を指定しない（vcomp 既定の空回りあり）
mkl と dnnl の差が「MKL を外した影響」、dnnl と custom の差が「OpenMP の部品を替えた影響」、
custom と custom_passive の差が「空回りを止めた影響（CPU 負荷は減るが遅くなりうる）」。
AMD 等ではもともと MKL を使わないので mkl は測らない（強制すると極端に遅い）。

測るもの（本体の translate.py をそのまま呼ぶ・intra_threads=4 は本体と同じ）:
  - en_int8 : 英訳 FuguMT int8（通常）
  - en_fp32 : 英訳 FuguMT fp32（高精度モード）
  - zh      : 多言語訳 M2M-100 ja→zh（int8・greedy）
  - ko      : 多言語訳 M2M-100 ja→ko（int8・beam 4）
  1文ごとの時間、訳文が基準（先頭の variant）と一致するか、ロード時間、ピークメモリ、
  CPU 時間（先頭20文で、1文訳して1秒空けるまで。計算後の OpenMP の空回りはここに出る）。
  結果には、実際に読み込まれた OpenMP ランタイムの DLL、効いていた OMP_WAIT_POLICY、
  ctranslate2 の版と DLL の SHA256、自前ビルドの build-info.json（CMake オプション等）を残す。

各 variant を往復の順（例: mkl → dnnl → custom → custom → dnnl → mkl）で2回ずつ別プロセスで
走らせ、1文ごとに同じ variant の2回のうち速い方を採る（初回のファイル読み込みや順番の有利不利を
打ち消すため）。

スレッド数の比較: 続けて、基準・custom_passive・custom を intra_threads=1 / 2 で1回ずつ
（先頭50文・CPU 時間は先頭10文）測り、4 スレッドの結果と並べる。スレッドを減らすと
PASSIVE の遅れと CPU 負荷の両方が下がるかを見るため。--sweep-sentences 0 で省く。

自前ビルドの指定: --custom <wheel か、ctranslate2 パッケージを含むフォルダ>。
  省略時は exe（またはこのファイル）の隣の ct2_custom\\ を使う（配布 Zip に入れておける）。
  自前ビルドが無ければ、従来どおり mkl / dnnl の2つだけを比べる。

使い方（配布版）: ct2_mkl_compare.exe をダブルクリック。
  初回はモデル（約0.7GB）を exe 隣の models\\ にダウンロードする。Mojicast の
  models フォルダを流用するなら: ct2_mkl_compare.exe --models "C:\\...\\Mojicast\\models"
結果は exe 隣の result_*.txt / result_*.json に出る。
"""
import argparse
import ctypes
import datetime
import hashlib
import importlib.machinery
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import zipfile

FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
if not FROZEN:
    # 開発実行: リポジトリ直下の translate.py / apppaths.py を使う
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

CASES = [
    ("en_int8", "英訳 FuguMT int8（通常）"),
    ("en_fp32", "英訳 FuguMT fp32（高精度モード）"),
    ("zh", "中国語訳 M2M ja→zh"),
    ("ko", "韓国語訳 M2M ja→ko"),
]
VARIANTS = {
    "mkl": {"label": "公式・MKL あり", "env": {"CT2_USE_MKL": "1"}, "custom": False},
    "dnnl": {"label": "公式・MKL なし（oneDNN＋Intel OpenMP）", "env": {"CT2_USE_MKL": "0"},
             "custom": False},
    "custom_passive": {"label": "自前ビルド（vcomp）＋PASSIVE", "env": {"OMP_WAIT_POLICY": "PASSIVE"},
                       "custom": True},
    "custom": {"label": "自前ビルド（vcomp）・待ち方は既定", "env": {"OMP_WAIT_POLICY": ""},
               "custom": True},
}
THREADS = 4
SWEEP_THREADS = (1, 2, 4)   # 4 も同じ条件（同じ文数・1回）で測り直して並べる
CPU_GAP = 1.0      # CPU 時間を測るときの、文と文の間（秒）

# sentences.txt が無いときの予備（配信でよく出る種類の文）
FALLBACK = [
    "こんばんは、今日も配信に来てくれてありがとう。",
    "ちょっと待って、今の見た？",
    "スパチャありがとうございます！",
    "この敵めちゃくちゃ強すぎませんか。",
    "じゃあそろそろ次のステージに行きましょうか。",
    "え、待って待って、どこ行ったの？",
    "今日は新しいゲームをやっていこうと思います。",
    "コメントありがとう、みんな優しいね。",
    "うわー、びっくりした。",
    "明日も同じ時間に配信する予定です。",
]


def _peak_working_set_mb():
    """自分のプロセスのピーク使用メモリ（Windows のみ・取れなければ None）"""
    if sys.platform != "win32":
        return None

    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC),
                                                ctypes.c_ulong]
        if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return round(pmc.PeakWorkingSetSize / 1048576)
    except Exception:
        pass
    return None


def _loaded_modules():
    """このプロセスに読み込まれている DLL のフルパス一覧（Windows のみ）"""
    if sys.platform != "win32":
        return []
    try:
        psapi = ctypes.WinDLL("psapi")
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        proc = k32.GetCurrentProcess()
        mods = (ctypes.c_void_p * 2048)()
        need = ctypes.c_ulong()
        psapi.EnumProcessModules.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        if not psapi.EnumProcessModules(proc, mods, ctypes.sizeof(mods), ctypes.byref(need)):
            return []
        psapi.GetModuleFileNameExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                               ctypes.c_wchar_p, ctypes.c_ulong]
        buf = ctypes.create_unicode_buffer(1024)
        out = []
        for i in range(min(need.value // ctypes.sizeof(ctypes.c_void_p), len(mods))):
            if psapi.GetModuleFileNameExW(proc, mods[i], buf, 1024):
                out.append(buf.value)
        return out
    except Exception:
        return []


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _mask_path(p):
    """結果に残すパスから、ユーザー名が入りうる部分を伏せる（ツールの場所 → <tool>、ホーム → ~）"""
    for base, tag in ((HERE, "<tool>"), (os.path.expanduser("~"), "~")):
        if base and os.path.normcase(p).startswith(os.path.normcase(base)):
            return tag + p[len(base):]
    return p


def _runtime_info():
    """読み込まれた ctranslate2 と OpenMP ランタイムの記録"""
    import ctranslate2
    pkg = os.path.dirname(os.path.abspath(ctranslate2.__file__))
    dll = os.path.join(pkg, "ctranslate2.dll")
    mods = _loaded_modules()
    names = [os.path.basename(m).lower() for m in mods]
    info = {
        "ctranslate2_version": getattr(ctranslate2, "__version__", "?"),
        "ctranslate2_dir": pkg,
        "ctranslate2_dll_sha256": _sha256(dll) if os.path.exists(dll) else None,
        "openmp_dlls": sorted({_mask_path(m) for m, n in zip(mods, names)
                               if n.startswith(("vcomp", "libiomp", "libomp", "libgomp"))}),
        # DLL だけを見る（このツール自身の ct2_mkl_compare.exe を拾わないように）
        "intel_dlls": sorted({_mask_path(m) for m, n in zip(mods, names)
                              if n.endswith(".dll") and ("mkl" in n or "iomp" in n)}),
        "package_files": sorted(os.listdir(pkg)),
    }
    bi = os.path.join(os.path.dirname(pkg), "build-info.json")
    if os.path.exists(bi):
        with open(bi, encoding="utf-8") as f:
            info["build_info"] = json.load(f)
    return info


class _PreferCustomCt2:
    """ctranslate2 を指定フォルダから読ませる（インストール済み・PyInstaller 同梱版より優先）"""

    def __init__(self, root):
        self.root = root

    def find_spec(self, name, path=None, target=None):
        # importlib.machinery はモジュール先頭で読み込み済み（ここで import すると再帰する）
        if name == "ctranslate2":
            return importlib.machinery.PathFinder.find_spec(name, [self.root])
        if name.startswith("ctranslate2.") and path:
            return importlib.machinery.PathFinder.find_spec(name, path)
        return None


def _cpu_reg(value):
    if sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(k, value)[0].strip()
        except Exception:
            pass
    return ""


def _cpu_name():
    return _cpu_reg("ProcessorNameString") or platform.processor() or "unknown"


def _cpu_vendor():
    """GenuineIntel / AuthenticAMD 等（取れなければ空文字）"""
    return _cpu_reg("VendorIdentifier")


_MODEL_REPOS = ("models--ishiki-emo--mojicast-fugumt-ja-en-ct2",
                "models--ishiki-emo--mojicast-m2m100-ct2")


def _find_mojicast_models():
    """インストール版 Mojicast の models を探す。翻訳モデルが2つとも揃っている時だけ使う
    （足りない分をこのツールが Mojicast のフォルダへ書き足すことはしない）"""
    roots = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Mojicast"),
             os.path.join(os.environ.get("ProgramFiles", ""), "Mojicast")]
    for root in roots:
        d = os.path.join(root, "models")
        if all(os.path.isdir(os.path.join(d, "hub", r)) for r in _MODEL_REPOS):
            return d
    return None


def _prepare_custom(path, work):
    """--custom の指定（wheel / フォルダ）を「ctranslate2 パッケージを含むフォルダ」にする"""
    if not path:
        return None
    path = os.path.abspath(path)
    if os.path.isfile(path) and path.lower().endswith(".whl"):
        root = os.path.join(work, "ct2_custom")
        shutil.rmtree(root, ignore_errors=True)
        with zipfile.ZipFile(path) as z:
            z.extractall(root)
        bi = os.path.join(os.path.dirname(path), "build-info.json")
        if os.path.exists(bi):
            shutil.copy(bi, os.path.join(root, "build-info.json"))
        return root
    if os.path.isfile(os.path.join(path, "ctranslate2", "__init__.py")):
        return path
    raise SystemExit(f"自前ビルドの ctranslate2 が見つかりません: {path}\n"
                     "（wheel、または ctranslate2\\ フォルダを含むフォルダを指定してください）")


# ---------------------------------------------------------------- 子プロセス

_CYCLE_HZ = None


def _cycle_hz():
    """CPU サイクル数を秒に直す係数。1スレッドで 0.3 秒空回りして測る（一度だけ）"""
    global _CYCLE_HZ
    if _CYCLE_HZ is None:
        k32 = ctypes.windll.kernel32
        k32.GetCurrentThread.restype = ctypes.c_void_p
        c = ctypes.c_ulonglong()
        h = ctypes.c_void_p(k32.GetCurrentThread())
        k32.QueryThreadCycleTime(h, ctypes.byref(c))
        c0, t0 = c.value, time.perf_counter()
        while time.perf_counter() - t0 < 0.3:
            pass
        k32.QueryThreadCycleTime(h, ctypes.byref(c))
        _CYCLE_HZ = (c.value - c0) / (time.perf_counter() - t0)
    return _CYCLE_HZ


def _cpu_seconds():
    """プロセスが使った CPU 時間（秒）。
    Windows の os.times() はタイマー割り込みの刻み（15.6ms）で数えるので、数 ms で終わる
    計算は 0 か 15.6ms のどちらかになる（N100 の fp32・2スレッドで CPU/文 5ms のような値が出た）。
    Windows では CPU サイクル数（QueryProcessCycleTime）を _cycle_hz() で割って求める"""
    if sys.platform == "win32":
        try:
            hz = _cycle_hz()                  # 校正（空回り）を読み取りより先に済ませる
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            c = ctypes.c_ulonglong()
            if k32.QueryProcessCycleTime(ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.byref(c)):
                return c.value / hz
        except Exception:
            pass
    t = os.times()
    return t.user + t.system


def _cpu_clock_name():
    if sys.platform == "win32" and _CYCLE_HZ:
        return f"QueryProcessCycleTime（{_CYCLE_HZ / 1e9:.2f} GHz で換算）"
    return "os.times"


def run_child(variant, sentences_path, out_path, custom_root, cpu_n, threads=THREADS):
    # ctranslate2 を import する前に決める（ランタイム初期化時に読まれる）。
    # OMP_WAIT_POLICY は variant が決める（空文字 = 指定なし。translate.py の setdefault も止まる）
    os.environ.pop("CT2_USE_MKL", None)
    os.environ.pop("OMP_WAIT_POLICY", None)
    os.environ.update(VARIANTS[variant]["env"])
    os.environ["CT2_VERBOSE"] = "1"         # 使った GEMM 実装を stderr に出させる
    if VARIANTS[variant]["custom"]:
        sys.meta_path.insert(0, _PreferCustomCt2(custom_root))
    import translate as T

    with open(sentences_path, encoding="utf-8") as f:
        sentences = [s.strip() for s in f if s.strip()]

    result = {"variant": variant, "threads": threads, "cases": {},
              # translate.py を読んだ後の値（本体が実際に使う待ち方）
              "omp_wait_policy": os.environ.get("OMP_WAIT_POLICY") or "",
              "ct2_openmp": getattr(T, "CT2_OPENMP", "")}
    _cpu_seconds()                            # 換算係数の校正を計測の外で済ませる
    result["cpu_clock"] = _cpu_clock_name()
    for key, _label in CASES:
        t0 = time.perf_counter()
        if key == "en_int8":
            T.unload("m2m")
            T.load_translator(threads, "int8")
            fn = T.translate
        elif key == "en_fp32":
            T.load_translator(threads, "fp32")
            fn = T.translate
        else:
            T.unload("fugumt")
            T.load_translator_zh(threads)
            fn = (lambda s, _t=key: T.translate_m2m(s, "ja", _t))
        load_ms = (time.perf_counter() - t0) * 1000
        for s in sentences[:3]:               # 温め（計測しない）
            fn(s)
        times, outs = [], []
        for s in sentences:
            t = time.perf_counter()
            outs.append(fn(s))
            times.append((time.perf_counter() - t) * 1000)
        # 配信中の負荷: 1文訳して CPU_GAP 秒空ける（発話の間）までに使った CPU 時間。
        # OpenMP のスレッドが計算後に空回りすると、ここが増える（壁時計の速さには出ない）
        cpu_ms = []
        for s in sentences[:cpu_n]:
            c0 = _cpu_seconds()
            fn(s)
            time.sleep(CPU_GAP)
            cpu_ms.append((_cpu_seconds() - c0) * 1000)
        result["cases"][key] = {"load_ms": load_ms, "times": times, "outs": outs,
                                "peak_mb": _peak_working_set_mb(), "cpu_ms": cpu_ms}
        print(f"  [{variant}/{threads}] {key}: 中央値 {statistics.median(times):.1f}ms",
              file=sys.stderr, flush=True)
    rt = _runtime_info()
    if VARIANTS[variant]["custom"]:
        want = os.path.normcase(os.path.abspath(custom_root))
        if not os.path.normcase(rt["ctranslate2_dir"]).startswith(want):
            raise SystemExit(f"自前ビルドではなく {rt['ctranslate2_dir']} の ctranslate2 が"
                             "読み込まれました")
    rt["ctranslate2_dir"] = _mask_path(rt["ctranslate2_dir"])
    result["runtime"] = rt
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)


# ---------------------------------------------------------------- 親プロセス

def _child_cmd(variant, sentences_path, out_path, custom_root, cpu_n, threads=THREADS):
    base = [sys.executable] if FROZEN else [sys.executable, os.path.abspath(__file__)]
    return base + ["--child", variant, "--sentences", sentences_path, "--out", out_path,
                   "--custom-root", custom_root or "", "--cpu-sentences", str(cpu_n),
                   "--threads", str(threads)]


def _backend_lines(stderr_text):
    keep = ("CPU:", "Selected ISA", "Use Intel MKL", "SGEMM backend", "GEMM_S8 backend",
            "GEMM_S16 backend", "Selected compute type")
    seen, out = set(), []
    for line in stderr_text.splitlines():
        if "[ctranslate2]" not in line:
            continue
        msg = line.split("] ", 4)[-1].strip()
        if any(k in msg for k in keep) and msg not in seen:
            seen.add(msg)
            out.append(msg)
    return out


def _p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def choose_variants(requested, intel, has_custom):
    """測る variant を決める（先頭が比較の基準）"""
    if requested:
        variants = [v for v in requested.split(",") if v]
        bad = [v for v in variants if v not in VARIANTS]
        if bad:
            raise SystemExit(f"不明な variant: {bad}（使えるもの: {', '.join(VARIANTS)}）")
        if any(VARIANTS[v]["custom"] for v in variants) and not has_custom:
            raise SystemExit("custom / custom_passive を測るには --custom で自前ビルドの wheel か"
                             "フォルダを指定してください")
        return variants
    return ((["mkl", "dnnl"] if intel else ["dnnl"])
            + (["custom_passive", "custom"] if has_custom else []))


def sweep_variants(variants):
    """スレッド数を変えて測る variant（基準と自前ビルド）"""
    return [v for i, v in enumerate(variants) if i == 0 or VARIANTS[v]["custom"]]


def run_parent(args):
    sentences_path = os.path.join(HERE, "sentences.txt")
    if args.sentences:
        sentences_path = args.sentences
    if not os.path.exists(sentences_path):
        sentences_path = os.path.join(HERE, "_fallback_sentences.txt")
        with open(sentences_path, "w", encoding="utf-8") as f:
            f.write("\n".join(FALLBACK) + "\n")
    with open(sentences_path, encoding="utf-8") as f:
        n_sent = sum(1 for s in f if s.strip())
    if args.limit:
        tmp = os.path.join(HERE, "_limited_sentences.txt")
        with open(sentences_path, encoding="utf-8") as f:
            lines = [s for s in f if s.strip()][:args.limit]
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        sentences_path, n_sent = tmp, len(lines)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = os.path.join(HERE, f"_work_{stamp}")
    os.makedirs(work, exist_ok=True)

    custom_arg = args.custom
    if not custom_arg and os.path.isdir(os.path.join(HERE, "ct2_custom")):
        custom_arg = os.path.join(HERE, "ct2_custom")
    custom_root = _prepare_custom(custom_arg, work)

    cpu = _cpu_name()
    vendor = _cpu_vendor()
    intel = vendor == "GenuineIntel" or not vendor or args.force
    print(f"CPU: {cpu}")
    variants = choose_variants(args.variants, intel, bool(custom_root))
    if len(variants) < 2:
        # AMD 等で自前ビルドも無い: MKL はもともと使っていないので比べるものが無い。
        # しかも MKL を強制すると1文数秒かかり、全体で1時間を超える
        print("\nこの計測は Intel の CPU 向けです。このパソコンでは測る必要がありません。")
        print("（Intel 以外の CPU では、Mojicast はもともと MKL を使っていません）")
        print("ご協力ありがとうございました。")
        return

    env = dict(os.environ)
    env.pop("CT2_USE_MKL", None)
    env.pop("OMP_WAIT_POLICY", None)        # variant ごとに子プロセスが決める
    models = args.models or _find_mojicast_models()
    if models:
        env["HF_HOME"] = models               # apppaths の setdefault より優先される
        print(f"モデル: {models}（インストール済みの Mojicast のものを使います）")
    else:
        print("モデル: 初回はダウンロードします（約0.7GB・このフォルダの models に保存）")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if custom_root:
        print(f"自前ビルド: {custom_arg}")

    order = [(v, THREADS) for v in variants + variants[::-1]]
    n_sweep = min(args.sweep_sentences, n_sent)
    sweep_path = None
    if n_sweep:
        sweep_path = os.path.join(work, "_sweep_sentences.txt")
        with open(sentences_path, encoding="utf-8") as f:
            lines = [s for s in f if s.strip()][:n_sweep]
        with open(sweep_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        order += [(v, t) for t in SWEEP_THREADS for v in sweep_variants(variants)]
    n_main = 2 * len(variants)
    print(f"文の数: {n_sent} / 4種類の翻訳 × {n_main}回"
          f"（{' / '.join(VARIANTS[v]['label'] for v in variants)} を各2回）")
    if n_sweep:
        print(f"続けてスレッド数 {'/'.join(map(str, SWEEP_THREADS))} で {len(order) - n_main}回"
              f"（先頭 {n_sweep} 文）")
    print(f"{4 * n_main + 2 * (len(order) - n_main)}〜{8 * n_main + 4 * (len(order) - n_main)}"
          "分ほどかかります。終わるまでこの窓を閉じないでください。\n")

    runs, sweep = [], []
    for i, (variant, threads) in enumerate(order):
        main_run = i < n_main
        tag = f"run{i}_{variant}" + ("" if main_run else f"_t{threads}")
        out_path = os.path.join(work, tag + ".json")
        what = VARIANTS[variant]["label"] + ("" if main_run else f"・{threads}スレッド")
        print(f"[{i + 1}/{len(order)}] {what} で計測中...", flush=True)
        t0 = time.time()
        cmd = _child_cmd(variant, sentences_path if main_run else sweep_path, out_path,
                         custom_root,
                         min(args.cpu_sentences if main_run else args.sweep_cpu_sentences,
                             n_sent if main_run else n_sweep),
                         threads)
        try:
            p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=args.timeout)
            err = p.stderr.decode("utf-8", "replace")
        except subprocess.TimeoutExpired as e:
            err = (e.stderr or b"").decode("utf-8", "replace")
            p = None
        with open(os.path.join(work, tag + ".log"), "w", encoding="utf-8") as f:
            f.write(err)
        dest = runs if main_run else sweep
        if p is None or p.returncode != 0 or not os.path.exists(out_path):
            why = "時間切れ" if p is None else f"終了コード {p.returncode}"
            print(f"  → 失敗（{why}）。ログ: {work}")
            print("\n".join("    " + s for s in err.strip().splitlines()[-15:]))
            dest.append({"variant": variant, "threads": threads, "failed": why,
                         "backend": _backend_lines(err)})
            continue
        with open(out_path, encoding="utf-8") as f:
            r = json.load(f)
        r["backend"] = _backend_lines(err)
        r["wall_s"] = round(time.time() - t0, 1)
        dest.append(r)
        print(f"  → {r['wall_s']} 秒")

    report = summarize(runs, variants, cpu, n_sent, sweep=sweep)
    txt_path = os.path.join(HERE, f"result_{stamp}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report["text"])
    with open(os.path.join(HERE, f"result_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(report["data"], f, ensure_ascii=False, indent=1)
    print("\n" + report["text"])
    print(f"結果を保存しました: {txt_path}")
    print("この result_ で始まる .txt と .json の2つを送ってください。")


def summarize(runs, variants, cpu, n_sent, now=None, sweep=None):
    now = now or datetime.datetime.now()
    ok = {v: [r for r in runs if r["variant"] == v and "cases" in r] for v in variants}
    backend = {v: next((r.get("backend") for r in runs if r["variant"] == v), [])
               for v in variants}
    runtime = {v: (ok[v][0].get("runtime") if ok[v] else None) for v in variants}
    lines = [f"CTranslate2 比較  {now:%Y-%m-%d %H:%M}",
             f"CPU: {cpu}",
             f"OS: {platform.platform()}",
             f"文の数: {n_sent}  スレッド: {THREADS}", ""]
    for v in variants:
        rt = runtime[v] or {}
        lines.append(f"[{v}] {VARIANTS[v]['label']}")
        if ok[v]:
            r0 = ok[v][0]
            lines.append(f"  OMP_WAIT_POLICY: {r0.get('omp_wait_policy') or '（指定なし）'}"
                         f"  translate.py の判定: {r0.get('ct2_openmp') or '-'}")
        lines.append(f"  ctranslate2 {rt.get('ctranslate2_version', '?')}"
                     f"  DLL sha256 {(rt.get('ctranslate2_dll_sha256') or '?')[:16]}")
        omp = ", ".join(os.path.basename(x) for x in rt.get("openmp_dlls", []))
        lines.append(f"  OpenMP: {omp or '（読み込まれていない）'}")
        if rt.get("intel_dlls"):
            lines.append("  Intel の DLL: "
                         + ", ".join(os.path.basename(x) for x in rt["intel_dlls"]))
        bi = rt.get("build_info")
        if bi:
            lines.append("  ビルド: " + " ".join(bi.get("cmake", [])))
            lines.append(f"  oneDNN {(bi.get('onednn') or {}).get('version')}"
                         f" / OpenMP {bi.get('openmp_runtime')} / MSVC {bi.get('msvc')}")
        lines += ["  " + s for s in (backend[v] or ["（ログなし）"])]
    lines.append("")
    failed = [r for r in runs if "failed" in r]
    failed += [r for r in (sweep or []) if "failed" in r]
    for r in failed:
        lines.append(f"!! 失敗した回: {r['variant']}・{r.get('threads', THREADS)}スレッド"
                     f"（{r['failed']}）")
    data = {"cpu": cpu, "os": platform.platform(), "n": n_sent, "threads": THREADS,
            "variants": variants, "backend": backend, "runtime": runtime,
            "omp_wait_policy": {v: (ok[v][0].get("omp_wait_policy") if ok[v] else None)
                                for v in variants},
            "failed": [{"variant": r["variant"], "threads": r.get("threads", THREADS),
                        "why": r["failed"]} for r in failed],
            "cases": {}, "sweep": {}}
    usable = [v for v in variants if ok[v]]
    if len(usable) < 2:
        lines.append("2つ以上の設定で完走しなかったため、比較できません。")
        return {"text": "\n".join(lines) + "\n", "data": data}
    base = usable[0]

    def best_times(rs, key):
        cols = [r["cases"][key]["times"] for r in rs]
        return [min(ts) for ts in zip(*cols)]

    lines.append(f"基準: {base}（{VARIANTS[base]['label']}）。倍率 = 平均 ÷ 基準の平均"
                 "（1 より大きいほど遅い）、一致 = 基準と同じ訳文の数")
    lines.append(f"CPU/文 = 1文訳して {CPU_GAP:g} 秒空けるまでに使った CPU 時間の平均"
                 "（配信中の負荷。計算後に空回りすると増える）")
    clock = ok[base][0].get("cpu_clock") or "os.times（15.6ms 刻み・参考値）"
    lines.append(f"  CPU 時間の測り方: {clock}")
    data["cpu_clock"] = clock
    diffs_all = []
    for key, label in CASES:
        lines.append(f"\n{label}")
        lines.append(f"  {'':<16}{'中央値 / 平均 / p95 (ms)':<28}{'倍率':<8}{'一致':<10}"
                     f"{'ロード':<10}{'ピークメモリ':<10}CPU/文")
        base_mean = statistics.mean(best_times(ok[base], key))
        base_out = ok[base][0]["cases"][key]["outs"]
        data["cases"][key] = {}
        for v in usable:
            t = best_times(ok[v], key)
            outs = ok[v][0]["cases"][key]["outs"]
            same = sum(1 for x, y in zip(base_out, outs) if x == y)
            mean = statistics.mean(t)
            ratio = mean / base_mean if base_mean else 0
            load = min(r["cases"][key]["load_ms"] for r in ok[v])
            peak = max((r["cases"][key]["peak_mb"] or 0) for r in ok[v])
            diffs = [{"i": i, "base": x, "other": y}
                     for i, (x, y) in enumerate(zip(base_out, outs)) if x != y]
            if v != base and diffs:
                diffs_all.append((label, v, diffs))
            # CPU 時間は2回のうち少ない方（他のプロセスの割り込みを除くため）
            cpus = [statistics.mean(r["cases"][key]["cpu_ms"]) for r in ok[v]
                    if r["cases"][key].get("cpu_ms")]
            cpu = min(cpus) if cpus else None
            data["cases"][key][v] = {
                "median": statistics.median(t), "mean": mean, "p95": _p95(t),
                "load_ms": load, "peak_mb": peak, "ratio_mean": ratio, "cpu_ms": cpu,
                "same": same, "total": len(outs), "diffs": diffs}
            fmt = f"{statistics.median(t):.1f} / {mean:.1f} / {_p95(t):.1f}"
            load_s = "（zh と共通）" if key == "ko" else f"{load:.0f}ms"
            cpu_s = f"{cpu:.0f}ms" if cpu is not None else "-"
            lines.append(f"  {v:<16}{fmt:<28}{ratio:>5.2f}倍  {same:>3}/{len(outs):<6}"
                         f"{load_s:<10}{str(peak) + 'MB':<10}{cpu_s}")
    lines.append("")
    lines.append(f"訳文が変わった文（各最大10件・上=基準 {base} / 下=比べた側）:")
    if not diffs_all:
        lines.append("  なし")
    for label, v, ds in diffs_all:
        lines.append(f"  -- {label} / {v}: {len(ds)}件")
        for d in ds[:10]:
            lines.append(f"    #{d['i']}  {d['base']}")
            lines.append(f"    {' ' * (len(str(d['i'])) + 1)}  {d['other']}")
    _summarize_sweep(lines, data, ok, base, sweep or [])
    return {"text": "\n".join(lines) + "\n", "data": data}


def _summarize_sweep(lines, data, ok, base, sweep):
    """スレッド数の比較。どのスレッド数も同じ条件（先頭 n 文・1回・CPU/文 は同じ文数）で測った
    sweep の回から作る。古い結果で sweep に 4 スレッドが無ければ、本計測から補う（条件は揃わない）。
    一致(同) の比べる相手は、本計測の 4 スレッドの訳文"""
    done = [r for r in sweep if "cases" in r]
    if not done:
        return
    n = min(len(r["cases"][CASES[0][0]]["outs"]) for r in done)
    vs = []
    for r in done:
        if r["variant"] not in vs and ok.get(r["variant"]):
            vs.append(r["variant"])
    lines.append("")
    lines.append(f"スレッド数の比較（先頭 {n} 文・各1回・どのスレッド数も同じ条件）。"
                 "CPU/文 は先頭の数文で測った値。")
    lines.append(f"  一致(同) = 同じ variant の 4 スレッドと同じ訳文の数、"
                 f"一致(基準) = 基準 {base} の 4 スレッドと同じ訳文の数")
    for key, label in CASES:
        lines.append(f"\n{label}")
        lines.append(f"  {'':<16}{'スレッド':<6}{'中央値 / 平均 (ms)':<20}{'CPU/文':<10}"
                     f"{'一致(同)':<10}一致(基準)")
        base_out = ok[base][0]["cases"][key]["outs"][:n]
        data["sweep"][key] = {}
        for v in vs:
            four = ok[v][0]["cases"][key]["outs"][:n]
            rows = []
            if not any(r["variant"] == v and r["threads"] == THREADS for r in done):
                t4 = [min(ts) for ts in zip(*[r["cases"][key]["times"][:n] for r in ok[v]])]
                cpus4 = [statistics.mean(r["cases"][key]["cpu_ms"]) for r in ok[v]
                         if r["cases"][key].get("cpu_ms")]
                rows.append((THREADS, t4, min(cpus4) if cpus4 else None, four))
            for r in done:
                if r["variant"] == v:
                    c = r["cases"][key]
                    rows.append((r["threads"], c["times"][:n],
                                 statistics.mean(c["cpu_ms"]) if c.get("cpu_ms") else None,
                                 c["outs"][:n]))
            rows.sort(key=lambda x: x[0])
            data["sweep"][key][v] = {}
            for th, t, cpu, outs in rows:
                same_self = sum(1 for x, y in zip(four, outs) if x == y)
                same_base = sum(1 for x, y in zip(base_out, outs) if x == y)
                data["sweep"][key][v][str(th)] = {
                    "median": statistics.median(t), "mean": statistics.mean(t),
                    "cpu_ms": cpu, "same_self": same_self, "same_base": same_base,
                    "total": len(outs)}
                fmt = f"{statistics.median(t):.1f} / {statistics.mean(t):.1f}"
                cpu_s = f"{cpu:.0f}ms" if cpu is not None else "-"
                lines.append(f"  {v:<16}{th:<8}{fmt:<22}{cpu_s:<10}"
                             f"{same_self:>3}/{len(outs):<6}{same_base:>3}/{len(outs)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", choices=list(VARIANTS))
    ap.add_argument("--custom-root", default="")
    ap.add_argument("--sentences")
    ap.add_argument("--out")
    ap.add_argument("--custom", help="自前ビルドの wheel、または ctranslate2\\ を含むフォルダ")
    ap.add_argument("--variants", help="測る組み合わせ（例: dnnl,custom）。先頭が基準。既定は自動")
    ap.add_argument("--models", help="Mojicast の models フォルダを流用する場合に指定")
    ap.add_argument("--limit", type=int, default=0, help="文の数を絞る（試運転用）")
    ap.add_argument("--cpu-sentences", type=int, default=20,
                    help="CPU 時間（1文＋1秒の間）を測る文の数。0 で測らない")
    ap.add_argument("--sweep-sentences", type=int, default=50,
                    help="スレッド数 1/2 の比較に使う文の数。0 で比較しない")
    ap.add_argument("--sweep-cpu-sentences", type=int, default=10,
                    help="スレッド数の比較で CPU 時間を測る文の数")
    ap.add_argument("--threads", type=int, default=THREADS, help=argparse.SUPPRESS)
    ap.add_argument("--timeout", type=int, default=1800, help="1回あたりの上限秒")
    ap.add_argument("--no-pause", action="store_true")
    ap.add_argument("--force", action="store_true", help="Intel 以外の CPU でも mkl を測る")
    args = ap.parse_args()
    if args.child:
        run_child(args.child, args.sentences, args.out, args.custom_root, args.cpu_sentences,
                  args.threads)
        return
    try:
        run_parent(args)
    finally:
        if FROZEN and not args.no_pause:
            input("\nEnter キーで閉じます")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
