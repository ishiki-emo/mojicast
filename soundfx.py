"""音イベント検出（笑い・拍手・歓声など → 演出トリガー）

AudioSet 527クラスの音イベント分類（sherpa-onnx AudioTagging・int8 27MB）で、
マイク/ループバックの音声ブロックから「演出に使える音」を拾う。
ASRとは完全に独立しており、k2-ja / SenseVoice どちらのモデル選択でも同じに動く。

SenseVoice の感情推定（SER）は3条件の実測すべてで実用に届かず不採用。
経緯と数値は ROADMAP #7 / SOUNDFX_DESIGN.md を参照。

判定パラメータは実測で確定した値（bench/probe_emotion_live.py と同値。
実配信178秒で笑い5/5・見逃し0・誤検出0）。**勘で動かさないこと。**

提供するもの:
    GROUPS                       AudioSetクラス → 演出グループの対応
    cached()                     モデルがローカルにあるか（DLサイズ見積り用）
    load_tagger(num_threads)     分類器をロード（無ければ27MBを自動DL）
    SoundFxDetector(...)         音声ブロック → on_event(group, score, speaker)
"""
import os
import threading

import numpy as np

SAMPLE_RATE = 16000

# 1回の分類に使う直近の秒数。3秒だと笑いが薄まりピークが半減する（実測）
WINDOW_SEC = 1.5
# 分類間隔。0.5秒だと笑いの山を踏み外しスコアが半減することがある（実測）。
# 1回の推論は約12ms → 0.25秒間隔でCPU約4.8%（1コア換算）
HOP_SEC = 0.25
# この秒数スコアが途切れたら別の出来事。2.0秒だと2.5秒差の別々の笑いが
# 1回に統合されてしまう（実測: 笑いの間はスコアが約1.25秒 0.00 に落ちる）
GAP_SEC = 1.0

# 演出グループ（AudioSetクラス名はモデル配布の class_labels_indices.csv 表記）。
# 笑い声は Laughter 単独ではなく Snicker/Chuckle/Giggle に確率が分散するため
# グループで合算して判定する（例: Snicker 0.65 + Chuckle 0.40 + Laughter 0.41）
GROUPS = {
    "笑い": {"Laughter", "Giggle", "Snicker", "Belly laugh",
             "Chuckle, chortle", "Baby laughter"},
    "拍手・歓声": {"Applause", "Clapping", "Cheering"},
    "歌・口笛": {"Singing", "Male singing", "Female singing",
                 "Child singing", "Humming", "Whistling"},
    "泣き": {"Crying, sobbing", "Whimper", "Sniff"},
    "驚き・叫び": {"Screaming", "Shout", "Yell", "Squeal", "Gasp"},
    "咳・くしゃみ": {"Cough", "Sneeze", "Throat clearing"},
}

# 二段しきい値。ENTER を超えたら出来事の追跡を始め、ピークが FIRE に達した
# 瞬間に1回だけ発火する。単一しきい値だと窓の位相ずれで際どいスコアが
# 出入りしてしまう（実測: 非笑いが位相次第で 0.28→0.46 まで動いた。
# 本物の笑いのピークは 0.56〜1.49 なので、FIRE=0.5 が両者を分ける）。
# 強い笑いは最初の窓から FIRE を超えるため遅延なし。弱い立ち上がりだけ
# 1〜2hop（0.25〜0.5秒）遅れて発火する
ENTER = 0.3          # 0.15まで下げると笑っていない箇所を拾う（実測）
FIRE_BY_GROUP = {"笑い": 0.5}
# グループ別の入口の上書き。咳と笑いは互いのスコアを立て合う（咳をすると
# Laughter が 0.42〜0.57、笑うと Cough が 0.33）ため、咳側だけ上げて
# 弱い誤発火を切る。叫びは表現によって弱く出るため少し下げる
ENTER_BY_GROUP = {
    "咳・くしゃみ": 0.5,
    "驚き・叫び": 0.25,
}

_REPO = "k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15"
_FILES = ["model.int8.onnx", "class_labels_indices.csv"]


def _resolve(download=True):
    import huggingface_hub as hf
    try:
        return hf.snapshot_download(_REPO, allow_patterns=_FILES,
                                    local_files_only=True)
    except Exception:
        if not download:
            raise
        return hf.snapshot_download(_REPO, allow_patterns=_FILES)


