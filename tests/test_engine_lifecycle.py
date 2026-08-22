import threading
import numpy as np
import time
import unittest
import queue

from engine import (
    AUDIO_QUEUE_MAX_BLOCKS,
    AUDIO_QUEUE_RECOVER_BLOCKS,
    SAMPLE_RATE,
    TRANSLATE_FAIL_WARN,
    TRANSLATION_QUEUE_MAX_ITEMS,
    TRANSLATION_QUEUE_RECOVER_ITEMS,
    WINDOW_SIZE,
    CaptionEngine,
    _offer_bounded_latest,
    _aux_precision,
    _should_emit_partial,
    _translate_signature,
)


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class ControlledEngine(CaptionEngine):
    """モデル・音声デバイスを使わずライフサイクルだけを試すエンジン。"""

    def __init__(self, *, block_load=False, fail_load_times=0,
                 stream_error=None, stream_exits=False, block_notice=False):
        self.events = []
        self._events_lock = threading.Lock()
        super().__init__(on_state=self._record_state)
        self.block_load = block_load
        self.fail_load_times = fail_load_times
        self.initial_stream_error = stream_error
        self.stream_exits = stream_exits
        self.block_notice = block_notice
        self.notice_entered = threading.Event()
        self.release_notice = threading.Event()
        self.load_entered = threading.Event()
        self.release_load = threading.Event()
        self.stream_started = threading.Event()
        self.load_calls = 0
        self.stream_calls = 0

    def _record_state(self, state, detail=""):
        with self._events_lock:
            self.events.append((state, detail))
        if self.block_notice and state == "loading":
            self.notice_entered.set()
            self.release_notice.wait(2)

    def _load(self, _cfg):
        self.load_calls += 1
        self.load_entered.set()
        if self.block_load:
            self.release_load.wait(2)
        if self.fail_load_times:
            self.fail_load_times -= 1
            raise RuntimeError("test load failure")

    def _open_log(self, _cfg):
        self._logf = None

    def _build_masker(self, _cfg):
        return None

    def _build_glossary(self, _cfg):
        return None

    def _resolve_sources(self, _cfg):
        return [(None, "", True)]

    def _stream_loop(self, _cfg, _device, _speaker, _primary):
        self.stream_calls += 1
        self.stream_started.set()
        if self.initial_stream_error:
            self._stream_error = self.initial_stream_error
            self._stop.set()
            return
        if self.stream_exits:
            return
        self._stop.wait(2)


