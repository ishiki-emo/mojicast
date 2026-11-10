"""GitHub Actions のワークフローの安全設定

- 使う action はすべて完全なコミットID（40桁）で固定し、版をコメントに併記する
  （タグは付け替えられるので、同じタグ名でも中身が変わりうる）
- ワークフローの既定の権限は読み取りだけにする（permissions: contents: read）
"""
import glob
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
WORKFLOWS = sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))
                   + glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yaml")))
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$")


class WorkflowTests(unittest.TestCase):
    def test_workflows_exist(self):
        names = {os.path.basename(p) for p in WORKFLOWS}
        self.assertIn("ci.yml", names)

    def test_actions_are_pinned_to_commit_ids(self):
        for path in WORKFLOWS:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            found = 0
            for no, line in enumerate(lines, 1):
                m = USES.match(line)
                if not m:
                    continue
                found += 1
                ref, rest = m.group(1), m.group(2)
                where = f"{os.path.basename(path)}:{no}"
                if ref.startswith("./"):
                    continue                       # 同じリポジトリ内の action
                self.assertRegex(ref, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$", where)
                self.assertRegex(rest, r"#\s*v\d+(\.\d+)*", where + " 版のコメントが無い")
            self.assertGreater(found, 0, path)

    def test_default_permissions_are_read_only(self):
        for path in WORKFLOWS:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            # 最上位（字下げなし）の permissions ブロック
            m = re.search(r"^permissions:\s*\n((?:[ \t]+.*\n?)+)", src, re.M)
            self.assertIsNotNone(m, f"{os.path.basename(path)}: permissions が無い")
            block = dict(re.findall(r"^\s+([\w-]+):\s*(\S+)", m.group(1), re.M))
            self.assertEqual(block, {"contents": "read"}, os.path.basename(path))
            self.assertNotIn("write-all", src)


if __name__ == "__main__":
    unittest.main()
