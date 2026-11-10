"""モデル取得の版（コミットID）固定

- 取りに行く版は model_pins.REVISIONS のコミットIDに固定されていること
- 既存の利用者（版固定前に main で取得したキャッシュ・同梱版・引き継いだ models\\）では、
  通信も再ダウンロードも起きないこと
- 手元に何も無いときだけ、固定したコミットIDを取得すること

実際の通信はしない（偽の HF キャッシュを一時フォルダに作り、取得関数はモックで見張る）。
"""
import os
import re
import tempfile
import unittest
from unittest import mock

import huggingface_hub as hf
from huggingface_hub import constants

import model_pins

ROOT = os.path.join(os.path.dirname(__file__), "..")
OLD = "0" * 40     # 版固定前に main で取得した、固定IDとは別の古い版


def _make_cache(cache, repo, commit, files, ref_main=True):
    """HF キャッシュの構造（models--org--name/{refs/main, snapshots/<commit>/...}）を作る"""
    d = os.path.join(cache, "models--" + repo.replace("/", "--"))
    snap = os.path.join(d, "snapshots", commit)
    os.makedirs(snap, exist_ok=True)
    for name in files:
        with open(os.path.join(snap, name), "w", encoding="utf-8") as f:
            f.write(commit)
    if ref_main:
        os.makedirs(os.path.join(d, "refs"), exist_ok=True)
        with open(os.path.join(d, "refs", "main"), "w", encoding="utf-8") as f:
            f.write(commit)
    return snap


class _NoNetwork(Exception):
    pass