class CaptionEngineLifecycleTests(unittest.TestCase):
    def tearDown(self):
        engine = getattr(self, "engine", None)
        if engine is not None:
            if hasattr(engine, "release_notice"):
                engine.release_notice.set()
            if hasattr(engine, "release_load"):
                engine.release_load.set()
            engine.stop(timeout=1)

    def test_concurrent_start_requests_create_only_one_session(self):
        self.engine = ControlledEngine(block_load=True)
        callers = 16
        barrier = threading.Barrier(callers)
        results = []
        result_lock = threading.Lock()

        def request_start():
            barrier.wait()
            accepted = self.engine.start({"request": "same"})
            with result_lock:
                results.append(accepted)

        threads = [threading.Thread(target=request_start) for _ in range(callers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertTrue(self.engine.load_entered.wait(1))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), callers - 1)
        self.assertEqual(self.engine.load_calls, 1)
        self.assertEqual(self.engine.lifecycle_state, "starting")

    def test_stop_during_load_blocks_restart_and_never_opens_audio(self):
        self.engine = ControlledEngine(block_load=True)
        self.assertTrue(self.engine.start({}))
        self.assertTrue(self.engine.load_entered.wait(1))

        self.assertFalse(self.engine.stop(timeout=0.01))
        self.assertEqual(self.engine.lifecycle_state, "stopping")
        self.assertFalse(self.engine.start({}))
        self.assertEqual(self.engine.load_calls, 1)

        self.engine.release_load.set()
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "stopped"
            and self.engine._thread is None
        ))
        self.assertEqual(self.engine.stream_calls, 0)
        states = [state for state, _detail in self.engine.events]
        self.assertIn("stopping", states)
        self.assertNotIn("running", states)

    def test_start_stop_race_before_worker_release_cannot_lose_thread(self):
        self.engine = ControlledEngine(block_load=True, block_notice=True)
        first_result = []
        duplicate_result = []
        stop_result = []

        first = threading.Thread(
            target=lambda: first_result.append(self.engine.start({}))
        )
        first.start()
        self.assertTrue(self.engine.notice_entered.wait(1))

        duplicate = threading.Thread(
            target=lambda: duplicate_result.append(self.engine.start({}))
        )
        stopper = threading.Thread(
            target=lambda: stop_result.append(self.engine.stop(timeout=0.05))
        )
        duplicate.start()
        stopper.start()
        self.engine.release_notice.set()

        first.join(timeout=1)
        duplicate.join(timeout=1)
        stopper.join(timeout=1)
        self.assertEqual(first_result, [True])
        self.assertEqual(duplicate_result, [False])
        self.assertTrue(self.engine.load_entered.wait(1))
        self.assertEqual(self.engine.load_calls, 1)

        self.engine.release_load.set()
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "stopped"
            and self.engine._thread is None
        ))

    def test_restart_is_allowed_after_stopped_session_has_fully_exited(self):
        self.engine = ControlledEngine(block_load=True)
        self.assertTrue(self.engine.start({}))
        self.assertTrue(self.engine.load_entered.wait(1))
        self.assertFalse(self.engine.stop(timeout=0.01))
        self.engine.release_load.set()
        self.assertTrue(wait_until(lambda: self.engine._thread is None))

        self.engine.load_entered.clear()
        self.assertTrue(self.engine.start({}))
        self.assertTrue(self.engine.stream_started.wait(1))
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "running"
        ))
        self.assertEqual(self.engine.load_calls, 2)
        self.assertTrue(self.engine.stop(timeout=1))
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "stopped"
        ))

    def test_load_failure_enters_error_and_can_be_retried(self):
        self.engine = ControlledEngine(fail_load_times=1)
        self.assertTrue(self.engine.start({}))
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "error"
            and self.engine._thread is None
        ))
        self.assertFalse(self.engine.running)

        self.assertTrue(self.engine.start({}))
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "running"
        ))
        self.assertEqual(self.engine.load_calls, 2)
        self.assertTrue(self.engine.stop(timeout=1))

    def test_stream_start_failure_enters_error(self):
        self.engine = ControlledEngine(stream_error="test input failure")
        self.assertTrue(self.engine.start({}))
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "error"
            and self.engine._thread is None
        ))
        self.assertFalse(self.engine.running)
        self.assertTrue(any(
            state == "error" and detail == "test input failure"
            for state, detail in self.engine.events
        ))

    def test_natural_stream_end_returns_to_stopped(self):
        self.engine = ControlledEngine(stream_exits=True)
        self.assertTrue(self.engine.start({}))
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "stopped"
            and self.engine._thread is None
        ))
        self.assertFalse(self.engine.running)

    def test_stop_is_idempotent_before_and_after_a_session(self):
        self.engine = ControlledEngine()
        self.assertTrue(self.engine.stop(timeout=0))
        self.assertTrue(self.engine.stop(timeout=0))
        self.assertEqual(self.engine.events, [])

        self.assertTrue(self.engine.start({}))
        self.assertTrue(wait_until(
            lambda: self.engine.lifecycle_state == "running"
        ))
        self.assertTrue(self.engine.stop(timeout=1))
        event_count = len(self.engine.events)
        self.assertTrue(self.engine.stop(timeout=0))
        self.assertEqual(len(self.engine.events), event_count)

    def test_bounded_queue_keeps_normal_items_unchanged(self):
        q = queue.Queue(maxsize=4)
        for value in range(4):
            self.assertEqual(_offer_bounded_latest(q, value, 1), [])
        self.assertEqual(list(q.queue), [0, 1, 2, 3])

    def test_bounded_queue_discards_oldest_only_after_limit(self):
        q = queue.Queue(maxsize=4)
        for value in range(4):
            q.put_nowait(value)
        dropped = _offer_bounded_latest(q, 4, 1)
        # 捨てた「中身」を返す（原文フォールバックの材料になるため）
        self.assertEqual(dropped, [0, 1, 2])
        self.assertEqual(list(q.queue), [3, 4])

    def test_production_queue_limits_leave_large_normal_headroom(self):
        buffered_audio_sec = (
            AUDIO_QUEUE_MAX_BLOCKS * WINDOW_SIZE / SAMPLE_RATE
        )
        self.assertGreaterEqual(buffered_audio_sec, 29.9)
        self.assertLess(AUDIO_QUEUE_RECOVER_BLOCKS, AUDIO_QUEUE_MAX_BLOCKS)
        self.assertGreaterEqual(TRANSLATION_QUEUE_MAX_ITEMS, 100)

    def test_translation_stop_discards_backlog_and_joins_worker(self):
        translated = []
        translating = threading.Event()
        release = threading.Event()
        self.engine = CaptionEngine(
            on_translation=lambda fid, text, fb=False:
                translated.append((fid, text, fb))
        )

        def slow_translate(text):
            translating.set()
            release.wait(1)
            return "tr:" + text

        self.engine._translate = slow_translate
        self.engine._translate_on = True
        self.engine._tq = queue.Queue(maxsize=8)
        self.engine._tworker = threading.Thread(
            target=self.engine._translate_loop, daemon=True
        )
        self.engine._tworker.start()
        self.engine._tq.put_nowait((1, "current"))
        self.assertTrue(translating.wait(1))
        for fid in range(2, 8):
            self.engine._tq.put_nowait((fid, f"pending-{fid}"))

        stopped = threading.Event()
        stopper = threading.Thread(
            target=lambda: (self.engine._stop_translate_worker(), stopped.set())
        )
        stopper.start()
        time.sleep(0.03)
        self.assertFalse(stopped.is_set())
        self.assertLessEqual(self.engine._tq.qsize(), 1)

        release.set()
        stopper.join(timeout=1)
        self.assertTrue(stopped.is_set())
        self.assertIsNone(self.engine._tworker)
        self.assertIsNone(self.engine._tq)
        self.assertEqual(translated, [])


