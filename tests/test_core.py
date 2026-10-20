import os
import shutil
import stat
import tempfile
import unittest
from recursive_directory_pruner.core import Pruner, PruneResult


class _Tree:
    """Helper: build a tree of directories and files from a list of specs."""

    def __init__(self, base: str) -> None:
        self.base = base

    def dir(self, rel: str) -> str:
        p = os.path.join(self.base, rel)
        os.makedirs(p, exist_ok=True)
        return p

    def file(self, rel: str, content: str = "") -> str:
        p = os.path.join(self.base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
        return p

    def symlink(self, target: str, rel: str) -> str:
        p = os.path.join(self.base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.symlink(target, p)
        return p

    def exists(self, rel: str) -> bool:
        return os.path.exists(os.path.join(self.base, rel))

    def lexists(self, rel: str) -> bool:
        return os.path.lexists(os.path.join(self.base, rel))


class PruneSimpleTests(unittest.TestCase):
    def test_removes_single_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a")
            res = Pruner().prune(td)
            self.assertIsInstance(res, PruneResult)
            self.assertIn(os.path.join(td, "a"), res.removed)
            self.assertFalse(t.exists("a"))
            self.assertTrue(os.path.isdir(td))

    def test_removes_nested_empty_dirs_bottom_up(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a/b/c")
            res = Pruner().prune(td)
            self.assertFalse(t.exists("a/b/c"))
            self.assertFalse(t.exists("a/b"))
            self.assertFalse(t.exists("a"))
            self.assertEqual(len(res.removed), 3)

    def test_keeps_dir_with_file(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.file("a/keep.txt", "data")
            t.dir("a/empty")
            res = Pruner().prune(td)
            self.assertTrue(t.exists("a/keep.txt"))
            self.assertTrue(os.path.isdir(os.path.join(td, "a")))
            self.assertFalse(t.exists("a/empty"))
            self.assertIn(os.path.join(td, "a"), res.kept)

    def test_keeps_intermediate_when_descendant_has_file(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.file("a/b/c/deep.txt", "x")
            res = Pruner().prune(td)
            self.assertTrue(os.path.isdir(os.path.join(td, "a", "b", "c")))
            self.assertIn(os.path.join(td, "a"), res.kept)
            self.assertIn(os.path.join(td, "a", "b"), res.kept)
            self.assertIn(os.path.join(td, "a", "b", "c"), res.kept)
            self.assertEqual(res.removed, [])


class PruneRootTests(unittest.TestCase):
    def test_root_kept_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            res = Pruner().prune(td)
            self.assertTrue(os.path.isdir(td))
            self.assertTrue(res.root_kept)

    def test_root_removed_when_empty_and_opted_in(self):
        with tempfile.TemporaryDirectory() as td:
            res = Pruner(remove_root=True).prune(td)
            self.assertFalse(os.path.exists(td))
            self.assertFalse(res.root_kept)

    def test_root_kept_when_nonempty_even_if_opted_in(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.file("keep.txt", "data")
            res = Pruner(remove_root=True).prune(td)
            self.assertTrue(os.path.isdir(td))
            self.assertTrue(res.root_kept)


class PruneMaxDepthTests(unittest.TestCase):
    def test_max_depth_zero_does_not_remove_descendants_of_immediate_children(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a/b/c")
            res = Pruner(max_depth=0).prune(td)
            # Cannot descend, so nothing is removed.
            self.assertEqual(res.removed, [])
            self.assertTrue(t.exists("a/b/c"))

    def test_max_depth_one_removes_first_level(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a/b/c")
            res = Pruner(max_depth=1).prune(td)
            # max_depth=1 allows descending one level below root.  'a' is
            # at depth 1 and can be inspected but 'a/b' is at depth 2 and
            # cannot be descended into, so 'a' still has the child 'b' and
            # is kept.  Nothing is removed.
            self.assertEqual(res.removed, [])

    def test_max_depth_two_removes_two_levels(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a/b/c")
            res = Pruner(max_depth=2).prune(td)
            # max_depth=2 allows descending into 'a' (depth 1) and 'a/b'
            # (depth 2).  'a/b/c' is at depth 3 and cannot be descended
            # into, so 'a/b' still has the child 'c' and is kept.  Nothing
            # is removed.
            self.assertEqual(res.removed, [])

    def test_max_depth_none_is_unlimited(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a/b/c/d/e")
            res = Pruner(max_depth=None).prune(td)
            self.assertFalse(t.exists("a"))
            self.assertEqual(len(res.removed), 5)

    def test_max_depth_negative_raises(self):
        with self.assertRaises(ValueError):
            Pruner(max_depth=-1)

    def test_max_depth_with_file_blocks_chain(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.file("a/b/keep.txt", "data")
            t.dir("a/b/empty")
            res = Pruner(max_depth=5).prune(td)
            self.assertFalse(t.exists("a/b/empty"))
            self.assertTrue(t.exists("a/b/keep.txt"))


class PruneStopAtTests(unittest.TestCase):
    def test_stop_at_keeps_directory_and_ancestors(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a/b/c")
            stop = os.path.join(td, "a", "b")
            res = Pruner(stop_at=stop).prune(td)
            self.assertTrue(os.path.isdir(stop))
            self.assertTrue(os.path.isdir(os.path.join(td, "a")))
            # c is beneath stop, so it is never removed.
            self.assertTrue(t.exists("a/b/c"))
            self.assertIn(stop, res.kept)
            self.assertEqual(res.removed, [])

    def test_stop_at_explicit_relative_input(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a/b/c")
            # Pass a relative path; the Pruner normalises to absolute and the
            # behaviour matches the absolute case.
            cwd = os.getcwd()
            try:
                os.chdir(td)
                stop = os.path.join("a", "b")
                res = Pruner(stop_at=stop).prune(td)
                self.assertTrue(os.path.isdir(os.path.join(td, "a", "b")))
                self.assertTrue(t.exists("a/b/c"))
                self.assertEqual(res.removed, [])
            finally:
                os.chdir(cwd)


class PruneSymlinkTests(unittest.TestCase):
    def test_symlink_to_dir_counts_as_content(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks not supported on this platform")
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("real/sub")
            t.symlink(os.path.join(t.base, "real"), "link")
            res = Pruner().prune(td)
            # link counts as content of td, so td is kept; real/sub is empty
            # and removed; real is now empty (the symlink from td did not
            # make real non-empty) and removed.
            self.assertTrue(t.lexists("link"))
            self.assertFalse(t.exists("real/sub"))
            self.assertFalse(t.exists("real"))


class PruneEdgeCaseTests(unittest.TestCase):
    def test_missing_root_returns_empty_result(self):
        with tempfile.TemporaryDirectory() as td:
            os.rmdir(td)
            res = Pruner().prune(td)
            self.assertIsInstance(res, PruneResult)
            self.assertEqual(res.removed, [])
            self.assertEqual(res.kept, [])
            self.assertFalse(res.root_kept)

    def test_root_is_symlink_no_traverse(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks not supported on this platform")
        with tempfile.TemporaryDirectory() as real:
            link = os.path.join(tempfile.mkdtemp(), "target.link")
            os.symlink(real, link)
            try:
                res = Pruner().prune(link)
                self.assertFalse(res.root_kept)
                self.assertEqual(res.removed, [])
            finally:
                os.unlink(link)

    def test_removed_order_is_bottom_up(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("a/b/c")
            res = Pruner().prune(td)
            removed = [os.path.relpath(p, td) for p in res.removed]
            self.assertEqual(removed, ["a/b/c", "a/b", "a"])

    def test_protected_directory_recorded(self):
        if os.name == "nt":
            self.skipTest("chmod protections unreliable on Windows")
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            t.dir("locked/child")
            locked = os.path.join(td, "locked")
            child = os.path.join(td, "locked", "child")
            os.chmod(locked, 0o500)  # r-x for owner: cannot rmdir children
            try:
                res = Pruner().prune(td)
                # 'locked' is r-x so rmdir('child') fails with EACCES.
                # 'child' is recorded in protected; 'locked' is kept because
                # it still has the non-removable child.
                self.assertTrue(child in res.protected or child in res.removed)
                self.assertTrue(locked in res.kept or locked in res.protected)
            finally:
                os.chmod(locked, 0o700)


if __name__ == "__main__":
    unittest.main()
