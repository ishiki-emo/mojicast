"""GUI窓の拡大率（ui_scale）の正規化・解決・窓サイズのクランプ

- 壊れた値やレンジ外を保存しても、窓が作れなくなる倍率にならないこと。
- "auto" は従来どおり起動モニタからの自動判定へ委ねること。
- 拡大指定時に窓・最小サイズが画面の作業領域を超えないこと。
"""
import http.client
import json
import threading
import unittest
from unittest import mock

import app_server
import platform_compat
import wordstore


class NormalizeUiScaleTests(unittest.TestCase):
    """保存前の正規化。UIから来る文字列も、手書きconfigの異常値も同じ関門を通す"""

    def test_auto_forms(self):
        for value in ("auto", "", None):
            self.assertEqual(app_server.normalize_ui_scale(value), "auto")

    def test_unparsable_falls_back_to_auto(self):
        for value in ("大きく", [], {}, float("nan")):
            self.assertEqual(app_server.normalize_ui_scale(value), "auto")

    def test_clamped_into_range(self):
        self.assertEqual(app_server.normalize_ui_scale(0.1), app_server.UI_SCALE_MIN)
        self.assertEqual(app_server.normalize_ui_scale(9.0), app_server.UI_SCALE_MAX)
        self.assertEqual(app_server.normalize_ui_scale(-3), app_server.UI_SCALE_MIN)

    def test_numeric_strings_accepted(self):
        # UIの<select>は文字列を送る
        self.assertEqual(app_server.normalize_ui_scale("1.25"), 1.25)
        self.assertEqual(app_server.normalize_ui_scale("1"), 1.0)


class ResolveUiScaleTests(unittest.TestCase):
    """実際に窓へ適用する倍率の決定"""

    def test_explicit_value_wins_over_auto_detection(self):
        with mock.patch.object(platform_compat, "ui_scale", return_value=0.8):
            self.assertEqual(app_server.resolve_ui_scale({"ui_scale": 1.5}), 1.5)

    def test_auto_uses_monitor_detection(self):
        with mock.patch.object(platform_compat, "ui_scale", return_value=0.8):
            self.assertEqual(app_server.resolve_ui_scale({"ui_scale": "auto"}), 0.8)

    def test_missing_key_behaves_as_auto(self):
        # 旧バージョンのconfig.jsonにはこのキーが無い（従来動作を維持する）
        with mock.patch.object(platform_compat, "ui_scale", return_value=0.75):
            self.assertEqual(app_server.resolve_ui_scale({}), 0.75)

    def test_broken_value_does_not_break_startup(self):
        with mock.patch.object(platform_compat, "ui_scale", return_value=0.8):
            self.assertEqual(app_server.resolve_ui_scale({"ui_scale": "???"}), 0.8)


class WindowFitTests(unittest.TestCase):
    """拡大時に窓が画面からはみ出さないこと（app.py）"""

    @classmethod
    def setUpClass(cls):
        try:
            import app
        except ImportError as e:      # pywebview未導入の環境ではGUI側の検証は行わない
            raise unittest.SkipTest(f"app.py を読み込めません: {e}")
        cls.fit = staticmethod(app._fit)

    def test_scales_when_screen_is_large_enough(self):
        self.assertEqual(self.fit(1100, 800, 1.5, (2560, 1440)), (1650, 1200))

    def test_clamped_to_work_area(self):
        # FullHDのタスクバー込み作業領域で1.5倍を指定しても画面内に収まる
        width, height = self.fit(1100, 800, 1.5, (1920, 1032))
        self.assertLessEqual(width, 1920)
        self.assertLessEqual(height, 1032)

    def test_no_work_area_means_no_clamp(self):
        # 作業領域を取得できないOS・環境では従来どおり倍率だけ掛ける
        self.assertEqual(self.fit(1100, 800, 0.8, (0, 0)), (880, 640))

    def test_min_size_never_exceeds_window_size(self):
        # 最小サイズが初期サイズを上回ると窓を作れない組み合わせが生じる
        work = (1280, 700)
        win = self.fit(1100, 800, 1.5, work)
        minimum = self.fit(900, 600, 1.5, work)
        self.assertLessEqual(minimum[0], win[0])
        self.assertLessEqual(minimum[1], win[1])


