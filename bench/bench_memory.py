# -*- coding: utf-8 -*-
"""ユースケース別メモリ実測（アプリ本体のモデル群を実際にロードして測る）

各ケースを**別プロセス**で走らせ、コンポーネントを1つずつ足しながら
RSS（実使用メモリ）を測る。断片化や解放漏れの影響を受けないよう、
ケース間でプロセスを使い回さない。

測るもの: 起動直後 → 各モデルのロード後 → 実推論後 → ピーク

実行:
  reazonspeech-env\\Scripts\\python.exe bench\\bench_memory.py
  ... --cases standard,standard+en      # ケースを絞る
  ... --wav 20260708.wav                # 実音声で推論（既定は合成音）
  ... --json out.json                   # 生データを保存

※ GUI窓（WebView2）は別プロセスなので本スクリプトには含まれない。
   アプリ全体（UI込み）の実測は bench\\mem_watch.ps1 を使うこと。
"""
import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

SAMPLE_RATE = 16000
TEXT = "こんばんはみなさん今日も配信を始めます今日は新しい機能のテストです"

# ケース定義: 名前 → コンポーネント列（この順に足しながら測る）
CASES = {
    # --- 単体 ---
    "base":         [],
    "vad":          ["vad"],
    "k2":           ["k2"],
    "k2-fp32":      ["k2-fp32"],
    "k2-int8":      ["k2-int8"],
    "sensevoice":   ["sensevoice"],
    "punct":        ["punct"],
    "trans-en":     ["trans-en"],
    "trans-zh":     ["trans-zh"],
    "sfx":          ["sfx"],
    # --- 実際の使われ方 ---
    "standard":     ["vad", "k2", "punct"],
    "standard+en":  ["vad", "k2", "punct", "trans-en"],
    "standard+zh":  ["vad", "k2", "punct", "trans-zh"],
    "standard+sfx": ["vad", "k2", "punct", "sfx"],
    "multi":        ["vad", "sensevoice"],
    "multi+zh":     ["vad", "sensevoice", "trans-zh"],
    "collab":       ["vad", "k2", "punct", "vad"],  # 2話者=VAD2本（Recognizerは共有）
    "all":          ["vad", "k2", "punct", "trans-en", "trans-zh", "sfx"],
}
DEFAULT_ORDER = ["base", "standard", "standard+en", "standard+zh",
                 "standard+sfx", "collab", "multi", "multi+zh", "all",
                 "vad", "k2", "k2-fp32", "k2-int8", "sensevoice",
                 "punct", "trans-en", "trans-zh", "sfx"]


# ---------------- メモリ取得（psutil を足さずに済ませる） ----------------
if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wt

    class _PMCEX(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    _GPMI = ctypes.windll.psapi.GetProcessMemoryInfo
    _GPMI.argtypes = [wt.HANDLE, ctypes.POINTER(_PMCEX), wt.DWORD]

    def mem_mb():
        """(現在RSS, ピークRSS, プライベート=コミット) を MB で返す"""
        c = _PMCEX()
        c.cb = ctypes.sizeof(_PMCEX)
        # 疑似ハンドル(-1)は 64bit へ正しく渡す必要があるので HANDLE 指定は必須
        _GPMI(ctypes.c_void_p(-1), ctypes.byref(c), c.cb)
        return (c.WorkingSetSize / 1048576, c.PeakWorkingSetSize / 1048576,
                c.PrivateUsage / 1048576)
else:
    import resource

    def mem_mb():
        rss = 0.0
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                             capture_output=True, text=True).stdout.strip()
        if out:
            rss = int(out) / 1024        # ps は KB 単位
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = peak / 1048576 if sys.platform == "darwin" else peak / 1024
        return rss, peak, rss


# ---------------- 子プロセス側: 1ケースを測る ----------------
def _audio(wav_path):
    """16kHz mono float32 の5秒を用意（wav 未指定なら合成音）"""
    import numpy as np
    if not wav_path:
        t = np.arange(SAMPLE_RATE * 5, dtype=np.float32) / SAMPLE_RATE
        return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    import wave
    with wave.open(wav_path) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(min(w.getnframes(), rate * 5))
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != SAMPLE_RATE:      # 素朴な線形補間（測定目的なので音質は不問）
        n = int(len(x) * SAMPLE_RATE / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n),
                      np.arange(len(x)), x).astype(np.float32)
    return x


