"""エフェクトのトリガーになる音を探すための実地計測ツール

2系統を同じ音声で比べられる:
  1. SenseVoice の感情/音声イベント（engine.py と同じ Silero VAD で発話単位に区切る）
  2. AudioSet 音イベント分類（--tagging。笑い・拍手・歓声・歌などを確率つきで）
入力はマイク / アプリの再生音（Chrome等のプロセスループバック）/ wavファイル。
コラボ相当の2系統同時（マイク＋アプリ）も回せる。

使い方:
    # 一覧
    python bench\\probe_emotion_live.py --list      # 入力デバイス
    python bench\\probe_emotion_live.py --apps      # 音を出しているアプリ(pid付き)

    # 音イベント分類だけ（笑い声などを探す。これが本命）
    python bench\\probe_emotion_live.py --process chrome.exe --tagging --no-asr --record

    # コラボ相当（マイク＝自分 / Chrome＝ゲスト）で認識と分類を併走
    python bench\\probe_emotion_live.py --collab --process chrome.exe --tagging --record

    # 手持ちのwavで
    python bench\\probe_emotion_live.py --wav 20260708.wav --tagging

停止は Ctrl+C。停止時に集計サマリを出し、生データを logs/emotion_probe_*.jsonl
（分類は *_tags.jsonl）に残す。--record を付けると取り込んだ音もwavで残るので、
あとから条件を変えて何度でも再解析できる。
"""
import argparse
import json
import os
import queue
import sys
import threading
import time
from collections import Counter
from datetime import datetime

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

SAMPLE_RATE = 16000
WINDOW_SIZE = 512
VAD_MODEL_PATH = os.path.join(ROOT, "silero_vad.onnx")

# 「何も起きていない」扱いにするラベル。エフェクトを出さない側。
EMO_IDLE = {"EMO_UNKNOWN", "NEUTRAL", "OTHER", ""}
EV_IDLE = {"Speech", "Speech_Noise", "Event_UNK", ""}

# ---- 音イベント分類（AudioSet 527クラス・sherpa-onnx の AudioTagging） ----
# SenseVoice の感情が使い物にならなかったため、エフェクトのトリガー候補として
# こちらを評価する。感情と違い確率つきで返るので、閾値と平滑化で安定させられる。
TAG_REPO = "k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15"
TAG_FILES = ["model.int8.onnx", "class_labels_indices.csv"]

# 演出に使えそうなクラスを「グループ」でまとめ、確率を合算して判定する。
# 実測（2026-08-17 実配信178秒）で、笑い声は Laughter/Snicker/Chuckle/Giggle が
# 同時に跳ねる（例: Snicker 0.64 + Chuckle 0.44 + Laughter 0.36）。
# 単独クラスの閾値では取りこぼすが、合算すれば地力が出る
# ——合算スコアの中央値は0.000・90%点0.02に対しピーク1.52と、分離は極めて良い。
TAG_GROUPS = {
    "笑い": {"Laughter", "Giggle", "Snicker", "Belly laugh",
             "Chuckle, chortle", "Baby laughter"},
    "拍手・歓声": {"Applause", "Clapping", "Cheering"},
    "歌・口笛": {"Singing", "Male singing", "Female singing",
                 "Child singing", "Humming", "Whistling"},
    "泣き": {"Crying, sobbing", "Whimper", "Sniff"},
    "驚き・叫び": {"Screaming", "Shout", "Yell", "Squeal", "Gasp"},
    "咳・くしゃみ": {"Cough", "Sneeze", "Throat clearing"},
}
TAG_WATCH = set().union(*TAG_GROUPS.values())


def _tag(s):
    """'<|HAPPY|>' → 'HAPPY'（タグでなければそのまま）"""
    s = (s or "").strip()
    if s.startswith("<|") and s.endswith("|>"):
        return s[2:-2]
    return s