class ConfigApiUiScaleTests(unittest.TestCase):
    """保存API経由でも正規化が効き、表示用の値が返ること"""

    @classmethod
    def setUpClass(cls):
        wordstore.ensure_data()
        cls.server = app_server._QuietHTTPServer(
            ("127.0.0.1", 0), app_server.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path,
                     body=json.dumps(body) if body else None, headers=headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        conn.close()
        return res.status, data

    def test_get_reports_resolved_and_auto(self):
        status, cfg = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(cfg["ui_scale_auto"], platform_compat.ui_scale())
        self.assertEqual(cfg["ui_scale_resolved"], app_server.resolve_ui_scale(cfg))

    def test_out_of_range_is_clamped_on_save(self):
        # 実環境の data/config.json を書き換えないよう保存だけ止める
        with mock.patch.object(app_server, "save_config"):
            status, res = self.request("POST", "/api/config", {"ui_scale": 99})
        self.assertEqual(status, 200)
        self.assertEqual(res["config"]["ui_scale"], app_server.UI_SCALE_MAX)

    def test_garbage_is_stored_as_auto(self):
        with mock.patch.object(app_server, "save_config"):
            status, res = self.request("POST", "/api/config",
                                       {"ui_scale": "very-big"})
        self.assertEqual(status, 200)
        self.assertEqual(res["config"]["ui_scale"], "auto")


class NormalizeWindowGeometryTests(unittest.TestCase):
    """窓の記憶値の正規化。壊れた記録で起動を止めないこと"""

    def test_valid_entry_kept(self):
        got = app_server.normalize_window_geometry(
            {"cockpit": {"x": 10, "y": 20, "w": 880, "h": 640, "scale": 0.8}})
        self.assertEqual(got, {"cockpit": {"x": 10, "y": 20, "w": 880,
                                           "h": 640, "scale": 0.8}})

    def test_entry_without_size_dropped(self):
        # 位置だけでは復元できない（大きさが決まらない）
        got = app_server.normalize_window_geometry({"cockpit": {"x": 10, "y": 20}})
        self.assertEqual(got, {})

    def test_broken_entry_does_not_drop_others(self):
        got = app_server.normalize_window_geometry(
            {"cockpit": {"w": 880, "h": 640}, "studio": "こわれている"})
        self.assertIn("cockpit", got)
        self.assertNotIn("studio", got)

    def test_bool_is_not_a_coordinate(self):
        # bool は int の派生。True が座標1として通らないこと
        got = app_server.normalize_window_geometry(
            {"cockpit": {"x": True, "w": 880, "h": 640}})
        self.assertNotIn("x", got["cockpit"])

    def test_non_dict_input(self):
        self.assertEqual(app_server.normalize_window_geometry(None), {})
        self.assertEqual(app_server.normalize_window_geometry([1, 2]), {})


class RememberedGeometryTests(unittest.TestCase):
    """前回の大きさ・位置の復元（app.py）"""

    SCREENS = [(0, 0, 1920, 1080)]
    WORK = (1920, 1032)

    @classmethod
    def setUpClass(cls):
        try:
            import app
        except ImportError as e:      # pywebview未導入の環境ではGUI側の検証は行わない
            raise unittest.SkipTest(f"app.py を読み込めません: {e}")
        cls.app = app

    def remember(self, geometry, scale=0.8):
        return self.app._remembered(
            "cockpit", 1100, 800, scale, self.WORK,
            {"window_geometry": geometry}, self.SCREENS)

    def test_no_memory_uses_default_size_and_centering(self):
        width, height, x, y = self.remember({})
        self.assertEqual((width, height), (880, 640))
        self.assertIsNone(x)   # None は pywebview 側の中央配置
        self.assertIsNone(y)

    def test_saved_size_and_position_restored(self):
        width, height, x, y = self.remember(
            {"cockpit": {"x": 40, "y": 60, "w": 760, "h": 540, "scale": 0.8}})
        self.assertEqual((width, height, x, y), (760, 540, 40, 60))

    def test_size_follows_scale_change(self):
        # 0.8で760x540まで縮めた窓を、1.25へ変えて起動した場合
        width, height, _, _ = self.remember(
            {"cockpit": {"x": 40, "y": 60, "w": 760, "h": 540, "scale": 0.8}},
            scale=1.25)
        self.assertEqual((width, height), (1187, 843))

    def test_offscreen_position_is_discarded(self):
        # モニタを外した後などに、掴めない位置へ復元しないこと
        width, height, x, y = self.remember(
            {"cockpit": {"x": -3000, "y": 500, "w": 760, "h": 540, "scale": 0.8}})
        self.assertEqual((width, height), (760, 540))   # 大きさは活かす
        self.assertIsNone(x)
        self.assertIsNone(y)

    def test_size_never_below_minimum(self):
        # 記憶が壊れて極小でも、最小サイズ未満の窓は作らない
        width, height, _, _ = self.remember(
            {"cockpit": {"x": 10, "y": 10, "w": 50, "h": 40, "scale": 0.8}})
        self.assertEqual((width, height), self.app._fit(900, 600, 0.8, self.WORK))

    def test_oversized_memory_clamped_to_largest_screen(self):
        width, height, _, _ = self.remember(
            {"cockpit": {"x": 0, "y": 0, "w": 9000, "h": 9000, "scale": 0.8}})
        self.assertLessEqual(width, 1920)
        self.assertLessEqual(height, 1080)


if __name__ == "__main__":
    unittest.main()