def _load(comp, state):
    """コンポーネントを1つロードして state に積む"""
    if comp == "vad":
        import sherpa_onnx
        from engine import VAD_MODEL_PATH, WINDOW_SIZE
        c = sherpa_onnx.VadModelConfig()
        c.silero_vad.model = VAD_MODEL_PATH
        c.silero_vad.threshold = 0.5
        c.silero_vad.min_silence_duration = 0.3
        c.silero_vad.min_speech_duration = 0.25
        c.silero_vad.window_size = WINDOW_SIZE
        c.sample_rate = SAMPLE_RATE
        state.setdefault("vad", []).append(
            sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=30))
    elif comp in ("k2", "k2-fp32", "k2-int8"):
        from asr_model import load_by_config
        prec = {"k2": "int8-fp32", "k2-fp32": "fp32", "k2-int8": "int8"}[comp]
        state["asr"], _ = load_by_config("k2-ja", precision=prec)
    elif comp == "sensevoice":
        from asr_model import load_by_config
        state["asr"], _ = load_by_config("sensevoice")
    elif comp == "punct":
        import punct
        punct.load_punctuator()
        state["punct"] = punct.add_punctuation
    elif comp == "trans-en":
        import translate
        translate.load_translator()
        state["trans_en"] = translate.translate
    elif comp == "trans-zh":
        import translate
        translate.load_translator_zh()
        state["trans_zh"] = translate.translate_m2m
    elif comp == "sfx":
        import soundfx
        state["sfx"] = soundfx.load_tagger()
    else:
        raise ValueError("未知のコンポーネント: " + comp)


def _infer(state, audio, rounds=3):
    """実推論を回す（推論用アリーナ確保ぶんを表に出すため）"""
    from engine import WINDOW_SIZE
    for _ in range(rounds):
        for vad in state.get("vad", []):
            for i in range(0, len(audio) - WINDOW_SIZE, WINDOW_SIZE):
                vad.accept_waveform(audio[i:i + WINDOW_SIZE])
            while not vad.empty():
                vad.pop()
        if "asr" in state:
            st = state["asr"].create_stream()
            st.accept_waveform(SAMPLE_RATE, audio)
            state["asr"].decode_stream(st)
        if "punct" in state:
            state["punct"](TEXT)
        if "trans_en" in state:
            state["trans_en"](TEXT)
        if "trans_zh" in state:
            state["trans_zh"](TEXT, "ja", "zh")
        if "sfx" in state:
            state["sfx"].classify(audio)


def run_child(case, wav):
    steps = [{"step": "python起動", "rss": mem_mb()[0]}]
    import engine  # noqa: F401  アプリ本体（numpy等）を読み込んだ状態を基準にする
    audio = _audio(wav)
    steps.append({"step": "アプリ土台", "rss": mem_mb()[0]})
    state = {}
    for comp in CASES[case]:
        _load(comp, state)
        steps.append({"step": "+" + comp, "rss": mem_mb()[0]})
    _infer(state, audio)
    rss, peak, priv = mem_mb()
    steps.append({"step": "推論後", "rss": rss})
    print("@@RESULT@@" + json.dumps(
        {"case": case, "steps": steps, "rss": rss, "peak": peak,
         "private": priv}, ensure_ascii=False))


# ---------------- 親プロセス側 ----------------
def run_parent(cases, wav, json_path):
    results = []
    for case in cases:
        comps = " + ".join(CASES[case]) or "モデルなし"
        print("[{}] 測定中 ({}) ...".format(case, comps), flush=True)
        cmd = [sys.executable, os.path.abspath(__file__), "--child", case]
        if wav:
            cmd += ["--wav", os.path.abspath(wav)]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT)
        line = next((l for l in p.stdout.splitlines()
                     if l.startswith("@@RESULT@@")), None)
        if line is None:
            tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
            print("  失敗: " + " / ".join(tail))
            continue
        r = json.loads(line[len("@@RESULT@@"):])
        results.append(r)
        base = r["steps"][0]["rss"]
        for s in r["steps"][1:]:
            print("    {:<14} {:7.1f} MB (+{:6.1f})".format(
                s["step"], s["rss"], s["rss"] - base))
        print("    → 常駐 {:.0f} MB / ピーク {:.0f} MB / コミット {:.0f} MB".format(
            r["rss"], r["peak"], r["private"]))

    print("\n" + "=" * 70)
    print("{:<14}{:>10}{:>10}{:>10}  内訳(起動直後からの累積MB)".format(
        "ケース", "常駐RSS", "ピーク", "コミット"))
    print("-" * 70)
    for r in results:
        inner = " ".join("{}={:.0f}".format(s["step"], s["rss"] - r["steps"][0]["rss"])
                         for s in r["steps"][2:-1])
        print("{:<14}{:9.0f}M{:9.0f}M{:9.0f}M  {}".format(
            r["case"], r["rss"], r["peak"], r["private"], inner))
    print("=" * 70)
    print("※ GUI窓(WebView2)は別プロセスのため未計上。UI込みは mem_watch.ps1 で測る。")
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("生データ: " + json_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="",
                    help="カンマ区切り。既定は全ケース。候補: " + ",".join(CASES))
    ap.add_argument("--wav", default="", help="実音声(先頭5秒)で推論する")
    ap.add_argument("--json", default="", help="生データの保存先")
    ap.add_argument("--child", default="", help="内部用（1ケースを子プロセスで測る）")
    a = ap.parse_args()
    if a.child:
        run_child(a.child, a.wav)
        return
    names = [c.strip() for c in a.cases.split(",") if c.strip()] or DEFAULT_ORDER
    bad = [c for c in names if c not in CASES]
    if bad:
        sys.exit("未知のケース: " + ", ".join(bad))
    run_parent(names, a.wav, a.json)


if __name__ == "__main__":
    main()