def load_wav_any(path):
    """wav/mp3等 → float32 mono 16kHz"""
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SAMPLE_RATE:
        # 整数倍は間引き平均、それ以外は線形補間（調査用途には十分）
        if sr % SAMPLE_RATE == 0:
            k = sr // SAMPLE_RATE
            x = x[: len(x) // k * k].reshape(-1, k).mean(axis=1)
        else:
            n = int(len(x) * SAMPLE_RATE / sr)
            x = np.interp(np.linspace(0, len(x) - 1, n),
                          np.arange(len(x)), x)
    return np.asarray(x, dtype=np.float32)


def load_tagging(num_threads=2, top_k=20):
    """AudioSet音イベント分類器をロード（int8で27MB。無ければHFから取得）"""
    import sherpa_onnx
    import huggingface_hub as hf
    try:
        d = hf.snapshot_download(TAG_REPO, allow_patterns=TAG_FILES,
                                 local_files_only=True)
    except Exception:
        print("音イベント分類モデルを取得します（27MB）…")
        d = hf.snapshot_download(TAG_REPO, allow_patterns=TAG_FILES)
    cfg = sherpa_onnx.AudioTaggingConfig(
        model=sherpa_onnx.AudioTaggingModelConfig(
            zipformer=sherpa_onnx.OfflineZipformerAudioTaggingModelConfig(
                model=os.path.join(d, "model.int8.onnx")),
            num_threads=num_threads, provider="cpu"),
        labels=os.path.join(d, "class_labels_indices.csv"), top_k=top_k)
    return sherpa_onnx.AudioTagging(cfg)


def _merge_events(hits, group, gap=2.0):
    """近接した検出を1つの出来事にまとめ [(開始秒, 最大スコア), ...] を返す。

    笑い1回で連続する窓が何度も立つため、そのまま数えるとエフェクトが連発する。
    実装時も同じまとめ方（発火後は gap 秒クールダウン）が要る。
    """
    events = []
    for r in sorted(hits, key=lambda r: r["at"]):
        s = r["groups"][group]
        if events and r["at"] - events[-1][2] <= gap:
            t0, best, _last = events[-1]
            events[-1] = (t0, max(best, s), r["at"])
        else:
            events.append((r["at"], s, r["at"]))
    return [(t0, best) for t0, best, _ in events]


class Tagger:
    """一定間隔で直近N秒を分類し、演出に使えそうな音を拾えるか測る。

    VADの発話境界とは独立に動かす（笑い声はVADに発話と見なされず、
    SenseVoice側には届かないことがあるため）。
    """

    def __init__(self, tagger, min_prob=0.3, log_path=None):
        self.t = tagger
        self.min_prob = min_prob   # グループ合算スコアのしきい値
        self.rows = []
        self.n = 0
        self.t_start = time.time()
        self._lock = threading.Lock()
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None

    @staticmethod
    def group_scores(top):
        """クラス確率 → グループごとの合算スコア（0のグループは落とす）"""
        d = dict(top)
        out = {}
        for g, members in TAG_GROUPS.items():
            s = sum(d.get(m, 0.0) for m in members)
            if s > 0:
                out[g] = round(s, 3)
        return out

    def feed(self, samples, speaker="", at=None):
        with self._lock:
            st = self.t.create_stream()
            st.accept_waveform(SAMPLE_RATE, np.asarray(samples,
                                                       dtype=np.float32))
            evs = self.t.compute(st)
        top = [(e.name, round(float(e.prob), 4)) for e in evs]
        self.n += 1
        at = at if at is not None else time.time() - self.t_start

        groups = self.group_scores(top)
        fired = {g: s for g, s in groups.items() if s >= self.min_prob}

        who = f"{speaker[:6]:<6}" if speaker else ""
        if fired:
            body = "  ".join(f"{g} {s:.2f}" for g, s in
                             sorted(fired.items(), key=lambda kv: -kv[1]))
            detail = "  ".join(f"{n} {p:.2f}" for n, p in top[:3])
            print(f"★[{at:6.1f}s] {who}♪ 【{body}】  {detail}", flush=True)
        else:
            body = "  ".join(f"{n} {p:.2f}" for n, p in top[:3])
            print(f"  [{at:6.1f}s] {who}♪ {body}", flush=True)

        row = {"at": round(at, 2), "speaker": speaker, "top": top,
               "groups": groups, "fired": sorted(fired)}
        self.rows.append(row)
        if self.log:
            self.log.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.log.flush()

    def summary(self):
        print("\n" + "=" * 72)
        print(f"■ 音イベント分類（判定{self.n}回・グループ合算しきい値"
              f"{self.min_prob}）")
        if not self.n:
            return
        speakers = sorted({r["speaker"] for r in self.rows})
        for spk in speakers:
            sel = [r for r in self.rows if r["speaker"] == spk]
            head = f"\n  ＜{spk or '入力'}＞ {len(sel)}判定"
            top1 = Counter(r["top"][0][0] for r in sel if r["top"])
            # 無音ばかりのソースは素材側の問題。まずそれが分かるようにする
            print(head + "   1位: " + "  ".join(
                f"{k} {v / len(sel) * 100:.0f}%" for k, v in top1.most_common(4)))
            for g in TAG_GROUPS:
                hits = [r for r in sel if g in r["fired"]]
                if not hits:
                    continue
                # 1回の笑いは連続する窓で何度も立つ。エフェクトは「1回の出来事に
                # 1回」出したいので、近接した検出はまとめて数える
                events = _merge_events(hits, g)
                best = max(r["groups"][g] for r in hits)
                when = "  ".join(f"{t:.0f}s({s:.2f})" for t, s in events[:10])
                print(f"      {g:<10} {len(events):3d}回"
                      f"（{len(hits)}窓）  最大{best:.2f}   {when}")
            if not any(r["fired"] for r in sel):
                print("      演出に使えそうな音は検出されませんでした")
        if self.log:
            self.log.close()


def build_vad(min_silence_ms, max_utt, threshold=0.5, min_speech=0.25):
    import sherpa_onnx
    c = sherpa_onnx.VadModelConfig()
    c.silero_vad.model = VAD_MODEL_PATH
    c.silero_vad.threshold = threshold
    c.silero_vad.min_silence_duration = min_silence_ms / 1000.0
    c.silero_vad.min_speech_duration = min_speech
    try:
        c.silero_vad.max_speech_duration = max_utt
    except AttributeError:
        pass                     # 古いsherpa-onnxには無い
    c.silero_vad.window_size = WINDOW_SIZE
    c.sample_rate = SAMPLE_RATE
    return sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=30)


