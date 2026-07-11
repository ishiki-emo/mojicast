"""
字幕エンジン（GUI用）

transcribe_stream.py の疑似ストリーミング処理を、開始/停止できるクラスに再構成。
認識結果はコールバックで通知する（表示は呼び出し側の責務）。

コールバック:
    on_partial(text)       認識途中（生テキスト）
    on_final(text, fid)    確定（単語置換・句読点適用済み）。fid は行の通し番号
    on_level(rms)          マイク入力レベル 0.0-1.0（約100ms間隔）
    on_state(state, detail) loading / ready / running / stopped / error
    on_translation(fid, en) 確定行の英訳（別スレッドで遅れて届く。fid で行に対応）
"""
import os
import time
import queue
import threading
import warnings
from datetime import datetime

import numpy as np

from apppaths import BASE

SAMPLE_RATE = 16000
WINDOW_SIZE = 512
VAD_MODEL_PATH = os.path.join(BASE, "silero_vad.onnx")
BANNED_PATH = os.path.join(BASE, "banned.txt")
GLOSSARY_PATH = os.path.join(BASE, "glossary.txt")

# 初回DLの進捗表示に使う、各モデルのおおよそのDLサイズ（MB）
_MODEL_SIZES_MB = {
    "models--reazon-research--reazonspeech-k2-v2": 739,       # ASR
    "models--tohoku-nlp--bert-base-japanese-char-v3": 702,    # 句読点の土台
    "models--bobfromjapan--bert_japanese_punctuation": 352,   # 句読点の重み
    "models--staka--fugumt-ja-en": 233,                       # 英訳
}


def _hub_dir():
    """HFキャッシュの hub ディレクトリ（凍結時は exe隣 models/hub、開発時はユーザキャッシュ）"""
    home = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface")
    return os.path.join(home, "hub")


def _dir_size_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1e6


