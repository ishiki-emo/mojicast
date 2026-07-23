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
from datetime import datetime

import numpy as np

from apppaths import BASE
import wordstore
from numnorm import normalize_numbers

SAMPLE_RATE = 16000
WINDOW_SIZE = 512
PARTIAL_WINDOW_SEC = 6.0   # 途中経過(薄文字)がデコードする末尾の秒数
VAD_MODEL_PATH = os.path.join(BASE, "silero_vad.onnx")

# 過負荷やデバイス異常時にもメモリを無制限に使わないための待ち行列上限。
# 音声は約30秒、翻訳は128発話ぶんを保持する。通常配信では到達しない余裕を
# 持たせ、到達時だけ古い待ちデータを整理して「ライブ」へ追いつかせる。
AUDIO_QUEUE_MAX_BLOCKS = max(1, int(30 * SAMPLE_RATE / WINDOW_SIZE))
AUDIO_QUEUE_RECOVER_BLOCKS = AUDIO_QUEUE_MAX_BLOCKS // 2
TRANSLATION_QUEUE_MAX_ITEMS = 128
TRANSLATION_QUEUE_RECOVER_ITEMS = 96

# 初回DLの進捗表示に使う、各モデルのおおよそのDLサイズ（MB）
_MODEL_SIZES_MB = {
    "asr": 739,           # ReazonSpeech k2 v2
    "asr_sv": 240,        # SenseVoice small（int8＋語彙）
    "punct": 364,         # 句読点BERT（ONNX変換済み・単一ファイル）
    "translate": 124,     # FuguMT（CTranslate2変換済み＋SentencePiece）
    "translate_zh": 470,  # M2M-100 418M（CT2 int8＋SentencePiece）
}