class Probe:
    """発話 → 認識 → 感情/イベント抽出 → 表示・集計・記録"""

    def __init__(self, rec, pad=0.0, pad_compare=False, log_path=None):
        self.rec = rec
        self.pad = pad
        self.pad_compare = pad_compare
        self.rows = []
        self.emo = Counter()
        self.ev = Counter()
        self.emo_pad = Counter()      # --pad-compare 時のパディング有り側
        self.t_start = time.time()
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None
        # engine と同じく、単一Recognizerを複数ソースで共有するため直列化する
        self._lock = threading.Lock()

    def _decode(self, samples, pad):
        if pad > 0:
            samples = np.pad(samples, int(pad * SAMPLE_RATE))
        s = self.rec.create_stream()
        s.accept_waveform(SAMPLE_RATE, samples)
        self.rec.decode_stream(s)
        r = s.result
        return (_tag(r.emotion), _tag(r.event), _tag(r.lang), r.text.strip())

    def feed(self, samples, at=None, speaker=""):
        dur = len(samples) / SAMPLE_RATE
        t0 = time.time()
        with self._lock:
            emo, ev, lang, text = self._decode(samples, self.pad)
            alt = self._decode(samples, 0.9 if self.pad == 0.0 else 0.0) \
                if self.pad_compare else None
        rtf = (time.time() - t0) / max(dur, 1e-6)
        if alt:
            self.emo_pad[alt[0]] += 1

        self.emo[emo] += 1
        self.ev[ev] += 1
        at = at if at is not None else time.time() - self.t_start

        hit = emo not in EMO_IDLE or ev not in EV_IDLE
        mark = "★" if hit else "  "
        who = f"{speaker[:6]:<6}" if speaker else ""
        line = (f"{mark}[{at:6.1f}s] {who}{dur:4.1f}s rtf{rtf:5.2f}  "
                f"{emo:<12} {ev:<10} {lang:<4}| {text[:48]}")
        if alt and alt[0] != emo:
            line += f"   (pad違い→{alt[0]})"
        print(line, flush=True)

        row = {"at": round(at, 2), "dur": round(dur, 2), "rtf": round(rtf, 3),
               "speaker": speaker, "emotion": emo, "event": ev,
               "lang": lang, "text": text}
        if alt:
            row["emotion_alt_pad"] = alt[0]
            row["event_alt_pad"] = alt[1]
        self.rows.append(row)
        if self.log:
            self.log.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.log.flush()

    def summary(self):
        n = len(self.rows)
        print("\n" + "=" * 72)
        print(f"発話数: {n}")
        if not n:
            return
        print("\n■ 感情ラベル")
        for k, v in self.emo.most_common():
            print(f"    {k:<14} {v:4d}  ({v / n * 100:5.1f}%)")
        print("\n■ 音声イベント")
        for k, v in self.ev.most_common():
            print(f"    {k:<14} {v:4d}  ({v / n * 100:5.1f}%)")

        if self.pad_compare:
            print("\n■ パディング条件を変えた場合の感情ラベル")
            for k, v in self.emo_pad.most_common():
                print(f"    {k:<14} {v:4d}  ({v / n * 100:5.1f}%)")
            diff = sum(1 for r in self.rows
                       if r.get("emotion_alt_pad") != r["emotion"])
            print(f"    → パディングで判定が変わった発話: {diff}/{n}")

        # 発話長と「感情が出るか」の関係。短い発話ほど出にくい等の傾向を見る
        print("\n■ 発話長 × 感情が出た割合")
        buckets = [(0, 1), (1, 2), (2, 4), (4, 8), (8, 99)]
        for lo, hi in buckets:
            sel = [r for r in self.rows if lo <= r["dur"] < hi]
            if not sel:
                continue
            hits = sum(1 for r in sel if r["emotion"] not in EMO_IDLE)
            print(f"    {lo}-{hi}s: {len(sel):4d}発話中 {hits:3d}件 "
                  f"({hits / len(sel) * 100:5.1f}%)")

        speakers = {r.get("speaker", "") for r in self.rows}
        if len(speakers) > 1:      # コラボ時は話者ごとの発火率も出す
            print("\n■ 話者別")
            for spk in sorted(speakers):
                sel = [r for r in self.rows if r.get("speaker", "") == spk]
                h = sum(1 for r in sel if r["emotion"] not in EMO_IDLE
                        or r["event"] not in EV_IDLE)
                labs = Counter(r["emotion"] for r in sel
                               if r["emotion"] not in EMO_IDLE)
                detail = "  ".join(f"{k}×{v}" for k, v in labs.most_common())
                print(f"    {spk or '(無名)':<8} {len(sel):4d}発話  反応{h:3d}件 "
                      f"({h / len(sel) * 100:5.1f}%)  {detail}")

        hit_rows = [r for r in self.rows if r["emotion"] not in EMO_IDLE
                    or r["event"] not in EV_IDLE]
        if hit_rows:
            print(f"\n■ 反応した発話（エフェクトが出る候補） {len(hit_rows)}件")
            for r in hit_rows[:30]:
                print(f"    [{r['at']:6.1f}s] {r['emotion']:<12}"
                      f"{r['event']:<10}| {r['text'][:40]}")
        rate = len(hit_rows) / n * 100
        print(f"\n→ 発話の {rate:.1f}% で何らかのラベルが立った")
        if self.log:
            self.log.close()


