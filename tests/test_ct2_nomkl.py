"""CTranslate2 の MKL なしビルド（docs/signing/CT2_NOMKL_BUILD.md）の整合

- build.ps1 がビルドする CTranslate2 の版が requirements.txt / ロックファイルと同じこと
  （ずれると、手元とCIで別の版の翻訳エンジンが入る）。構成は MSVC＋vcomp140
- リリースビルドの既定は公式版のまま（MKL なし版は SignPath に求められたら切り替える控え）で、nomkl を選べること
- CREDITS.md がリリースビルドの既定と同じ版の中身で書かれていること
- MKL なし版の wheel のキャッシュの鍵が、2つのワークフローで同じこと
- translate.py が、vcomp の ctranslate2 のときだけ ctranslate2 を読む前に OMP_WAIT_POLICY=PASSIVE を
  設定し、利用者の指定を上書きしないこと
- 比較ツールの集計（bench/ct2_mkl_compare/compare.py）
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


def _release_default():
    src = _read(".github", "workflows", "release-build-windows.yml")
    m = re.search(r"ctranslate2:\s*\n(?:\s+.*\n)*?\s+default:\s*\"(\w+)\"", src)
    return m.group(1) if m else None


class BuildScriptTests(unittest.TestCase):
    def setUp(self):
        self.ps1 = _read("tools", "signing", "ct2_nomkl", "build.ps1")

    def _var(self, name):
        m = re.search(rf'^\${name}\s*=\s*"([^"]+)"', self.ps1, re.M)
        self.assertIsNotNone(m, name)
        return m.group(1)

    def test_version_matches_requirements(self):
        tag = self._var("CT2_TAG")
        self.assertRegex(tag, r"^v\d+\.\d+\.\d+$")
        ver = tag[1:]
        self.assertIn(f"ctranslate2=={ver}", _read("requirements.txt"))
        self.assertIn(f"ctranslate2=={ver}", _read(".github", "release", "requirements-win.lock"))

    def test_sources_are_pinned(self):
        self.assertRegex(self._var("CT2_COMMIT"), r"^[0-9a-f]{40}$")
        self.assertRegex(self._var("ONEDNN_SHA256"), r"^[0-9a-f]{64}$")

    def test_mkl_off_and_dispatch_on(self):
        for opt in ("-DWITH_MKL=OFF", "-DWITH_DNNL=ON", "-DOPENMP_RUNTIME=COMP",
                    "-DENABLE_CPU_DISPATCH=ON", "-DWITH_CUDA=OFF"):
            self.assertIn(f'"{opt}"', self.ps1, opt)

    def test_translation_code_is_compiled_with_msvc(self):
        # clang-cl で CTranslate2 をコンパイルすると int8 の訳文が公式とずれるので不採用（手順書 2.3）。
        # oneDNN と CTranslate2 は常に cl。clang-cl は -OpenMP llvm の libomp（C のランタイム）だけ
        m = re.search(r'^\$GEN = @\((.+)\)$', self.ps1, re.M)
        self.assertIsNotNone(m)
        self.assertIn("-DCMAKE_CXX_COMPILER=$cl", m.group(1))
        self.assertEqual(self.ps1.count("-DCMAKE_CXX_COMPILER=$clangCl"), 1)   # libomp の genClang だけ
        self.assertIn('[ValidateSet("vcomp", "llvm")][string]$OpenMP = "vcomp"', self.ps1)
        self.assertIn("vcomp140", self.ps1)
        self.assertIn("PrintToolchain", self.ps1)          # キャッシュの鍵

    def test_llvm_variant_uses_pinned_upstream_libomp(self):
        self.assertRegex(self._var("LLVM_OPENMP_SHA256"), r"^[0-9a-f]{64}$")
        self.assertRegex(self._var("LLVM_CMAKE_SHA256"), r"^[0-9a-f]{64}$")
        self.assertIn("releases/download/llvmorg-$LLVM_VERSION", self.ps1)
        self.assertIn("/openmp:llvm", self.ps1)
        self.assertIn("LICENSE.LLVM-openmp", self.ps1)
        self.assertIn("libomp140", self.ps1)               # 再頒布不可の libomp140 に依存したら失敗させる
        self.assertIn('"-DLIBOMP_INSTALL_ALIASES=OFF"', self.ps1)


class WorkflowTests(unittest.TestCase):
    def test_release_build_defaults_to_official(self):
        src = _read(".github", "workflows", "release-build-windows.yml")
        self.assertEqual(_release_default(), "official")
        self.assertIn("options: [official, nomkl]", src)
        self.assertIn("ct2_rebuild:", src)
        self.assertIn("tools\\signing\\ct2_nomkl\\build.ps1", src)
        # MKL なし版で出すときの確認: Intel のもの・他の OpenMP が無く、vcomp140 があること
        self.assertIn("iomp|mkl_|^libomp|^libgomp", src)
        self.assertIn("_internal\\ctranslate2\\vcomp140.dll", src)
        self.assertIn("SHA256SUMS.txt", src)               # キャッシュから戻した wheel の照合

    def test_cache_key_is_shared(self):
        def keys(name):
            src = _read(".github", "workflows", name)
            return set(re.findall(r"key: (ct2-nomkl-.+)", src))
        build = keys("build-ctranslate2-windows.yml")
        release = keys("release-build-windows.yml")
        self.assertEqual(len(build), 1)
        self.assertEqual(len(release), 1)
        # 鍵の中の steps.<id> の名前は違ってよいので、そこだけ揃えて比べる
        norm = lambda s: re.sub(r"steps\.\w+\.outputs", "steps.X.outputs", s)
        self.assertEqual({norm(k) for k in build}, {norm(k) for k in release})
        for k in build:
            self.assertIn("toolchain", k)
            self.assertIn("hashFiles('tools/signing/ct2_nomkl/build.ps1'", k)


class CreditsTests(unittest.TestCase):
    def test_credits_match_release_default(self):
        src = _read("CREDITS.md")
        self.assertIn("oneDNN", src)
        if _release_default() == "official":
            self.assertIn("Intel oneMKL", src)
            self.assertIn("libiomp5md", src)
            self.assertNotIn("libomp)", src)
        else:
            self.assertNotIn("Intel oneMKL", src)
            self.assertNotIn("libiomp5md", src)
            self.assertIn("vcomp140", src)


class TranslateOpenMPTests(unittest.TestCase):
    """translate.py を別プロセスで import し、ダミーの ctranslate2 パッケージで判定を確かめる"""

    def _run(self, dll, env_value=None):
        with tempfile.TemporaryDirectory() as d:
            pkg = os.path.join(d, "ctranslate2")
            os.makedirs(pkg)
            with open(os.path.join(pkg, "__init__.py"), "w") as f:
                f.write("raise ImportError('ctranslate2 must not be imported here')\n")
            if dll:
                open(os.path.join(pkg, dll), "wb").close()
            code = ("import sys, os, json; sys.path[:0] = [sys.argv[1], sys.argv[2]];"
                    "import translate as T;"
                    "print(json.dumps({'omp': os.environ.get('OMP_WAIT_POLICY'),"
                    " 'kind': T.CT2_OPENMP, 'loaded': 'ctranslate2' in sys.modules}))")
            env = dict(os.environ)
            env.pop("OMP_WAIT_POLICY", None)
            if env_value is not None:
                env["OMP_WAIT_POLICY"] = env_value
            p = subprocess.run([sys.executable, "-c", code, d, ROOT], env=env,
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout.strip().splitlines()[-1])

    def test_vcomp_sets_passive_before_import(self):
        r = self._run("vcomp140.dll")
        self.assertEqual(r, {"omp": "PASSIVE", "kind": "msvc", "loaded": False})

    def test_user_value_wins(self):
        self.assertEqual(self._run("vcomp140.dll", "ACTIVE")["omp"], "ACTIVE")

    def test_intel_openmp_untouched(self):
        r = self._run("libiomp5md.dll")
        self.assertEqual(r["kind"], "intel")
        self.assertIsNone(r["omp"])

    def test_upstream_libomp_untouched(self):
        # 上流 LLVM libomp は KMP_BLOCKTIME=5 が効くので PASSIVE にしない
        r = self._run("libomp.dll")
        self.assertEqual(r, {"omp": None, "kind": "llvm", "loaded": False})


def _load_compare():
    path = os.path.join(ROOT, "bench", "ct2_mkl_compare", "compare.py")
    spec = importlib.util.spec_from_file_location("ct2_compare", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CompareSummaryTests(unittest.TestCase):
    def setUp(self):
        self.c = _load_compare()

    def _run(self, variant, scale, outs, cpu=100.0, threads=4):
        cases = {}
        for key, _ in self.c.CASES:
            cases[key] = {"load_ms": 10.0, "times": [10.0 * scale, 20.0 * scale],
                          "outs": list(outs), "peak_mb": 500, "cpu_ms": [cpu, cpu]}
        return {"variant": variant, "threads": threads, "cases": cases, "backend": [],
                "omp_wait_policy": "", "ct2_openmp": "",
                "runtime": {"ctranslate2_version": "4.8.1", "openmp_dlls": []}}

    def test_ratio_and_diffs_against_first_variant(self):
        runs = [self._run("dnnl", 1.0, ["a", "b"]), self._run("custom", 1.1, ["a", "B"], cpu=90),
                self._run("custom", 1.2, ["a", "B"], cpu=80), self._run("dnnl", 1.0, ["a", "b"])]
        rep = self.c.summarize(runs, ["dnnl", "custom"], "cpu", 2)
        en = rep["data"]["cases"]["en_int8"]
        self.assertAlmostEqual(en["dnnl"]["ratio_mean"], 1.0)
        self.assertAlmostEqual(en["custom"]["ratio_mean"], 1.1)   # 2回のうち速い方
        self.assertEqual(en["custom"]["same"], 1)
        self.assertEqual(en["custom"]["diffs"], [{"i": 1, "base": "b", "other": "B"}])
        self.assertEqual(en["custom"]["cpu_ms"], 80)               # 2回のうち少ない方
        self.assertIn("訳文が変わった文", rep["text"])
        self.assertEqual(rep["data"]["sweep"], {})                 # スレッド比較なし

    def test_thread_sweep(self):
        runs = [self._run("dnnl", 1.0, ["a", "b"]), self._run("custom_passive", 2.0, ["a", "b"]),
                self._run("custom_passive", 2.0, ["a", "b"]), self._run("dnnl", 1.0, ["a", "b"])]
        sweep = [self._run("dnnl", 1.5, ["a", "b"], cpu=30, threads=1),
                 self._run("custom_passive", 1.5, ["a", "X"], cpu=20, threads=1),
                 {"variant": "custom_passive", "threads": 2, "failed": "x", "backend": []}]
        rep = self.c.summarize(runs, ["dnnl", "custom_passive"], "cpu", 2, sweep=sweep)
        sw = rep["data"]["sweep"]["en_int8"]
        self.assertEqual(set(sw), {"dnnl", "custom_passive"})
        self.assertEqual(set(sw["custom_passive"]), {"1", "4"})    # 失敗した 2 は載せない
        one = sw["custom_passive"]["1"]
        self.assertAlmostEqual(one["mean"], 22.5)
        self.assertEqual(one["cpu_ms"], 20)
        self.assertEqual((one["same_self"], one["same_base"], one["total"]), (1, 1, 2))
        self.assertAlmostEqual(sw["custom_passive"]["4"]["mean"], 30.0)   # sweep に無い 4 は本計測から
        self.assertIn("スレッド数の比較", rep["text"])
        self.assertIn("custom_passive・2スレッド", rep["text"])     # 失敗の記録

    def test_thread_sweep_prefers_same_condition_run(self):
        runs = [self._run("dnnl", 1.0, ["a", "b"]), self._run("dnnl", 1.0, ["a", "b"], cpu=50)]
        runs = [runs[0], self._run("custom", 1.0, ["a", "b"]), self._run("custom", 1.0, ["a", "b"]),
                runs[1]]
        sweep = [self._run("dnnl", 3.0, ["a", "b"], cpu=70, threads=4),
                 self._run("dnnl", 1.5, ["a", "b"], cpu=30, threads=1)]
        rep = self.c.summarize(runs, ["dnnl", "custom"], "cpu", 2, sweep=sweep)
        four = rep["data"]["sweep"]["en_int8"]["dnnl"]["4"]
        self.assertAlmostEqual(four["mean"], 45.0)                 # 本計測（15.0）ではなく sweep の回
        self.assertEqual(four["cpu_ms"], 70)

    def test_cpu_seconds_is_fine_grained(self):
        # Windows の os.times は 15.6ms 刻み。サイクル数による計測は数 ms の計算も拾う
        if sys.platform != "win32":
            self.skipTest("Windows のみ")
        import time
        c0 = self.c._cpu_seconds()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 0.003:
            pass
        ms = (self.c._cpu_seconds() - c0) * 1000
        self.assertGreater(ms, 1.0)
        self.assertLess(ms, 12.0)
        self.assertIn("QueryProcessCycleTime", self.c._cpu_clock_name())

    def test_not_comparable_when_one_side_failed(self):
        runs = [self._run("dnnl", 1.0, ["a"]), {"variant": "custom", "failed": "x", "backend": []}]
        rep = self.c.summarize(runs, ["dnnl", "custom"], "cpu", 1)
        self.assertEqual(rep["data"]["cases"], {})
        self.assertIn("比較できません", rep["text"])

    def test_variant_selection(self):
        cv = self.c.choose_variants
        self.assertEqual(cv(None, True, {}), ["mkl", "dnnl"])
        self.assertEqual(cv(None, True, {"vcomp"}), ["mkl", "dnnl", "custom_passive", "custom"])
        self.assertEqual(cv(None, False, {"vcomp"}), ["dnnl", "custom_passive", "custom"])
        self.assertEqual(cv(None, False, {}), ["dnnl"])               # AMD で自前ビルドなし: 測らない
        self.assertEqual(cv("custom,dnnl", False, {"vcomp"}), ["custom", "dnnl"])
        # 上流 libomp 版があるとき: vcomp は PASSIVE ありの形だけ（計測の回数を前回と同じに保つ）
        self.assertEqual(cv(None, True, {"vcomp", "llvm"}), ["mkl", "dnnl", "llvm", "custom_passive"])
        self.assertEqual(cv(None, True, {"llvm"}), ["mkl", "dnnl", "llvm"])
        self.assertEqual(cv(None, False, {"llvm"}), ["dnnl", "llvm"])
        with self.assertRaises(SystemExit):
            cv("dnnl,custom_passive", False, {})
        with self.assertRaises(SystemExit):
            cv("dnnl,llvm", False, {"vcomp"})                         # 種類の違う自前ビルドしかない
        with self.assertRaises(SystemExit):
            cv("dnnl,foo", True, {"vcomp"})

    def test_sweep_variants(self):
        sv = self.c.sweep_variants
        self.assertEqual(sv(["mkl", "dnnl", "custom_passive", "custom"]),
                         ["mkl", "custom_passive", "custom"])
        self.assertEqual(sv(["dnnl", "custom_passive", "custom"]),
                         ["dnnl", "custom_passive", "custom"])
        self.assertEqual(sv(["mkl", "dnnl", "llvm", "custom_passive"]),
                         ["mkl", "llvm", "custom_passive"])

    def test_passive_variants_set_wait_policy(self):
        v = self.c.VARIANTS
        self.assertEqual(v["custom_passive"]["env"]["OMP_WAIT_POLICY"], "PASSIVE")
        self.assertEqual(v["custom"]["env"]["OMP_WAIT_POLICY"], "")   # 指定なし（vcomp 既定）
        self.assertEqual(v["llvm"]["env"]["OMP_WAIT_POLICY"], "")     # libomp は KMP_BLOCKTIME で足りる
        self.assertEqual({k: x["custom"] for k, x in v.items() if x["custom"]},
                         {"custom_passive": "vcomp", "custom": "vcomp", "llvm": "llvm"})

    def test_mask_path_hides_user_folders(self):
        home = os.path.expanduser("~")
        p = os.path.join(home, "Downloads", "x", "vcomp140.dll")
        self.assertNotIn(home, self.c._mask_path(p))
        self.assertTrue(self.c._mask_path(p).startswith(("~", "<tool>")))


if __name__ == "__main__":
    unittest.main()