class TranslationFallbackTests(unittest.TestCase):
    """訳が出せなかった行の扱い。

    翻訳のみ表示（displayMode="en"）には「字幕本体」が無く、訳が届かない行は
    画面に何も出ない。稼働中に翻訳が失敗し続けると字幕が全消失したまま
    「認識中」表示が続くため、原文へのフォールバックと警告を検証する。
    """

    def _engine(self, translate):
        events = []
        warns = []
        engine = CaptionEngine(
            on_translation=lambda fid, text, fb=False:
                events.append((fid, text, fb)),
            on_warn=lambda kind, msg="", active=True:
                warns.append((kind, active)),
        )
        engine._translate = translate
        engine._translate_on = True
        engine._tq = queue.Queue(maxsize=8)
        return engine, events, warns

    def _run_once(self, engine, item):
        engine._tq.put_nowait(item)
        engine._tq.put_nowait(None)
        engine._translate_loop()

    def test_translate_failure_falls_back_to_source_text(self):
        def boom(_text):
            raise RuntimeError("translate crashed")

        engine, events, _ = self._engine(boom)
        self._run_once(engine, (7, "こわいよ"))
        self.assertEqual(events, [(7, "こわいよ", True)])

    def test_empty_translation_falls_back_to_source_text(self):
        engine, events, _ = self._engine(lambda _text: "")
        self._run_once(engine, (7, "！！！"))
        self.assertEqual(events, [(7, "！！！", True)])

    def test_successful_translation_is_not_marked_fallback(self):
        engine, events, _ = self._engine(lambda text: "en:" + text)
        self._run_once(engine, (7, "にげて"))
        self.assertEqual(events, [(7, "en:にげて", False)])

    def test_glossary_substitution_is_not_leaked_into_fallback(self):
        """英訳辞書は翻訳の前処理。失敗時に英単語混じりの原文を出さない。"""
        engine, events, _ = self._engine(lambda _text: "")
        engine._gloss = [("癒色えも", "Emo Ishiki")]
        engine._translate_sig = ("fugumt", "ja", "en")
        self._run_once(engine, (7, "癒色えもです"))
        self.assertEqual(events, [(7, "癒色えもです", True)])

    def test_consecutive_failures_warn_once_and_clear_on_recovery(self):
        outcomes = ["", "", "", "", "", "", "ok"]
        engine, _, warns = self._engine(lambda _t: outcomes.pop(0))
        for fid in range(len(outcomes)):
            engine._tq.put_nowait((fid, f"line-{fid}"))
        engine._tq.put_nowait(None)
        engine._translate_loop()

        self.assertEqual(warns, [("translate", True), ("translate", False)])
        self.assertEqual(engine.perf["translate_fail"], TRANSLATE_FAIL_WARN + 1)
        self.assertEqual(engine._tfail, 0)

    def test_single_failure_does_not_warn(self):
        outcomes = ["", "ok"]
        engine, _, warns = self._engine(lambda _t: outcomes.pop(0))
        for fid in range(2):
            engine._tq.put_nowait((fid, f"line-{fid}"))
        engine._tq.put_nowait(None)
        engine._translate_loop()
        self.assertEqual(warns, [])

    def test_queue_overflow_falls_back_instead_of_dropping_silently(self):
        """混雑で押し出された行にも原文を出す（訳は永遠に来ないため）。

        本番と同じ上限で確かめる。満杯になると RECOVER まで一気に間引くので、
        1回のあふれで (MAX - RECOVER) 行ぶんのフォールバックが出る。
        """
        engine, events, _ = self._engine(lambda text: text)
        engine._tq = queue.Queue(maxsize=TRANSLATION_QUEUE_MAX_ITEMS)
        for fid in range(TRANSLATION_QUEUE_MAX_ITEMS):
            engine._queue_translation(fid, f"line-{fid}")
        self.assertEqual(events, [])          # 満杯になるまでは何も捨てない

        engine._queue_translation(TRANSLATION_QUEUE_MAX_ITEMS, "overflowing")
        n = TRANSLATION_QUEUE_MAX_ITEMS - TRANSLATION_QUEUE_RECOVER_ITEMS
        self.assertEqual([(fid, text, fb) for fid, text, fb in events],
                         [(i, f"line-{i}", True) for i in range(n)])
        self.assertEqual(engine.perf["translate_dropped"], n)

    def test_translation_off_emits_nothing(self):
        engine, events, _ = self._engine(lambda text: "en:" + text)
        engine._translate_on = False
        self._run_once(engine, (7, "にげて"))
        self.assertEqual(events, [])