def run_wav(probe, path, vad, every=0.0, tagger=None,
            tag_window=2.0, tag_hop=1.0):
    print(f"[wav] {path}")
    x = load_wav_any(path)
    if tagger is not None:
        n, hop = int(tag_window * SAMPLE_RATE), int(tag_hop * SAMPLE_RATE)
        for i in range(0, max(0, len(x) - n), hop):
            tagger.feed(x[i:i + n], at=i / SAMPLE_RATE)
    if probe is None:
        return
    if every > 0:                       # VAD迂回。一定間隔で機械的に切る
        n = int(every * SAMPLE_RATE)
        for i in range(0, len(x) - n, n):
            probe.feed(x[i:i + n], at=i / SAMPLE_RATE)
        return
    for off in range(0, len(x) - WINDOW_SIZE, WINDOW_SIZE):
        vad.accept_waveform(x[off:off + WINDOW_SIZE])
        while not vad.empty():
            seg = np.array(vad.front.samples, dtype=np.float32)
            vad.pop()
            probe.feed(seg, at=off / SAMPLE_RATE)
    vad.flush()
    while not vad.empty():
        seg = np.array(vad.front.samples, dtype=np.float32)
        vad.pop()
        probe.feed(seg, at=len(x) / SAMPLE_RATE)


