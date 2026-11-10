"""CTranslate2 の MKL なしビルド（docs/signing/CT2_NOMKL_BUILD.md）の整合

- build.ps1 がビルドする CTranslate2 の版が requirements.txt / ロックファイルと同じこと
  （ずれると、手元とCIで別の版の翻訳エンジンが入る）
- リリースビルドの既定が MKL なし版で、公式版へ戻す入力があること
- CREDITS.md が MKL なし版の中身（Intel のものなし・LLVM OpenMP あり）で書かれていること
- 比較ツールの集計（bench/ct2_mkl_compare/compare.py）
"""
import importlib.util
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


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
        for name in ("ONEDNN_SHA256", "LLVM_OPENMP_SHA256", "LLVM_CMAKE_SHA256"):
            self.assertRegex(self._var(name), r"^[0-9a-f]{64}$", name)

    def test_mkl_off_and_dispatch_on(self):
        for opt in ("-DWITH_MKL=OFF", "-DWITH_DNNL=ON", "-DOPENMP_RUNTIME=COMP",
                    "-DENABLE_CPU_DISPATCH=ON", "-DWITH_CUDA=OFF"):
            self.assertIn(f'"{opt}"', self.ps1, opt)
        self.assertIn("-ffp-contract=off", self.ps1)


class WorkflowTests(unittest.TestCase):
    def test_release_build_uses_nomkl_by_default(self):
        src = _read(".github", "workflows", "release-build-windows.yml")
        m = re.search(r"ctranslate2:\s*\n(?:\s+.*\n)*?\s+default:\s*\"(\w+)\"", src)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "nomkl")
        self.assertIn("options: [nomkl, official]", src)
        self.assertIn("tools\\signing\\ct2_nomkl\\build.ps1", src)
        self.assertIn("iomp|mkl_|^vcomp", src)               # 配布物に残っていないことの確認


class CreditsTests(unittest.TestCase):
    def test_credits_describe_nomkl_build(self):
        src = _read("CREDITS.md")
        self.assertNotIn("Intel oneMKL", src)
        self.assertNotIn("libiomp5md", src)
        self.assertIn("LLVM OpenMP", src)
        self.assertIn("oneDNN", src)


def _load_compare():
    path = os.path.join(ROOT, "bench", "ct2_mkl_compare", "compare.py")
    spec = importlib.util.spec_from_file_location("ct2_compare", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CompareSummaryTests(unittest.TestCase):
    def setUp(self):
        self.c = _load_compare()

    def _run(self, variant, scale, outs, cpu=100.0):
        cases = {}
        for key, _ in self.c.CASES:
            cases[key] = {"load_ms": 10.0, "times": [10.0 * scale, 20.0 * scale],
                          "outs": list(outs), "peak_mb": 500, "cpu_ms": [cpu, cpu]}
        return {"variant": variant, "cases": cases, "backend": [],
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

    def test_not_comparable_when_one_side_failed(self):
        runs = [self._run("dnnl", 1.0, ["a"]), {"variant": "custom", "failed": "x", "backend": []}]
        rep = self.c.summarize(runs, ["dnnl", "custom"], "cpu", 1)
        self.assertEqual(rep["data"]["cases"], {})
        self.assertIn("比較できません", rep["text"])

    def test_variant_selection(self):
        cv = self.c.choose_variants
        self.assertEqual(cv(None, True, False), ["mkl", "dnnl"])
        self.assertEqual(cv(None, True, True), ["mkl", "dnnl", "custom"])
        self.assertEqual(cv(None, False, True), ["dnnl", "custom"])
        self.assertEqual(cv(None, False, False), ["dnnl"])            # AMD で自前ビルドなし: 測らない
        self.assertEqual(cv("custom,dnnl", False, True), ["custom", "dnnl"])
        with self.assertRaises(SystemExit):
            cv("dnnl,custom", False, False)
        with self.assertRaises(SystemExit):
            cv("dnnl,foo", True, True)

    def test_mask_path_hides_user_folders(self):
        home = os.path.expanduser("~")
        p = os.path.join(home, "Downloads", "x", "libomp.dll")
        self.assertNotIn(home, self.c._mask_path(p))
        self.assertTrue(self.c._mask_path(p).startswith(("~", "<tool>")))


if __name__ == "__main__":
    unittest.main()