class ResolveTests(unittest.TestCase):
    REPO = "ishiki-emo/mojicast-fugumt-ja-en-ct2"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = self.tmp.name
        p = mock.patch.object(constants, "HF_HUB_CACHE", self.cache)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.pin = model_pins.REVISIONS[self.REPO]
        # 通信を伴う呼び出し（local_files_only でない呼び出し）を見張る
        self.net_calls = []
        real_snap, real_file = hf.snapshot_download, hf.hf_hub_download

        def snap(repo_id, **kw):
            if not kw.get("local_files_only"):
                self.net_calls.append(("snapshot", repo_id, kw.get("revision")))
                raise _NoNetwork()
            return real_snap(repo_id, **kw)

        def file(repo_id, filename, **kw):
            if not kw.get("local_files_only"):
                self.net_calls.append(("file", repo_id, filename, kw.get("revision")))
                raise _NoNetwork()
            return real_file(repo_id, filename, **kw)

        for name, fn in (("snapshot_download", snap), ("hf_hub_download", file)):
            p = mock.patch.object(hf, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    # --- 既存の利用者: 再ダウンロードしない ---
    def test_cache_at_pinned_commit_is_used_without_network(self):
        # 開発機・同梱版・インストール版の現状（refs/main ＝ 固定ID）
        snap = _make_cache(self.cache, self.REPO, self.pin, ["model.bin"])
        self.assertEqual(os.path.normcase(model_pins.resolve_snapshot(self.REPO)),
                         os.path.normcase(snap))
        self.assertEqual(self.net_calls, [])

    def test_older_cache_from_unpinned_versions_is_kept(self):
        # 版固定前に main を取得した利用者（固定IDとは別の版）。再取得しない
        snap = _make_cache(self.cache, self.REPO, OLD, ["model.bin"])
        self.assertEqual(os.path.normcase(model_pins.resolve_snapshot(self.REPO)),
                         os.path.normcase(snap))
        path = model_pins.resolve_file(self.REPO, "model.bin")
        self.assertEqual(os.path.normcase(path),
                         os.path.normcase(os.path.join(snap, "model.bin")))
        self.assertEqual(self.net_calls, [])

    def test_pinned_download_without_refs_main_is_found_offline(self):
        # 固定IDで新しく取得した人（コミットID指定の取得は refs/main を書かない）
        snap = _make_cache(self.cache, self.REPO, self.pin, ["model.bin"], ref_main=False)
        self.assertEqual(os.path.normcase(model_pins.resolve_snapshot(self.REPO, download=False)),
                         os.path.normcase(snap))
        self.assertTrue(model_pins.resolve_file(self.REPO, "model.bin", download=False))
        self.assertEqual(self.net_calls, [])

    def test_pinned_snapshot_is_preferred_over_older_one(self):
        _make_cache(self.cache, self.REPO, OLD, ["model.bin"])
        snap = _make_cache(self.cache, self.REPO, self.pin, ["model.bin"], ref_main=False)
        self.assertEqual(os.path.normcase(model_pins.resolve_snapshot(self.REPO)),
                         os.path.normcase(snap))

    # --- 手元に無い: 固定IDを取得 ---
    def test_missing_downloads_pinned_revision(self):
        with self.assertRaises(_NoNetwork):
            model_pins.resolve_snapshot(self.REPO, allow_patterns=["model.bin"])
        self.assertEqual(self.net_calls, [("snapshot", self.REPO, self.pin)])
        self.net_calls.clear()
        with self.assertRaises(_NoNetwork):
            model_pins.resolve_file(self.REPO, "model.bin")
        self.assertEqual(self.net_calls, [("file", self.REPO, "model.bin", self.pin)])

    def test_missing_file_in_old_snapshot_downloads_pinned(self):
        # 古い版のキャッシュに無いファイル（例: 句読点 int8 追加前の利用者）は固定IDから取る
        _make_cache(self.cache, self.REPO, OLD, ["other.bin"])
        with self.assertRaises(_NoNetwork):
            model_pins.resolve_file(self.REPO, "model.bin")
        self.assertEqual(self.net_calls, [("file", self.REPO, "model.bin", self.pin)])

    def test_download_false_never_touches_network(self):
        with self.assertRaises(Exception) as cm:
            model_pins.resolve_snapshot(self.REPO, download=False)
        self.assertNotIsInstance(cm.exception, _NoNetwork)
        with self.assertRaises(Exception) as cm:
            model_pins.resolve_file(self.REPO, "model.bin", download=False)
        self.assertNotIsInstance(cm.exception, _NoNetwork)
        self.assertEqual(self.net_calls, [])


class CallSiteTests(unittest.TestCase):
    """各モジュールのダウンロードが model_pins を通り、取得元がすべて固定されていること"""
    MODULES = ("soundfx.py", "punct.py", "asr_model.py", "translate.py")

    def test_revisions_are_full_commit_ids(self):
        for repo, rev in model_pins.REVISIONS.items():
            self.assertRegex(rev, r"^[0-9a-f]{40}$", repo)

    def test_no_direct_download_calls(self):
        for name in self.MODULES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                src = f.read()
            self.assertNotRegex(src, r"\b(snapshot_download|hf_hub_download)\(", name)

    def test_every_repo_is_pinned(self):
        import asr_model
        import punct
        import soundfx
        import translate
        repos = {asr_model._REPO_ID, asr_model._SV_REPO, punct._REPO_ID,
                 soundfx._REPO, translate._REPO_ID, translate._REPO_ID_M2M}
        self.assertEqual(repos, set(model_pins.REVISIONS))

    def test_callers_pass_through(self):
        import asr_model
        import punct
        import soundfx
        import translate
        with tempfile.TemporaryDirectory() as snap, tempfile.TemporaryDirectory() as empty:
            # スナップショットの中身の確認（model.int8.onnx / model.bin）を通すための偽ファイル
            for name in ("model.int8.onnx", "model.bin"):
                open(os.path.join(snap, name), "w").close()
            # models_conv（開発・同梱版の変換済みモデル）は無い前提にする
            with mock.patch.object(punct, "BASE", empty), \
                    mock.patch.object(translate, "BASE", empty), \
                    mock.patch.object(model_pins, "resolve_snapshot", return_value=snap) as rs, \
                    mock.patch.object(model_pins, "resolve_file", return_value="f") as rf:
                soundfx._resolve(download=False)
                asr_model._resolve_sensevoice(download=False)
                asr_model.cached("k2-ja")
                asr_model._resolve_files()
                translate._resolve_dir(download=False)
                translate._resolve_dir_m2m(download=True)
                punct._resolve("punct_bert.int8.onnx", download=False)
        self.assertEqual([(c.args[0], c.kwargs.get("download", True)) for c in rs.call_args_list],
                         [(soundfx._REPO, False), (asr_model._SV_REPO, False),
                          (asr_model._REPO_ID, False), (asr_model._REPO_ID, True),
                          (translate._REPO_ID, False), (translate._REPO_ID_M2M, True)])
        rf.assert_called_once_with(punct._REPO_ID, "punct_bert.int8.onnx", download=False)


class DocsTests(unittest.TestCase):
    def test_update_procedure_lists_every_pin(self):
        with open(os.path.join(ROOT, "docs", "MODEL_PINS.md"), encoding="utf-8") as f:
            doc = f.read()
        for repo, rev in model_pins.REVISIONS.items():
            self.assertIn(repo, doc)
            self.assertIn(rev, doc)
        self.assertTrue(re.search(r"model_pins\.py", doc))


if __name__ == "__main__":
    unittest.main()