def _source_loop(probe, source, speaker, vad, every, stop, rec_path=None,
                 tagger=None, tag_window=2.0, tag_hop=1.0):
    """1入力ソースの取り込み→VAD→認識ループ（engine._stream_loop の縮小版）。

    source: ("mic", デバイス) か ("process", exe名)
    rec_path: 指定すると取り込んだ音をそのままwavに残す（後日の再解析用）
    """
    q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4000)
    rec_f = None
    if rec_path:
        import soundfile as sf
        rec_f = sf.SoundFile(rec_path, "w", samplerate=SAMPLE_RATE,
                             channels=1, subtype="PCM_16")

    def handle_mono(mono):
        # ループバックは可変長で届くので mic と同じ512サンプル単位へ揃える
        mono = np.asarray(mono, dtype=np.float32).reshape(-1)
        for off in range(0, len(mono), WINDOW_SIZE):
            try:
                q.put_nowait(np.array(mono[off:off + WINDOW_SIZE],
                                      dtype=np.float32, copy=True))
            except queue.Full:
                pass

    kind, target = source
    if kind == "process":
        from proc_loopback import ProcessLoopbackCapture
        stream = ProcessLoopbackCapture(target, on_audio=handle_mono)
    else:
        import sounddevice as sd
        dev = None if target in ("", "default", None) else target

        def cb(indata, frames, time_info, status):
            handle_mono(indata[:, 0])

        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="float32", blocksize=WINDOW_SIZE,
                                device=dev, callback=cb)

    buf = np.empty(0, dtype=np.float32)
    chunk_n = int(every * SAMPLE_RATE) if every > 0 else 0
    tag_n, hop_n = int(tag_window * SAMPLE_RATE), int(tag_hop * SAMPLE_RATE)
    tagbuf, since_hop = np.empty(0, dtype=np.float32), 0
    try:
        with stream:
            while not stop.is_set():
                err = getattr(stream, "error", None)
                if err:
                    print(f"[{speaker or 'input'}] 取り込みエラー: {err}",
                          flush=True)
                    return
                try:
                    block = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if rec_f is not None:
                    rec_f.write(block)
                if tagger is not None:
                    # VADとは独立に、直近tag_window秒を一定間隔で分類する
                    tagbuf = np.concatenate([tagbuf, block])[-tag_n:]
                    since_hop += len(block)
                    if since_hop >= hop_n and len(tagbuf) >= tag_n:
                        since_hop = 0
                        tagger.feed(tagbuf, speaker=speaker)
                if probe is None:
                    continue
                buf = np.concatenate([buf, block])
                if chunk_n:                 # VAD迂回モード
                    while len(buf) >= chunk_n:
                        probe.feed(buf[:chunk_n], speaker=speaker)
                        buf = buf[chunk_n:]
                    continue
                while len(buf) >= WINDOW_SIZE:
                    vad.accept_waveform(buf[:WINDOW_SIZE])
                    buf = buf[WINDOW_SIZE:]
                while not vad.empty():
                    seg = np.array(vad.front.samples, dtype=np.float32)
                    vad.pop()
                    probe.feed(seg, speaker=speaker)
    finally:
        if rec_f is not None:
            rec_f.close()