def cached() -> bool:
    """モデルがローカルにあるか（DLはしない。初回DLサイズ見積り用）"""
    try:
        _resolve(download=False)
        return True
    except Exception:
        return False


class SharedTagger:
    """分類器の共有ラッパ。単一の AudioTagging を全話者で使い回すため
    compute をロックで直列化する（1回約12msなので待ちはほぼ発生しない）。"""

    def __init__(self, tagger):
        self._t = tagger
        self._lock = threading.Lock()

    def classify(self, samples: np.ndarray) -> dict:
        """直近窓の音声 → {クラス名: 確率}（上位のみ）"""
        with self._lock:
            stream = self._t.create_stream()
            stream.accept_waveform(
                SAMPLE_RATE, np.asarray(samples, dtype=np.float32))
            events = self._t.compute(stream)
        return {e.name: float(e.prob) for e in events}


def load_tagger(num_threads: int = 2) -> SharedTagger:
    """音イベント分類器をロード（モデルが無ければ27MBを自動DL）"""
    import sherpa_onnx
    d = _resolve()
    cfg = sherpa_onnx.AudioTaggingConfig(
        model=sherpa_onnx.AudioTaggingModelConfig(
            zipformer=sherpa_onnx.OfflineZipformerAudioTaggingModelConfig(
                model=os.path.join(d, "model.int8.onnx")),
            num_threads=num_threads, provider="cpu"),
        labels=os.path.join(d, "class_labels_indices.csv"),
        top_k=20)   # グループ合算に必要な下位クラスまで取る
    return SharedTagger(sherpa_onnx.AudioTagging(cfg))


class SoundFxDetector:
    """音声ブロックを受け取り、演出グループが立ったらコールバックする。

    話者（入力ソース）ごとに1インスタンス。バッファと発火状態が独立する。
    分類器は SharedTagger を全話者で共有する。

    on_event(group, score, speaker) は取り込みスレッドから呼ばれる。
    発火は1出来事につき1回のみ: ENTER 超えで追跡を始め、ピークが FIRE に
    達した瞬間に発火する。以後 ENTER 超えが続く間は同じ出来事として
    再発火しない。GAP_SEC 途切れてから再び超えたら新しい出来事。
    時計は壁時計でなく音声サンプル数で数える（入力の詰まり・テストの
    再現性に影響されないため）。
    """

    def __init__(self, tagger: SharedTagger, on_event, speaker=""):
        self._tagger = tagger
        self._on_event = on_event
        self._speaker = speaker
        self._win = np.empty(0, dtype=np.float32)
        self._win_n = int(WINDOW_SEC * SAMPLE_RATE)
        self._hop_n = int(HOP_SEC * SAMPLE_RATE)
        self._since_hop = 0
        self._pos = 0                # 通算サンプル数（音声時間の時計）
        self._events = {}            # グループ → {"last": 秒, "fired": bool}

    @staticmethod
    def group_scores(probs: dict) -> dict:
        """{クラス名: 確率} → {グループ名: 合算スコア}（0のグループは含めない）"""
        out = {}
        for group, members in GROUPS.items():
            s = sum(probs.get(m, 0.0) for m in members)
            if s > 0:
                out[group] = s
        return out

    def feed(self, block: np.ndarray) -> None:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        self._pos += len(block)
        self._win = np.concatenate([self._win, block])[-self._win_n:]
        self._since_hop += len(block)
        if self._since_hop < self._hop_n or len(self._win) < self._win_n:
            return
        self._since_hop = 0

        probs = self._tagger.classify(self._win)
        scores = self.group_scores(probs)
        above = {g: s for g, s in scores.items()
                 if s >= ENTER_BY_GROUP.get(g, ENTER)}
        if not above:
            return
        # 咳と笑いのように互いのスコアを立て合う組でも、正解側が常に高い（実測）。
        # 1窓につき最上位グループだけを採用する
        group = max(above, key=lambda g: above[g])
        score = above[group]

        now = self._pos / SAMPLE_RATE
        ev = self._events.get(group)
        if ev is None or now - ev["last"] > GAP_SEC:
            ev = {"last": now, "fired": False}   # 新しい出来事の追跡開始
            self._events[group] = ev
        ev["last"] = now
        if ev["fired"]:
            return                   # この出来事はもう発火済み
        if score >= FIRE_BY_GROUP.get(group, ENTER_BY_GROUP.get(group, ENTER)):
            ev["fired"] = True
            self._on_event(group, score, self._speaker)