class CaptionBlackoutInvariantTests(unittest.TestCase):
    """字幕が無言で消えないための、コンポーネントをまたぐ不変条件。"""

    def test_overlay_pending_limit_exceeds_engine_translation_queue(self):
        """オーバーレイの翻訳待ち上限は、エンジンの翻訳キュー上限より大きいこと。

        小さいと、訳が届く前に待ち行が捨てられ「訳が来ても表示先が無い」状態に
        なる。翻訳のみ表示では、そのまま字幕が出ないまま固定される（v0.9.0で
        PENDING_MAX=64 < キュー128 だった）。
        """
        import os
        import re
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "overlay.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        m = re.search(r"const PENDING_MAX = (\d+);", html)
        self.assertIsNotNone(m, "overlay.html の PENDING_MAX が見つからない")
        self.assertGreater(int(m.group(1)), TRANSLATION_QUEUE_MAX_ITEMS)

    def test_translation_recover_target_leaves_room_below_limit(self):
        self.assertLess(TRANSLATION_QUEUE_RECOVER_ITEMS,
                        TRANSLATION_QUEUE_MAX_ITEMS)


class _RecordingModel:
    """decode に渡されたサンプル数だけ覚えるダミー認識器"""

    def __init__(self):
        self.decoded_len = None

    class _Stream:
        def __init__(self, owner):
            self._owner = owner

        def accept_waveform(self, sample_rate, samples):
            self._owner.decoded_len = len(samples)

        @property
        def result(self):
            return type("R", (), {"text": " ok "})()

    def create_stream(self):
        return self._Stream(self)

    def decode_stream(self, stream):
        pass


