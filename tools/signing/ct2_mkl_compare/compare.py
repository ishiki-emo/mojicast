# -*- coding: utf-8 -*-
"""翻訳エンジン（CTranslate2）の Intel MKL あり/なし比較ツール。

SignPath Foundation の署名条件（同梱物がすべて OSI 承認ライセンス）を満たすには、
ctranslate2.dll に静的リンクされた Intel oneMKL を外したビルドが要る。
AMD 機はもともと MKL を使わず oneDNN で動いているので、影響を受けるのは Intel 機だけ。
現行の ctranslate2 は環境変数 CT2_USE_MKL=0 で MKL を使わない経路（＝MKL 抜きビルドと
同じ oneDNN 経路）に切り替わるので、ビルドし直さずに Intel 機で差を測れる。

測るもの（本体の translate.py をそのまま呼ぶ・intra_threads=4 は本体と同じ）:
  - en_int8 : 英訳 FuguMT int8（通常）
  - en_fp32 : 英訳 FuguMT fp32（高精度モード）
  - zh      : 多言語訳 M2M-100 ja→zh（int8・greedy）
  - ko      : 多言語訳 M2M-100 ja→ko（int8・beam 4）
  1文ごとの時間、訳文が MKL あり/なしで一致するか、ロード時間、ピークメモリ。

MKL=1 → MKL=0 → MKL=0 → MKL=1 の順に4回（別プロセス）走らせ、1文ごとに同じ設定の
2回のうち速い方を採る（初回のファイル読み込みや順番の有利不利を打ち消すため）。

使い方（配布版）: ct2_mkl_compare.exe をダブルクリック。
  初回はモデル（約0.7GB）を exe 隣の models\\ にダウンロードする。Mojicast の
  models フォルダを流用するなら: ct2_mkl_compare.exe --models "C:\\...\\Mojicast\\models"
結果は exe 隣の result_*.txt / result_*.json に出る。
"""
import argparse
import ctypes
import datetime
import json
import os
import platform
import statistics
import subprocess
import sys
import time

FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
if not FROZEN:
    # 開発実行: リポジトリ直下の translate.py / apppaths.py を使う
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

CASES = [
    ("en_int8", "英訳 FuguMT int8（通常）"),
    ("en_fp32", "英訳 FuguMT fp32（高精度モード）"),
    ("zh", "中国語訳 M2M ja→zh"),
    ("ko", "韓国語訳 M2M ja→ko"),
]
ORDER = ["1", "0", "0", "1"]
THREADS = 4

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


def _cpu_name():
    if sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:
            pass
    return platform.processor() or "unknown"


# ---------------------------------------------------------------- 子プロセス

def run_child(mode, sentences_path, out_path):
    # ctranslate2 を import する前に決める（ランタイム初期化時に読まれる）
    os.environ["CT2_USE_MKL"] = mode
    os.environ["CT2_VERBOSE"] = "1"         # 使った GEMM 実装を stderr に出させる
    import translate as T

    with open(sentences_path, encoding="utf-8") as f:
        sentences = [s.strip() for s in f if s.strip()]

    result = {"mode": mode, "cases": {}}
    for key, _label in CASES:
        t0 = time.perf_counter()
        if key == "en_int8":
            T.unload("m2m")
            T.load_translator(THREADS, "int8")
            fn = T.translate
        elif key == "en_fp32":
            T.load_translator(THREADS, "fp32")
            fn = T.translate
        else:
            T.unload("fugumt")
            T.load_translator_zh(THREADS)
            fn = (lambda s, _t=key: T.translate_m2m(s, "ja", _t))
        load_ms = (time.perf_counter() - t0) * 1000
        for s in sentences[:3]:               # 温め（計測しない）
            fn(s)
        times, outs = [], []
        for s in sentences:
            t = time.perf_counter()
            outs.append(fn(s))
            times.append((time.perf_counter() - t) * 1000)
        result["cases"][key] = {"load_ms": load_ms, "times": times, "outs": outs,
                                "peak_mb": _peak_working_set_mb()}
        print(f"  [{mode}] {key}: 中央値 {statistics.median(times):.1f}ms",
              file=sys.stderr, flush=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)