def run_live(probe, sources, vads, every=0.0, rec_prefix=None, tagger=None,
             tag_window=2.0, tag_hop=1.0):
    """複数ソースを同時に回す（コラボ相当）。Ctrl+C まで戻らない。"""
    if probe is None:
        mode = f"音イベント分類のみ（{tag_window}秒窓を{tag_hop}秒ごと）"
    elif every > 0:
        mode = f"{every}秒ごとに機械分割（VAD迂回）"
    else:
        mode = "VADで発話分割"
    recs = []
    for i, ((kind, target), spk) in enumerate(sources):
        path = f"{rec_prefix}_{i}_{spk or kind}.wav" if rec_prefix else None
        recs.append(path)
        print(f"[{spk or 'input'}] {kind}: {target}"
              + (f"  → 録音 {os.path.basename(path)}" if path else ""), flush=True)
    print(f"／ {mode}。Ctrl+C で終了", flush=True)
    print("-" * 72, flush=True)
    stop = threading.Event()
    threads = [threading.Thread(
                   target=_source_loop,
                   args=(probe, src, spk, vad, every, stop, rp,
                         tagger, tag_window, tag_hop),
                   daemon=True)
               for ((src, spk), vad, rp) in zip(sources, vads, recs)]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.2)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wav", help="wav/mp3を解析（省略時はマイク生入力）")
    p.add_argument("--device", default="", help="入力デバイス番号 or 名前")
    p.add_argument("--list", action="store_true", help="入力デバイス一覧")
    p.add_argument("--process", default="",
                   help="このexeの再生音を取り込む（例 chrome.exe）。"
                        "コラボの方式2と同じプロセスループバック")
    p.add_argument("--apps", action="store_true",
                   help="音を出しているアプリ一覧（--process に渡す名前）")
    p.add_argument("--collab", action="store_true",
                   help="マイクと --process を同時に回す（コラボ相当・話者ラベル付き）")
    p.add_argument("--self-name", default="自分")
    p.add_argument("--guest-name", default="ゲスト")
    p.add_argument("--lang", default="ja",
                   help="ja/zh/en/ko/yue/auto（既定 ja）")
    p.add_argument("--pad", type=float, default=0.0,
                   help="認識前の無音パディング秒（既定0＝素の発話）")
    p.add_argument("--pad-compare", action="store_true",
                   help="パディング0秒と0.9秒の両方で判定して差を見る")
    p.add_argument("--silence-ms", type=int, default=300,
                   help="VAD無音判定（engine既定300）")
    p.add_argument("--max-utt", type=float, default=12.0)
    p.add_argument("--vad-threshold", type=float, default=0.5,
                   help="VAD感度。下げると笑い声・相槌も拾う（既定0.5）")
    p.add_argument("--min-speech", type=float, default=0.25,
                   help="この秒数未満の音は捨てる（既定0.25）")
    p.add_argument("--every", type=float, default=0.0,
                   help="VADを使わずN秒ごとに機械分割。笑い声がVADで"
                        "捨てられていないかの切り分け用（例 --every 3）")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--tagging", action="store_true",
                   help="音イベント分類(AudioSet 527クラス)を併走させる。"
                        "笑い・拍手・歓声・歌などを確率つきで拾う")
    p.add_argument("--no-asr", action="store_true",
                   help="SenseVoiceを使わず音イベント分類だけ回す（--tagging前提）")
    p.add_argument("--tag-window", type=float, default=1.5,
                   help="1回の分類に使う直近の秒数（既定1.5。実測でこれが最良。"
                        "3秒だと笑いが薄まってピークが半減する）")
    p.add_argument("--tag-hop", type=float, default=0.5,
                   help="分類する間隔（既定0.5秒ごと）")
    p.add_argument("--tag-min", type=float, default=0.3,
                   help="★を付けるグループ合算スコアのしきい値（既定0.3）")
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--record", action="store_true",
                   help="取り込んだ音を logs/ にwav保存する（条件を変えた再解析用）")
    a = p.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if a.list:
        import engine
        for d in engine.list_input_devices():
            print(f"  {d['index']:3d}  {d['name']}"
                  + ("  ← 既定" if d["default"] else ""))
        return

    if a.apps:
        import proc_loopback
        if not proc_loopback.is_supported():
            print("プロセスループバック非対応のOSです（Windows 10 2004以降が必要）")
            return
        sess = proc_loopback.list_audio_sessions()
        if not sess:
            print("音を出しているアプリが見つかりません（動画を再生してから実行）")
        seen = set()
        for x in sess:
            key = (x["name"], x["pid"])
            if key in seen:
                continue
            seen.add(key)
            print(f"  {x['name']:<28} pid={x['pid']:<7}"
                  + ("  ← 再生中" if x["active"] else ""))
        print("\n※ 同じexeが複数ある場合、--process に pid を直接渡せます")
        return

    if a.no_asr and not a.tagging:
        p.error("--no-asr は --tagging と一緒に使ってください")

    rec = None
    if not a.no_asr:
        import asr_model
        print(f"SenseVoice ロード中（language={a.lang}）…")
        rec = asr_model.load_sensevoice(a.lang, a.threads)

    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    log_path = rec_prefix = None
    if not a.no_log or a.record:
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    if not a.no_log:
        log_path = os.path.join(ROOT, "logs", f"emotion_probe_{stamp}.jsonl")
    if a.record:
        rec_prefix = os.path.join(ROOT, "logs", f"emotion_probe_{stamp}")

    probe = Probe(rec, pad=a.pad, pad_compare=a.pad_compare,
                  log_path=log_path) if rec is not None else None
    tagger = None
    if a.tagging:
        print("音イベント分類 ロード中…")
        tagger = Tagger(load_tagging(a.threads), min_prob=a.tag_min,
                        log_path=(log_path or "").replace(".jsonl", "_tags.jsonl")
                        or None)

    def new_vad():
        return build_vad(a.silence_ms, a.max_utt, a.vad_threshold, a.min_speech)

    dev = int(a.device) if a.device.isdigit() else a.device
    # 数字なら PID 直指定（同名プロセスが多いChrome等で確実に狙う）
    proc = int(a.process) if a.process.isdigit() else a.process
    if a.collab:      # コラボ相当: マイク＝自分 / プロセス＝ゲスト
        if not a.process:
            p.error("--collab には --process chrome.exe が要ります")
        sources = [(("mic", dev), a.self_name),
                   (("process", proc), a.guest_name)]
    elif a.process:
        sources = [(("process", proc), a.guest_name)]
    else:
        sources = [(("mic", dev), "")]

    try:
        if a.wav:
            run_wav(probe, a.wav, new_vad(), a.every, tagger,
                    a.tag_window, a.tag_hop)
        else:
            run_live(probe, sources, [new_vad() for _ in sources], a.every,
                     rec_prefix, tagger, a.tag_window, a.tag_hop)
    except KeyboardInterrupt:
        pass
    if probe is not None:
        probe.summary()
    if tagger is not None:
        tagger.summary()
    if log_path:
        print(f"\n生データ: {log_path}")


if __name__ == "__main__":
    main()