class RecognizePaddingTests(unittest.TestCase):
    """無音パディングはモデルの caps["pad"] で切り替わる。

    SenseVoice にパディングを入れると感情ラベルが全て EMO_UNKNOWN に潰れ、
    数字まわりに余分な空白も入るため、素の発話を渡さなければならない。
    """

    def _decode_len(self, caps):
        engine = CaptionEngine()
        engine._model = _RecordingModel()
        engine._asr_caps = caps
        engine._recognize(np.zeros(SAMPLE_RATE, dtype=np.float32))
        return engine._model.decoded_len

    def test_k2_keeps_the_silence_padding(self):
        expected = SAMPLE_RATE + 2 * int(CaptionEngine.PAD_SECONDS * SAMPLE_RATE)
        self.assertEqual(self._decode_len({"pad": True}), expected)

    def test_sensevoice_receives_the_raw_utterance(self):
        self.assertEqual(self._decode_len({"pad": False}), SAMPLE_RATE)

    def test_unknown_model_falls_back_to_padding(self):
        expected = SAMPLE_RATE + 2 * int(CaptionEngine.PAD_SECONDS * SAMPLE_RATE)
        self.assertEqual(self._decode_len({}), expected)

    def test_registry_marks_sensevoice_as_unpadded_and_needing_numnorm(self):
        import asr_model

        self.assertTrue(asr_model.MODELS["k2-ja"]["caps"]["pad"])
        self.assertFalse(asr_model.MODELS["sensevoice"]["caps"]["pad"])
        # 句読点は内蔵、数字は漢数字で出るので numnorm を通す必要がある
        self.assertTrue(asr_model.MODELS["sensevoice"]["caps"]["punct"])
        self.assertFalse(asr_model.MODELS["sensevoice"]["caps"]["itn"])


class TranslateSignatureTests(unittest.TestCase):
    """翻訳経路の再ロード判定。翻訳OFF（plan=None）で落ちないこと。"""

    def test_returns_none_when_translation_is_off(self):
        # v0.9.5 で踏んだ回帰: plan に直接 tuple を足して
        # 「unsupported operand type(s) for +: 'NoneType' and 'tuple'」で起動不能に。
        # 翻訳は既定OFFなので、ほぼ全ユーザーが初回起動で踏む状態だった
        self.assertIsNone(_translate_signature(None, "int8"))

    def test_includes_precision_for_fugumt(self):
        # 英訳は高精度モードで精度が変わるので積み直しが要る
        self.assertEqual(_translate_signature(("fugumt", "ja", "en"), "int8"),
                         ("fugumt", "ja", "en", "int8"))
        self.assertEqual(_translate_signature(("fugumt", "ja", "en"), "fp32"),
                         ("fugumt", "ja", "en", "fp32"))

    def test_excludes_precision_for_other_engines(self):
        # M2M は変換時点で int8。精度を切り替えても積み直す必要がない
        self.assertEqual(_translate_signature(("m2m", "ja", "zh"), "fp32"),
                         ("m2m", "ja", "zh"))
        self.assertEqual(_translate_signature(("opencc", "zh", "zh_tw"), "fp32"),
                         ("opencc", "zh", "zh_tw"))