class CaptionEngine:
    def __init__(self, on_partial=None, on_final=None,
                 on_level=None, on_state=None, on_translation=None):
        self.on_partial = on_partial or (lambda t: None)
        self.on_final = on_final or (lambda t, fid: None)
        self.on_level = on_level or (lambda v: None)
        self.on_state = on_state or (lambda s, d="": None)
        self.on_translation = on_translation or (lambda fid, en: None)
        self._model = None
        self._model_sig = None      # (precision, hotwords mtime, score) 変更検知
        self._replacer = None
        self._punct = None
        self._translate = None      # 英訳関数（無効時 None。ロード済みなら再利用）
        self._tq = None             # 翻訳ジョブのキュー
        self._tworker = None        # 翻訳ワーカースレッド
        self._fid = 0               # 確定行の通し番号（英訳の対応付け用）
        self._logf = None           # 文字起こしログのファイルハンドル（無効時 None）
        self._mask = None           # 禁止ワードの伏せ字化関数（無効時 None）
        self._gloss = None          # 英訳辞書 [(表記, 英訳)]（無効時 None）
        self._load_warn = ""        # 直近ロードの非致命的警告（英訳/句読点の失敗）
        self._thread = None
        self._stop = threading.Event()
        self.running = False

    # ---------------- 文字起こしログ ----------------

    def _open_log(self, cfg):
        """セッション開始時に logs/日付/日付時刻.log を開く（無効・失敗時は None のまま）"""
        self._logf = None
        if not cfg.get("save_log", True):
            return
        try:
            now = datetime.now()
            d = os.path.join(BASE, "logs", now.strftime("%Y-%m-%d"))
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, now.strftime("%Y-%m-%d_%H%M%S") + ".log")
            self._logf = open(path, "a", encoding="utf-8")
        except OSError:
            self._logf = None       # 書けなくても認識は続行

    def _log_final(self, text):
        """確定行を [発言時刻] 本文 で追記（例外は握りつぶす）"""
        if self._logf is None:
            return
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._logf.write(f"[{ts}] {text}\n")
            self._logf.flush()      # クラッシュしても残るよう都度フラッシュ
        except OSError:
            pass

    def _close_log(self):
        if self._logf is not None:
            try:
                self._logf.close()
            except OSError:
                pass
            self._logf = None

    # ---------------- 禁止ワードの伏せ字化 ----------------

    def _build_masker(self, cfg):
        """banned.txt の語を伏せ字化する関数を返す（語が無ければ None）"""
        try:
            with open(BANNED_PATH, encoding="utf-8") as f:
                words = [ln.strip() for ln in f
                         if ln.strip() and not ln.lstrip().startswith("#")]
        except OSError:
            words = []
        words = sorted(set(words), key=len, reverse=True)  # 長い語から置換
        if not words:
            return None
        ch = (cfg.get("mask_char") or "○").strip() or "○"
        ch = ch[0]                                          # 伏せ字は1文字だけ使う
        def mask(text):
            for w in words:
                if w in text:
                    text = text.replace(w, ch * len(w))     # 文字数ぶん繰り返す
            return text
        return mask

    # ---------------- 英訳辞書（グロッサリ） ----------------

    def _build_glossary(self):
        """glossary.txt（表記,英訳）を読み込む。翻訳前に日本語側で英訳語へ
        置換すると、NMTはラテン文字の固有名詞をそのまま英文へ通すため、
        固有名詞の訳を固定できる。無ければ None。"""
        pairs = []
        try:
            with open(GLOSSARY_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "," not in line:
                        continue
                    ja, en = line.split(",", 1)             # 英訳側の,も許容
                    ja, en = ja.strip(), en.strip()
                    if ja and en:
                        pairs.append((ja, en))
        except OSError:
            return None
        if not pairs:
            return None
        pairs.sort(key=lambda p: len(p[0]), reverse=True)   # 長い表記から置換
        return pairs

    # ---------------- モデルロード ----------------

    def _expected_download_mb(self, cfg):
        """この設定で未キャッシュのモデルの合計DLサイズ(MB)と、DLが要るかを返す"""
        hub = _hub_dir()
        need = ["models--reazon-research--reazonspeech-k2-v2"]
        if cfg.get("punctuate", True):
            need += ["models--tohoku-nlp--bert-base-japanese-char-v3",
                     "models--bobfromjapan--bert_japanese_punctuation"]
        if cfg.get("translate", False):
            need += ["models--staka--fugumt-ja-en"]
        total = sum(_MODEL_SIZES_MB[m] for m in need
                    if not os.path.isdir(os.path.join(hub, m)))
        return total, total > 0

    def _dl_monitor(self, total_mb, stop_evt):
        """DL中の models/ の増加量を監視し、進捗をGUIへ流す（別スレッド）"""
        hub = _hub_dir()
        start = _dir_size_mb(hub)
        while not stop_evt.wait(0.5):
            done = max(0.0, _dir_size_mb(hub) - start)
            pct = min(99, int(done / total_mb * 100)) if total_mb > 0 else 0
            self.on_state("loading",
                          f"初回のみ: モデルをダウンロード中... "
                          f"{done:.0f} / 約{total_mb:.0f}MB ({pct}%)")

    def _load(self, cfg):
        """設定に応じてモデル群をロード（変更が無ければ再利用）"""
        self._load_warn = ""    # このロードでの非致命的な警告（英訳/句読点の失敗など）
        hw_file = os.path.join(BASE, "hotwords.txt") if cfg.get("use_hotwords", True) else ""
        hw_mtime = os.path.getmtime(hw_file) if hw_file and os.path.exists(hw_file) else 0
        sig = (cfg.get("precision", "int8-fp32"), hw_file, hw_mtime,
               cfg.get("hotwords_score", 2.0))

        # 各モデルの「ロードが要るか」を個別判定（ASRの再利用判定に引きずられない）。
        # 一度ロードしたモデルは保持し、ON/OFFの実効切替は _run 側が cfg で行う。
        reload_asr = self._model is None or sig != self._model_sig
        need_punct = cfg.get("punctuate", True) and self._punct is None
        need_trans = cfg.get("translate", False) and self._translate is None
        if not (reload_asr or need_punct or need_trans):
            return  # ロード対象なし（設定のON/OFFは次の _run で即反映）

        # 軽量版はモデルが無ければ初回だけ自動DLされる。その進捗をGUIへ流す。
        total_mb, need_dl = self._expected_download_mb(cfg)
        stop_evt = threading.Event()
        mon = None
        if need_dl:
            self.on_state("loading",
                          f"初回のみ: モデルをダウンロード中... (約{total_mb:.0f}MB)")
            mon = threading.Thread(target=self._dl_monitor,
                                   args=(total_mb, stop_evt), daemon=True)
            mon.start()

        try:
            if reload_asr:
                self.on_state("loading", "認識モデルをロード中...")
                from asr_model import load_model
                hw_path = ""
                self._replacer = None
                if hw_file and os.path.exists(hw_file):
                    from vocab import parse_vocab, write_hotwords, build_replacer
                    entries = parse_vocab(hw_file)
                    if entries:
                        # 固定パスに書き出す（mkstempだと毎回%TEMP%に溜まるため）
                        hw_path = write_hotwords(
                            entries, os.path.join(BASE, "_hotwords_gen.txt"))
                        self._replacer = build_replacer(entries)
                self._model = load_model(
                    device="cpu", hotwords_file=hw_path,
                    hotwords_score=cfg.get("hotwords_score", 2.0),
                    precision=cfg.get("precision", "int8-fp32"))
                self._model_sig = sig

            # 句読点・英訳は「付加機能」。ロードに失敗しても ASR（本体）は落とさず、
            # その機能だけ無効にして続行する（＝日本語字幕は出続ける）。
            if need_punct:
                self.on_state("loading", "句読点モデルをロード中...")
                try:
                    from punct import add_punctuation, load_punctuator
                    load_punctuator()
                    self._punct = add_punctuation
                except Exception:
                    self._punct = None
                    self._load_warn = "句読点の読み込みに失敗"

            if need_trans:
                self.on_state("loading", "翻訳モデル(英訳)をロード中...")
                try:
                    from translate import translate as _tr, load_translator
                    load_translator()
                    self._translate = _tr
                except Exception:
                    self._translate = None
                    self._load_warn = "英訳の読み込みに失敗（英訳なしで続行）"
        finally:
            stop_evt.set()
            if mon is not None:
                mon.join(timeout=2)

    def _build_vad(self, min_silence_ms):
        import sherpa_onnx
        c = sherpa_onnx.VadModelConfig()
        c.silero_vad.model = VAD_MODEL_PATH
        c.silero_vad.threshold = 0.5
        c.silero_vad.min_silence_duration = min_silence_ms / 1000.0
        c.silero_vad.min_speech_duration = 0.25
        c.silero_vad.window_size = WINDOW_SIZE
        c.sample_rate = SAMPLE_RATE
        return sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=30)

    def _recognize(self, samples):
        from reazonspeech.k2.asr import transcribe, audio_from_numpy
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return transcribe(self._model,
                              audio_from_numpy(samples, SAMPLE_RATE)).text.strip()

    # ---------------- 開始 / 停止 ----------------

    def start(self, cfg: dict):
        """認識を開始する（別スレッド）。cfg はGUIの設定辞書"""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(cfg,), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _translate_loop(self):
        """確定行を順に英訳する（認識ループとは別スレッド）"""
        q = self._tq
        while True:
            item = q.get()
            if item is None:      # 停止サインで終了
                break
            fid, text = item
            # 英訳辞書: 翻訳前に日本語側で英訳語へ置換（固有名詞の訳を固定）
            if self._gloss:
                for ja, en_word in self._gloss:
                    if ja in text:
                        text = text.replace(ja, en_word)
            try:
                en = self._translate(text) if self._translate else ""
            except Exception:
                en = ""           # 翻訳失敗は無視（字幕本体は出続ける）
                self._log_translate_error(text)
            if en:
                self.on_translation(fid, en)

    def _log_translate_error(self, text):
        """英訳ワーカーで起きた例外を translate_error.log に残す（無言失敗の可視化）"""
        try:
            import traceback
            with open(os.path.join(BASE, "translate_error.log"),
                      "a", encoding="utf-8") as f:
                f.write(f"--- 英訳失敗: {text!r}\n")
                f.write(traceback.format_exc() + "\n")
        except OSError:
            pass

    def _stop_translate_worker(self):
        if self._tq is not None:
            self._tq.put(None)
        self._tworker = None
        self._tq = None

    def _run(self, cfg):
        try:
            self._load(cfg)
        except Exception as e:  # モデル・単語帳のエラーをGUIへ
            info = (type(e).__name__ + " " + str(e)).lower()
            netish = any(k in info for k in (
                "connection", "download", "resolve", "timeout", "network",
                "getaddrinfo", "maxretry", "httperror", "localentrynotfound",
                "offline", "temporarily", "ssl"))
            if netish:
                self.on_state("error", "モデルのダウンロードに失敗しました。"
                              "ネット接続を確認して、もう一度 ▶開始 してください"
                              "（ダウンロード済みの分は続きから取得します）")
            else:
                self.on_state("error", f"ロード失敗: {e}")
            return

        # 英訳ワーカー起動（この設定で有効なときだけ）。確定行を別スレッドで翻訳し認識を止めない
        translate_on = cfg.get("translate", False) and self._translate is not None
        self._tq = queue.Queue()
        self._tworker = None
        if translate_on:
            self._tworker = threading.Thread(target=self._translate_loop,
                                             daemon=True)
            self._tworker.start()

        self._open_log(cfg)             # 文字起こしログをこのセッション用に開く
        self._mask = self._build_masker(cfg)   # 禁止ワードの伏せ字化を用意
        self._gloss = self._build_glossary()   # 英訳辞書（固有名詞の訳を固定）

        try:
            import sounddevice as sd
            vad = self._build_vad(cfg.get("silence_ms", 300))
            audio_q: "queue.Queue[np.ndarray]" = queue.Queue()
            last_level_t = [0.0]

            def callback(indata, frames, time_info, status):
                mono = indata[:, 0].copy()
                audio_q.put(mono)
                now = time.time()
                if now - last_level_t[0] >= 0.1:   # レベルは約10回/秒に間引き
                    last_level_t[0] = now
                    rms = float(np.sqrt(np.mean(mono ** 2)))
                    self.on_level(min(1.0, rms * 8))

            interval_samples = int(cfg.get("interval", 0.4) * SAMPLE_RATE)
            max_samples = int(cfg.get("max_utt", 12.0) * SAMPLE_RATE)
            device = cfg.get("device", None)
            if device in ("", "default"):
                device = None

            buffer = np.empty(0, dtype=np.float32)
            last_partial_len = 0

            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="float32", blocksize=WINDOW_SIZE,
                                device=device, callback=callback):
                self.running = True
                self.on_state("running",
                              "認識中" + (f"  ※{self._load_warn}" if self._load_warn else ""))
                while not self._stop.is_set():
                    try:
                        block = audio_q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    buffer = np.concatenate([buffer, block])
                    while len(buffer) >= WINDOW_SIZE:
                        vad.accept_waveform(buffer[:WINDOW_SIZE])
                        buffer = buffer[WINDOW_SIZE:]

                    # 確定した発話
                    while not vad.empty():
                        samples = np.array(vad.front.samples, dtype=np.float32)
                        vad.pop()
                        text = self._recognize(samples)
                        if text:
                            if self._replacer is not None:
                                text = self._replacer(text)
                            if self._punct is not None and cfg.get("punctuate", True):
                                text = self._punct(text)
                            if self._mask is not None:      # 禁止ワードを伏せ字化
                                text = self._mask(text)
                        if text:
                            self._fid += 1
                            self.on_final(text, self._fid)
                            self._log_final(text)
                            if translate_on:
                                self._tq.put((self._fid, text))
                        else:
                            # 後処理で空になった発話は出さず、薄文字だけ消す
                            self.on_partial("")
                        last_partial_len = 0

                    # 発話中の途中経過
                    if vad.is_speech_detected():
                        cur = np.array(vad.current_segment.samples,
                                       dtype=np.float32)
                        if (len(cur) - last_partial_len >= interval_samples
                                and len(cur) >= int(0.3 * SAMPLE_RATE)):
                            last_partial_len = len(cur)
                            p = self._recognize(cur)
                            if self._mask is not None:      # 認識中(薄文字)も伏せ字化
                                p = self._mask(p)
                            self.on_partial(p)
                        if len(cur) >= max_samples:
                            vad.flush()
        except Exception as e:
            self.running = False
            self._stop_translate_worker()
            self._close_log()
            self.on_state("error", f"実行エラー: {e}")
            return
        self.running = False
        self._stop_translate_worker()
        self._close_log()
        self.on_level(0.0)
        self.on_partial("")
        self.on_state("stopped", "停止しました")


def list_input_devices():
    """入力デバイス一覧を [{index, name, default}] で返す

    Windowsでは同じマイクがMME/DirectSound/WASAPI等で重複して並ぶため、
    既定入力デバイスと同じホストAPIのものに絞って返す。
    """
    import sounddevice as sd
    out = []
    try:
        default_idx = sd.default.device[0]
        default_api = sd.query_devices(default_idx)["hostapi"] \
            if default_idx is not None and default_idx >= 0 else None
    except Exception:
        default_idx, default_api = -1, None
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) <= 0:
            continue
        if default_api is not None and d.get("hostapi") != default_api:
            continue
        out.append({"index": i, "name": d["name"],
                    "default": i == default_idx})
    return out