# ---------------------------------------------------------------- 親プロセス

def _child_cmd(mode, sentences_path, out_path):
    base = [sys.executable] if FROZEN else [sys.executable, os.path.abspath(__file__)]
    return base + ["--child", mode, "--sentences", sentences_path, "--out", out_path]


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

    env = dict(os.environ)
    if args.models:
        env["HF_HOME"] = args.models          # apppaths の setdefault より優先される
    env.setdefault("PYTHONIOENCODING", "utf-8")

    cpu = _cpu_name()
    print(f"CPU: {cpu}")
    print(f"文の数: {n_sent} / 4ケース × 4回（MKL あり2回・なし2回）")
    print("初回はモデルのダウンロードがあります。数分〜十数分かかります。\n")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = os.path.join(HERE, f"_work_{stamp}")
    os.makedirs(work, exist_ok=True)
    runs = []
    for i, mode in enumerate(ORDER):
        out_path = os.path.join(work, f"run{i}_mkl{mode}.json")
        label = "MKL あり" if mode == "1" else "MKL なし"
        print(f"[{i + 1}/{len(ORDER)}] {label} で計測中...", flush=True)
        t0 = time.time()
        try:
            p = subprocess.run(_child_cmd(mode, sentences_path, out_path), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=args.timeout)
            err = p.stderr.decode("utf-8", "replace")
        except subprocess.TimeoutExpired as e:
            err = (e.stderr or b"").decode("utf-8", "replace")
            p = None
        with open(os.path.join(work, f"run{i}_mkl{mode}.log"), "w",
                  encoding="utf-8") as f:
            f.write(err)
        if p is None or p.returncode != 0 or not os.path.exists(out_path):
            why = "時間切れ" if p is None else f"終了コード {p.returncode}"
            print(f"  → 失敗（{why}）。ログ: {work}")
            print("\n".join("    " + s for s in err.strip().splitlines()[-15:]))
            runs.append({"mode": mode, "failed": why, "backend": _backend_lines(err)})
            continue
        with open(out_path, encoding="utf-8") as f:
            r = json.load(f)
        r["backend"] = _backend_lines(err)
        r["wall_s"] = round(time.time() - t0, 1)
        runs.append(r)
        print(f"  → {r['wall_s']} 秒")

    report = summarize(runs, cpu, n_sent)
    txt_path = os.path.join(HERE, f"result_{stamp}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report["text"])
    with open(os.path.join(HERE, f"result_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(report["data"], f, ensure_ascii=False, indent=1)
    print("\n" + report["text"])
    print(f"結果を保存しました: {txt_path}")


def summarize(runs, cpu, n_sent):
    by_mode = {"1": [r for r in runs if r["mode"] == "1" and "cases" in r],
               "0": [r for r in runs if r["mode"] == "0" and "cases" in r]}
    backend = {m: next((r.get("backend") for r in runs if r["mode"] == m), [])
               for m in ("1", "0")}
    lines = [f"CTranslate2 MKL あり/なし比較  {datetime.datetime.now():%Y-%m-%d %H:%M}",
             f"CPU: {cpu}",
             f"OS: {platform.platform()}",
             f"文の数: {n_sent}  スレッド: {THREADS}", ""]
    for m, label in (("1", "MKL あり（CT2_USE_MKL=1）"), ("0", "MKL なし（CT2_USE_MKL=0）")):
        lines.append(f"[{label}] 使われた実装:")
        lines += ["  " + s for s in (backend[m] or ["（ログなし）"])]
    lines.append("")
    failed = [r for r in runs if "failed" in r]
    for r in failed:
        lines.append(f"!! 失敗した回: MKL={r['mode']}（{r['failed']}）")
    data = {"cpu": cpu, "os": platform.platform(), "n": n_sent, "backend": backend,
            "failed": [{"mode": r["mode"], "why": r["failed"]} for r in failed],
            "cases": {}}
    if not by_mode["1"] or not by_mode["0"]:
        lines.append("片方の設定で1回も完走しなかったため、比較できません。")
        return {"text": "\n".join(lines) + "\n", "data": data}

    def best_times(rs, key):
        cols = [r["cases"][key]["times"] for r in rs]
        return [min(ts) for ts in zip(*cols)]

    lines.append(f"{'ケース':<34}{'MKLあり 中央値/平均/p95 (ms)':<32}"
                 f"{'MKLなし 中央値/平均/p95 (ms)':<32}{'なし/あり':<10}{'訳文一致':<10}")
    diffs_all = {}
    for key, label in CASES:
        a = best_times(by_mode["1"], key)
        b = best_times(by_mode["0"], key)
        oa = by_mode["1"][0]["cases"][key]["outs"]
        ob = by_mode["0"][0]["cases"][key]["outs"]
        same = sum(1 for x, y in zip(oa, ob) if x == y)
        ratio = statistics.mean(b) / statistics.mean(a) if statistics.mean(a) else 0
        fa = f"{statistics.median(a):.1f} / {statistics.mean(a):.1f} / {_p95(a):.1f}"
        fb = f"{statistics.median(b):.1f} / {statistics.mean(b):.1f} / {_p95(b):.1f}"
        lines.append(f"{label:<30}{fa:<34}{fb:<34}{ratio:>5.2f}倍    "
                     f"{same}/{len(oa)}")
        load_a = min(r["cases"][key]["load_ms"] for r in by_mode["1"])
        load_b = min(r["cases"][key]["load_ms"] for r in by_mode["0"])
        peak_a = max((r["cases"][key]["peak_mb"] or 0) for r in by_mode["1"])
        peak_b = max((r["cases"][key]["peak_mb"] or 0) for r in by_mode["0"])
        diffs = []
        for i, (x, y) in enumerate(zip(oa, ob)):
            if x != y:
                diffs.append({"i": i, "mkl": x, "nomkl": y})
        diffs_all[key] = diffs
        data["cases"][key] = {
            "mkl": {"median": statistics.median(a), "mean": statistics.mean(a),
                    "p95": _p95(a), "load_ms": load_a, "peak_mb": peak_a},
            "nomkl": {"median": statistics.median(b), "mean": statistics.mean(b),
                      "p95": _p95(b), "load_ms": load_b, "peak_mb": peak_b},
            "ratio_mean": ratio, "same": same, "total": len(oa), "diffs": diffs,
        }
    lines.append("")
    lines.append("ロード時間 (ms) / ピークメモリ (MB・その時点までの最大):")
    for key, label in CASES:
        c = data["cases"][key]
        la, lb = c["mkl"]["load_ms"], c["nomkl"]["load_ms"]
        load = ("（zh と同じモデル）" if key == "ko" else
                f"あり {la:.0f}ms / なし {lb:.0f}ms")
        lines.append(f"  {label:<30} {load}   メモリ あり {c['mkl']['peak_mb']}MB"
                     f" / なし {c['nomkl']['peak_mb']}MB")
    lines.append("")
    lines.append("訳文が変わった文（各ケース最大10件・上=MKLあり / 下=MKLなし）:")
    for key, label in CASES:
        ds = diffs_all[key]
        if not ds:
            continue
        lines.append(f"  -- {label}: {len(ds)}件")
        for d in ds[:10]:
            lines.append(f"    #{d['i']}  {d['mkl']}")
            lines.append(f"    {' ' * (len(str(d['i'])) + 1)}  {d['nomkl']}")
    return {"text": "\n".join(lines) + "\n", "data": data}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", choices=["0", "1"])
    ap.add_argument("--sentences")
    ap.add_argument("--out")
    ap.add_argument("--models", help="Mojicast の models フォルダを流用する場合に指定")
    ap.add_argument("--limit", type=int, default=0, help="文の数を絞る（試運転用）")
    ap.add_argument("--timeout", type=int, default=1800, help="1回あたりの上限秒")
    ap.add_argument("--no-pause", action="store_true")
    args = ap.parse_args()
    if args.child:
        run_child(args.child, args.sentences, args.out)
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