class FinalOnlyTests(unittest.TestCase):
    """「確定した字幕だけ表示する」= 途中経過（薄文字）を作らない設定。

    表示を止めるだけでなく末尾デコードごと省くため、CPU負荷も下がる。
    最長時間での強制確定（vad.flush）は従来どおり効く必要がある。
    """

    def test_skips_the_partial_when_final_only(self):
        self.assertFalse(_should_emit_partial(
            {"final_only": True}, SAMPLE_RATE, 0, 100))

    def test_emits_the_partial_by_default(self):
        self.assertTrue(_should_emit_partial({}, SAMPLE_RATE, 0, 100))
        self.assertTrue(_should_emit_partial(
            {"final_only": False}, SAMPLE_RATE, 0, 100))

    def test_waits_until_enough_new_audio_arrived(self):
        # 前回から gap ぶん貯まるまでは出さない（適応スロットリング）
        self.assertFalse(_should_emit_partial({}, SAMPLE_RATE, SAMPLE_RATE - 10, 100))

    def test_ignores_utterances_shorter_than_the_minimum(self):
        self.assertFalse(_should_emit_partial({}, int(0.2 * SAMPLE_RATE), 0, 100))

    def test_default_config_keeps_the_partial_on(self):
        import app_server

        self.assertIs(app_server.DEFAULT_CONFIG["final_only"], False)


class AuxPrecisionTests(unittest.TestCase):
    """句読点BERT・英訳モデルの精度は認識モデルの「高精度モード」に揃える。

    句読点int8は fp32 比で常駐 -236MB・約2倍速（判定は89.5%一致・不一致の7割は
    文末「。」の欠落）、英訳int8は -170MB・3倍速（訳文は62%一致だが品質は互角）。
    fp32 を選ぶ人のために両方を残し、1つの設定でまとめて切り替える。
    """

    def test_high_accuracy_mode_uses_the_fp32_punctuator(self):
        self.assertEqual(_aux_precision({"precision": "fp32"}), "fp32")

    def test_default_and_fast_asr_use_int8(self):
        self.assertEqual(_aux_precision({}), "int8")
        self.assertEqual(_aux_precision({"precision": "int8-fp32"}), "int8")
        self.assertEqual(_aux_precision({"precision": "int8"}), "int8")

    def test_model_file_maps_precision_and_falls_back_to_int8(self):
        import punct

        self.assertEqual(punct.model_file("fp32"), "punct_bert.onnx")
        self.assertEqual(punct.model_file("int8"), "punct_bert.int8.onnx")
        # 設定ファイルが壊れていても字幕は出し続ける（軽い方へ倒す）
        self.assertEqual(punct.model_file("bogus"), "punct_bert.int8.onnx")

    def test_translation_backend_maps_precision_to_compute_type(self):
        import translate

        # CTranslate2 は実行時に精度を選べる（モデルの再変換は不要）
        self.assertEqual(translate._COMPUTE_TYPE["int8"], "int8_float32")
        self.assertEqual(translate._COMPUTE_TYPE["fp32"], "float32")

    def test_download_estimate_follows_the_selected_precision(self):
        import engine as engine_mod

        self.assertEqual(engine_mod._MODEL_SIZES_MB["punct_int8"], 109)
        self.assertLess(engine_mod._MODEL_SIZES_MB["punct_int8"],
                        engine_mod._MODEL_SIZES_MB["punct"])


if __name__ == "__main__":
    unittest.main()