def _offer_bounded_latest(q, item, recover_to):
    """上限までは通常追加し、満杯時だけ古い項目を整理して最新を入れる。

    戻り値は破棄した項目数。リアルタイム処理のproducerをブロックしない。
    """
    try:
        q.put_nowait(item)
        return 0
    except queue.Full:
        pass

    target = max(0, min(int(recover_to), q.maxsize - 1))
    dropped = 0
    while q.qsize() > target:
        try:
            q.get_nowait()
            dropped += 1
        except queue.Empty:
            break

    try:
        q.put_nowait(item)
    except queue.Full:
        # 複数producerが同時に補充した場合は、今回の最新項目を無理に
        # ブロックしてまで入れない。いずれにせよメモリ上限は維持される。
        dropped += 1
    return dropped


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
        self.on_partial = on_partial or (lambda t, spk="": None)
        self.on_final = on_final or (lambda t, fid, spk="": None)
        self.on_level = on_level or (lambda v, spk="": None)
        self.on_state = on_state or (lambda s, d="": None)
        self.on_translation = on_translation or (lambda fid, en: None)
        self._rec_lock = threading.Lock()   # 単一Recognizerへの decode を直列化（2話者共有）
        self._fid_lock = threading.Lock()   # fid採番の排他（話者をまたいで一意に）
        self._translate_on = False
        self._stream_error = None           # ストリームスレッドで起きた致命的エラー
        self._model = None
        self._model_sig = None      # (precision, hotwords mtime, score) 変更検知
        self._replacer = None
        self._punct = None
        self._translate = None      # 翻訳関数（無効時 None。ロード済みなら再利用）
        self._translate_sig = None  # ロード済み翻訳経路 (engine, src, tgt)
        self._asr_caps = {"hotwords": True, "punct": False,
                          "spaces": False, "multilang": False}  # 既定=k2
        self._tq = None             # 翻訳ジョブのキュー
        self._tworker = None        # 翻訳ワーカースレッド
        self._fid = 0               # 確定行の通し番号（英訳の対応付け用）
        self._logf = None           # 文字起こしログのファイルハンドル（無効時 None）
        self._mask = None           # 禁止ワードの伏せ字化関数（無効時 None）
        self._gloss = None          # 英訳辞書 [(表記, 英訳)]（無効時 None）
        self._load_warn = ""        # 直近ロードの非致命的警告（英訳/句読点の失敗）
        self._thread = None
        self._stop = threading.Event()
        # running だけではモデルロード中を表現できず、同時に start() が来ると
        # 複数のモデルロードスレッドを起動できてしまう。ライフサイクル全体を
        # 同じロックで管理し、開始・停止要求を直列化する。
        self._state_lock = threading.RLock()
        self._lifecycle_state = "stopped"
        self.running = False
        self.perf = self._fresh_perf()   # デコード計測（/api/perf 用）

    @staticmethod
    def _fresh_perf():
        return {"partial_n": 0, "partial_ms": 0.0,
                "final_n": 0, "final_ms": 0.0, "since": time.time()}

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

    def _log_final(self, text, speaker=""):
        """確定行を [発言時刻] (話者) 本文 で追記（例外は握りつぶす）"""
        if self._logf is None:
            return
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            who = f"[{speaker}] " if speaker else ""
            self._logf.write(f"[{ts}] {who}{text}\n")
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
        """禁止ワード（共通＋プロファイル合成）を伏せ字化する関数を返す（語が無ければ None）"""
        words = wordstore.merged_banned(cfg.get("word_profile", ""))
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

    def _build_glossary(self, cfg):
        """英訳辞書（共通＋プロファイル合成）を読み込む。翻訳前に日本語側で英訳語へ
        置換すると、NMTはラテン文字の固有名詞をそのまま英文へ通すため、
        固有名詞の訳を固定できる。無ければ None。"""
        pairs = wordstore.merged_glossary(cfg.get("word_profile", ""))
        if not pairs:
            return None
        pairs.sort(key=lambda p: len(p[0]), reverse=True)   # 長い表記から置換
        return pairs

    # ---------------- モデルロード ----------------

    def _log_load_error(self, name):
        """付加機能（句読点/英訳）のロード失敗時に traceback を logs/ へ残す。
        GUIには短い警告しか出せないため、テスターからの報告調査はこのログで行う。"""
        try:
            import traceback
            d = os.path.join(BASE, "logs")
            os.makedirs(d, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(os.path.join(d, "load_error.log"), "a",
                      encoding="utf-8") as f:
                f.write(f"[{ts}] {name} のロードに失敗\n")
                f.write(traceback.format_exc() + "\n")
        except OSError:
            pass                    # ログが書けなくても本体は続行

    def _translate_plan(self, cfg):
        """cfg から翻訳経路を決める → ("fugumt"|"m2m", 原文言語, 翻訳先) / 不要なら None

        原文言語は通常 ja。SenseVoiceで認識言語を明示している場合はそれに合わせる
        （例: 中国語認識＋英訳 → M2Mの zh→en）。原文=翻訳先のときは None（翻訳不要）。
        """
        if not cfg.get("translate", False):
            return None
        tgt = cfg.get("translate_lang", "en")
        src = "ja"
        if cfg.get("asr_model", "k2-ja") == "sensevoice":
            al = cfg.get("asr_lang", "auto")
            if al not in ("auto", "ja"):
                src = al
        if src == "yue":
            src = "zh"   # M2M-100は広東語非対応。書き起こしは中文字なのでzh扱いで翻訳
        if src == tgt:
            return None
        # SenseVoiceの中国語認識結果を台湾／香港表記へ整えるだけなら、
        # 翻訳モデルを通さずOpenCCの地域表記変換だけを使う。
        if src == "zh" and tgt in ("zh_tw", "zh_hk"):
            return ("opencc", src, tgt)
        eng = "fugumt" if (src, tgt) == ("ja", "en") else "m2m"
        return (eng, src, tgt)

    def _expected_download_mb(self, cfg):
        """この設定で未キャッシュのモデルの合計DLサイズ(MB)と、DLが要るかを返す"""
        total = 0
        import asr_model
        model_id = cfg.get("asr_model", "k2-ja")
        caps = asr_model.MODELS.get(model_id, asr_model.MODELS["k2-ja"])["caps"]
        if model_id == "sensevoice":
            if not asr_model.cached("sensevoice"):
                total += _MODEL_SIZES_MB["asr_sv"]
        elif not os.path.isdir(os.path.join(
                _hub_dir(), "models--reazon-research--reazonspeech-k2-v2")):
            total += _MODEL_SIZES_MB["asr"]
        # 句読点内蔵モデル（SenseVoice等）ではBERTを使わないためDLも不要
        if cfg.get("punctuate", True) and not caps["punct"]:
            import punct
            if not punct.cached():
                total += _MODEL_SIZES_MB["punct"]
        plan = self._translate_plan(cfg)
        if plan:
            import translate
            if plan[0] == "m2m":
                if not translate.cached_zh():
                    total += _MODEL_SIZES_MB["translate_zh"]
            elif plan[0] == "fugumt" and not translate.cached():
                total += _MODEL_SIZES_MB["translate"]
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
        # ホットワードは共通＋使用中プロファイルの合成（内容が変わればASRを再ロード）
        hw_entries = (wordstore.merged_hotwords(cfg.get("word_profile", ""))
                      if cfg.get("use_hotwords", True) else [])
        sig = (cfg.get("precision", "int8-fp32"), tuple(hw_entries),
               cfg.get("hotwords_score", 2.0),
               cfg.get("asr_model", "k2-ja"), cfg.get("asr_lang", "auto"))

        # 各モデルの「ロードが要るか」を個別判定（ASRの再利用判定に引きずられない）。
        # 一度ロードしたモデルは保持し、ON/OFFの実効切替は _run 側が cfg で行う。
        from asr_model import MODELS
        model_id = cfg.get("asr_model", "k2-ja")
        model_caps = MODELS.get(model_id, MODELS["k2-ja"])["caps"]
        reload_asr = self._model is None or sig != self._model_sig
        # 句読点内蔵モデル（SenseVoice等）ではBERTを使わない。ロードを省き、
        # モデル切替で不要になった常駐BERTはここで解放する（メモリ返却）
        if model_caps["punct"] and self._punct is not None:
            import punct
            punct.unload()
            self._punct = None
        need_punct = (cfg.get("punctuate", True)
                      and not model_caps["punct"] and self._punct is None)
        plan = self._translate_plan(cfg)
        need_trans = plan is not None and self._translate_sig != plan
        if cfg.get("translate", False) and plan is None:
            # 認識言語と翻訳先が同じ（例: 中国語認識＋中国語訳）→ 翻訳は無意味
            self._translate = None
            self._translate_sig = None
            self._load_warn = "認識言語と翻訳先が同じため、翻訳はスキップされます"
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
                from asr_model import load_by_config
                hw_path = ""
                self._replacer = None
                if hw_entries:
                    from vocab import write_hotwords, build_replacer
                    # 表記置換はどのモデルでも有効。認識誘導（hotwords_file）は
                    # transducer系（k2）のみ対応
                    self._replacer = build_replacer(hw_entries)
                    if model_caps["hotwords"]:
                        # 固定パスに書き出す（mkstempだと毎回%TEMP%に溜まるため）
                        hw_path = write_hotwords(
                            hw_entries, wordstore.data_path("_hotwords_gen.txt"))
                self._model, self._asr_caps = load_by_config(
                    model_id, hotwords_file=hw_path,
                    hotwords_score=cfg.get("hotwords_score", 2.0),
                    precision=cfg.get("precision", "int8-fp32"),
                    language=cfg.get("asr_lang", "auto"))
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
                    self._log_load_error("句読点モデル")

            if need_trans:
                eng, src, tgt = plan
                label = {"en": "英訳", "zh": "中国語（簡体字）訳",
                         "zh_tw": "中国語（台湾繁体字）訳",
                         "zh_hk": "中国語（香港繁体字）訳",
                         "ja": "日本語訳", "ko": "韓国語訳",
                         "id": "インドネシア語訳"}.get(tgt, f"{tgt}訳")
                self.on_state("loading", f"翻訳モデル({label})をロード中...")
                try:
                    if eng == "fugumt":
                        from translate import translate as _tr, load_translator
                        load_translator()
                        self._translate = _tr
                    elif eng == "opencc":
                        from translate import convert_zh_variant
                        # 初回変換をここで行い、依存ファイル不足を字幕開始前に検出する。
                        convert_zh_variant("测试", tgt)
                        self._translate = (lambda t, _t=tgt:
                                           convert_zh_variant(t, _t))
                    else:
                        from translate import translate_m2m, load_translator_zh
                        load_translator_zh()
                        self._translate = (lambda t, _s=src, _t=tgt:
                                           translate_m2m(t, _s, _t))
                    self._translate_sig = plan
                    # 切替で使わなくなった側の翻訳バックエンドを解放（メモリ返却）
                    from translate import unload as unload_translator
                    if eng != "fugumt":
                        unload_translator("fugumt")
                    if eng != "m2m":
                        unload_translator("m2m")
                except Exception:
                    self._translate = None
                    self._translate_sig = None
                    self._load_warn = f"{label}の読み込みに失敗（翻訳なしで続行）"
                    self._log_load_error(f"翻訳モデル({label})")
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

    # 上流 reazonspeech.k2.asr.transcribe と同じ無音パディング（k2の認識精度に効く）。
    # ラッパー2関数のためだけに librosa→sklearn/scipy 系が配布物に入っていたため、
    # 実体（パディング＋sherpa_onnxデコード）をここへインライン化した。
    PAD_SECONDS = 0.9

    def _recognize(self, samples):
        # 単一Recognizerを2話者で共有するため decode を直列化（交互会話ならほぼ待ち無し）
        with self._rec_lock:
            padded = np.pad(samples, int(self.PAD_SECONDS * SAMPLE_RATE))
            stream = self._model.create_stream()
            stream.accept_waveform(SAMPLE_RATE, padded)
            self._model.decode_stream(stream)
            return stream.result.text.strip()

    def _next_fid(self):
        with self._fid_lock:
            self._fid += 1
            return self._fid

    # ---------------- 開始 / 停止 ----------------

    @property
    def lifecycle_state(self):
        """内部ライフサイクル状態（stopped/starting/running/stopping/error）。"""
        with self._state_lock:
            return self._lifecycle_state

    def _set_lifecycle(self, state):
        with self._state_lock:
            self._lifecycle_state = state
            self.running = state == "running"

    def _finish_session(self, state, detail):
        """セッションの最終状態を確定してGUIへ通知する。"""
        self._set_lifecycle(state)
        self.on_state(state, detail)

    def _run_after_start_notice(self, cfg, gate):
        """start() の状態通知が完了してからセッション本体を動かす。"""
        gate.wait()
        self._run(cfg)

    def start(self, cfg: dict):
        """認識を開始する（別スレッド）。

        開始済み・ロード中・停止処理中の要求は安全なno-opとして False を返す。
        """
        with self._state_lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                return False
            if self._lifecycle_state in ("starting", "running", "stopping"):
                return False

            self._stop.clear()
            self._lifecycle_state = "starting"
            self.running = False
            gate = threading.Event()
            thread = threading.Thread(
                target=self._run_after_start_notice,
                args=(dict(cfg), gate), daemon=True,
                name="CaptionEngine",
            )
            self._thread = thread

            try:
                # 先にスレッドを生存状態にすることで、同時に来た stop() が
                # 「まだスレッド無し」と誤認して次の start() を許す隙間をなくす。
                thread.start()
            except Exception:
                gate.set()
                if self._thread is thread:
                    self._thread = None
                self._lifecycle_state = "error"
                self.running = False
                self.on_state("error", "認識処理を開始できませんでした")
                raise

            try:
                self.on_state("loading", "起動準備中...")
            finally:
                gate.set()
        return True

    def stop(self, timeout=5):
        """停止を要求する。

        timeout 内にモデルロード等が終わらなくてもスレッド参照を捨てない。
        生きている旧セッションが終了するまでは start() が次を起動できないため、
        ロードの二重化を防げる。停止完了なら True、処理継続中なら False。
        """
        with self._state_lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._thread = None
                already_stopped = self._lifecycle_state == "stopped"
                self._lifecycle_state = "stopped"
                self.running = False
                self._stop.set()
                notify_stopped = not already_stopped
            else:
                notify_stopped = False
                notify = self._lifecycle_state != "stopping"
                self._lifecycle_state = "stopping"
                self.running = False
                self._stop.set()

        if notify_stopped:
            self.on_state("stopped", "停止しました")
        if thread is None or not thread.is_alive():
            return True

        if notify:
            self.on_state("stopping", "停止処理中...")

        # コールバックから stop() された場合に自分自身を join しない。
        if thread is threading.current_thread():
            return False
        thread.join(timeout=max(0, timeout))
        return not thread.is_alive()

    def _translate_loop(self):
        """確定行を順に英訳する（認識ループとは別スレッド）"""
        q = self._tq
        while True:
            item = q.get()
            if item is None:      # 停止サインで終了
                break
            fid, text = item
            # 英訳辞書: 翻訳前に日本語側で英訳語へ置換（固有名詞の訳を固定）。
            # 適用は日→英（FuguMT）のみ。他方向では英単語を注入してしまうため
            if self._gloss and (self._translate_sig or ("",))[0] == "fugumt":
                for ja, en_word in self._gloss:
                    if ja in text:
                        text = text.replace(ja, en_word)
            try:
                en = self._translate(text) if self._translate else ""
            except Exception:
                en = ""           # 翻訳失敗は無視（字幕本体は出続ける）
                self._log_translate_error(text)
            if en and self._translate_on:
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
        # セッション終了後に古い翻訳を延々と消化すると、次回開始時のworkerと
        # 重なり得る。未処理分は捨て、現在処理中の1件だけ完了を待って終了する。
        self._translate_on = False
        q = self._tq
        worker = self._tworker
        if q is not None:
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            q.put_nowait(None)
        if (worker is not None and worker.is_alive()
                and worker is not threading.current_thread()):
            worker.join()
        if self._tworker is worker:
            self._tworker = None
        if self._tq is q:
            self._tq = None

    def _resolve_sources(self, cfg):
        """入力ソースを決める。 [(device, speaker, is_primary), ...]
        通常は1本（話者ラベル空＝従来どおり）。1対1コラボ時のみ2本目（相手）を足す。
        相手のソースは2方式: 入力デバイス（仮想ケーブル） or
        ("process", exe名) のプロセスループバック（方式2・仮想ケーブル不要）。"""
        dev = cfg.get("device", None)
        if cfg.get("collab"):
            guest_src = None
            if cfg.get("collab_source", "process") == "process":
                pname = (cfg.get("collab_process") or "").strip()
                if pname:
                    guest_src = ("process", pname)
            if guest_src is None:      # デバイス方式 or プロセス未指定のフォールバック
                cdev = cfg.get("collab_device", None)
                if cdev not in (None, "", "default"):
                    guest_src = cdev
            if guest_src is not None:
                self_name = (cfg.get("self_name") or "自分").strip() or "自分"
                guest_name = (cfg.get("guest_name") or "ゲスト").strip() or "ゲスト"
                return [(dev, self_name, True), (guest_src, guest_name, False)]
        return [(dev, "", True)]

    def _stream_loop(self, cfg, device, speaker, is_primary):
        """1入力ソースの取り込み→VAD→認識ループ（話者ラベル付き）。ソースごとに1スレッド。
        認識は共有Recognizerを _recognize 内のロックで直列化する。"""
        try:
            import sounddevice as sd
            vad = self._build_vad(cfg.get("silence_ms", 300))
            audio_q: "queue.Queue[np.ndarray]" = queue.Queue(
                maxsize=AUDIO_QUEUE_MAX_BLOCKS)
            last_level_t = [0.0]

            def handle_mono(mono):
                mono = np.asarray(mono, dtype=np.float32).reshape(-1)
                # ループバックは可変長で届くため、micと同じ512sample単位へ
                # 揃えて「約30秒」の上限が入力方式に左右されないようにする。
                for offset in range(0, len(mono), WINDOW_SIZE):
                    chunk = np.array(
                        mono[offset:offset + WINDOW_SIZE],
                        dtype=np.float32, copy=True)
                    _offer_bounded_latest(
                        audio_q, chunk, AUDIO_QUEUE_RECOVER_BLOCKS)
                if is_primary:   # レベルメーターは主入力（自分のマイク）だけ流す
                    now = time.time()
                    if len(mono) and now - last_level_t[0] >= 0.1:
                        last_level_t[0] = now
                        rms = float(np.sqrt(np.mean(mono ** 2)))
                        self.on_level(min(1.0, rms * 8), speaker)

            def callback(indata, frames, time_info, status):
                handle_mono(indata[:, 0].copy())

            interval_samples = int(cfg.get("interval", 0.4) * SAMPLE_RATE)
            max_samples = int(cfg.get("max_utt", 12.0) * SAMPLE_RATE)

            # 入力ストリームを開く: ("process", exe名) → プロセスループバック（方式2）
            #                       それ以外 → 通常の入力デバイス
            if isinstance(device, tuple) and device[0] == "process":
                from proc_loopback import ProcessLoopbackCapture
                stream = ProcessLoopbackCapture(device[1], on_audio=handle_mono)
            else:
                dev = None if device in ("", "default") else device
                stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                        dtype="float32", blocksize=WINDOW_SIZE,
                                        device=dev, callback=callback)

            buffer = np.empty(0, dtype=np.float32)
            last_partial_len = 0
            partial_gap = interval_samples   # 次の途中経過までに要る新規音声量（適応）

            with stream:
                while not self._stop.is_set():
                    err = getattr(stream, "error", None)
                    if err:                      # ループバック側の実行時エラー
                        raise RuntimeError(err)
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
                        t0 = time.time()
                        text = self._recognize(samples)
                        self.perf["final_n"] += 1
                        self.perf["final_ms"] += (time.time() - t0) * 1000
                        if text:
                            if self._asr_caps.get("spaces"):
                                from asr_model import strip_cjk_spaces
                                text = strip_cjk_spaces(text)
                            if self._replacer is not None:
                                text = self._replacer(text)
                            # SenseVoice系は句読点・数字正規化(ITN)を内蔵しているため
                            # 後段のnumnorm/BERTはスキップ（日本語以外にBERTは使えない）
                            if (cfg.get("num_arabic", True)
                                    and not self._asr_caps.get("punct")):
                                text = normalize_numbers(text)   # 三十五 → 35
                            if (self._punct is not None
                                    and cfg.get("punctuate", True)
                                    and not self._asr_caps.get("punct")):
                                text = self._punct(text)
                            if self._mask is not None:      # 禁止ワードを伏せ字化
                                text = self._mask(text)
                        if text:
                            fid = self._next_fid()
                            self.on_final(text, fid, speaker)
                            self._log_final(text, speaker)
                            if self._translate_on:
                                _offer_bounded_latest(
                                    self._tq, (fid, text),
                                    TRANSLATION_QUEUE_RECOVER_ITEMS)
                        else:
                            # 後処理で空になった発話は出さず、薄文字だけ消す
                            self.on_partial("", speaker)
                        last_partial_len = 0
                        partial_gap = interval_samples   # 新しい発話は素早く出す

                    # 発話中の途中経過
                    if vad.is_speech_detected():
                        cur = np.array(vad.current_segment.samples,
                                       dtype=np.float32)
                        if (len(cur) - last_partial_len >= partial_gap
                                and len(cur) >= int(0.3 * SAMPLE_RATE)):
                            last_partial_len = len(cur)
                            # 薄文字は末尾だけデコード（長発話でも1回のコストを一定に。
                            # 確定字幕は従来どおり発話全体をデコードするので精度不変）
                            win = int(PARTIAL_WINDOW_SEC * SAMPLE_RATE)
                            tail = cur[-win:] if len(cur) > win else cur
                            t0 = time.time()
                            p = self._recognize(tail)
                            dt = time.time() - t0
                            self.perf["partial_n"] += 1
                            self.perf["partial_ms"] += dt * 1000
                            # 適応スロットリング: デコードが interval に収まらない
                            # 遅いCPUでも張り付かないよう、所要時間×2 ぶんの新規音声が
                            # 貯まるまで次の途中経過を待つ（＝途中経過デコードの占有率を
                            # 最大50%に制限。速いCPUでは gap=interval のまま挙動不変）
                            partial_gap = max(interval_samples,
                                              int(dt * 2.0 * SAMPLE_RATE))
                            if self._asr_caps.get("spaces"):
                                from asr_model import strip_cjk_spaces
                                p = strip_cjk_spaces(p)
                            if (cfg.get("num_arabic", True)
                                    and not self._asr_caps.get("punct")):
                                p = normalize_numbers(p)
                            if self._mask is not None:      # 認識中(薄文字)も伏せ字化
                                p = self._mask(p)
                            if len(cur) > win and p:
                                p = "…" + p   # 冒頭が省略されていることを示す
                            self.on_partial(p, speaker)
                        if len(cur) >= max_samples:
                            vad.flush()
        except Exception as e:
            # このソースで致命的エラー → 全体を止めて _run 側で通知
            self._stream_error = f"実行エラー（{speaker or '入力'}）: {e}"
            self._stop.set()

    def _run(self, cfg):
        try:
            try:
                self._load(cfg)
            except Exception as e:  # モデル・単語帳のエラーをGUIへ
                if self._stop.is_set():
                    self._finish_session("stopped", "停止しました")
                    return
                info = (type(e).__name__ + " " + str(e)).lower()
                netish = any(k in info for k in (
                    "connection", "download", "resolve", "timeout", "network",
                    "getaddrinfo", "maxretry", "httperror", "localentrynotfound",
                    "offline", "temporarily", "ssl"))
                if netish:
                    detail = ("モデルのダウンロードに失敗しました。"
                              "ネット接続を確認して、もう一度 ▶開始 してください"
                              "（ダウンロード済みの分は続きから取得します）")
                else:
                    detail = f"ロード失敗: {e}"
                self._finish_session("error", detail)
                return

            # ロードはライブラリ内部でブロックすることがあり、途中停止を即時には
            # 中断できない。完了直後に停止要求を確認し、音声入力は開始しない。
            if self._stop.is_set():
                self._finish_session("stopped", "停止しました")
                return

            # 英訳ワーカー起動（この設定で有効なときだけ）。確定行を別スレッドで翻訳し認識を止めない
            self._translate_on = cfg.get("translate", False) and self._translate is not None
            self._tq = queue.Queue(maxsize=TRANSLATION_QUEUE_MAX_ITEMS)
            self._tworker = None
            if self._translate_on:
                self._tworker = threading.Thread(target=self._translate_loop,
                                                 daemon=True)
                self._tworker.start()

            self._open_log(cfg)             # 文字起こしログをこのセッション用に開く
            self._mask = self._build_masker(cfg)   # 禁止ワードの伏せ字化を用意
            self._gloss = self._build_glossary(cfg)   # 英訳辞書（固有名詞の訳を固定）
            self._stream_error = None
            self.perf = self._fresh_perf()   # セッションごとに計測をリセット

            if self._stop.is_set():
                self._stop_translate_worker()
                self._close_log()
                self._finish_session("stopped", "停止しました")
                return

            # 入力ソース（通常=1本 / 1対1コラボ=2本）ごとにスレッドを起こす
            sources = self._resolve_sources(cfg)
            threads = [threading.Thread(target=self._stream_loop,
                                        args=(cfg, dev, spk, prim), daemon=True)
                       for dev, spk, prim in sources]
            for t in threads:
                t.start()
            # スレッドが1本でも走り出すまで少し待ち、致命的エラーが無ければ「認識中」に
            self._stop.wait(0.3)
            if self._stream_error:
                self._stop_translate_worker()
                self._close_log()
                self._finish_session("error", self._stream_error)
                return
            if not self._stop.is_set():
                self._set_lifecycle("running")
                self.on_state(
                    "running",
                    "認識中" + (f"  ※{self._load_warn}" if self._load_warn else ""),
                )

            # 停止指示、または全ソースが（エラー等で）終了するまで待つ
            while not self._stop.is_set() and any(t.is_alive() for t in threads):
                self._stop.wait(0.2)
            for t in threads:
                t.join(timeout=5)

            self._stop_translate_worker()
            self._close_log()
            self.on_level(0.0, "")
            self.on_partial("", "")
            if self._stream_error:
                self._finish_session("error", self._stream_error)
            else:
                self._finish_session("stopped", "停止しました")
        except Exception as e:
            # 音声入力準備など、ロード後の予期しない失敗でも状態を宙ぶらりんにしない。
            self._stop_translate_worker()
            self._close_log()
            if self._stop.is_set():
                self._finish_session("stopped", "停止しました")
            else:
                self._finish_session("error", f"起動失敗: {e}")
        finally:
            with self._state_lock:
                if self._thread is threading.current_thread():
                    self._thread = None


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
