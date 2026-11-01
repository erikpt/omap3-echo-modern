import os
import tempfile
import unittest
from unittest import mock

from dumpkit import _debugfs
from dumpkit.errors import DumpError
from tests.fixtures import make_mbr_dump, require_tool, set_partition1_files


def ls_p(*rows):
    """Build `ls -p` text from (mode, name) rows, as debugfs prints it."""
    lines = []
    for n, (mode, name) in enumerate(rows, 2):
        size = "" if mode.startswith("04") else "5"
        lines.append("/%d/%s/0/0/%s/%s/\n" % (n, mode, name, size))
    return "".join(lines) + "\n"


DOTS = (("040755", "."), ("040755", ".."))


class FindDebugfsTests(unittest.TestCase):
    def test_missing_debugfs_raises_with_install_hint(self):
        with tempfile.TemporaryDirectory() as empty, \
                mock.patch.dict(os.environ, {"PATH": empty}), \
                mock.patch.object(_debugfs, "_EXTRA_DIRS", ()):
            with self.assertRaises(DumpError) as cm:
                _debugfs.find_debugfs()
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("install e2fsprogs", str(cm.exception))
        self.assertIn("brew --prefix e2fsprogs", str(cm.exception))

    def test_falls_back_to_sbin_directories(self):
        with tempfile.TemporaryDirectory() as empty, tempfile.TemporaryDirectory() as sbin:
            fake = os.path.join(sbin, "debugfs")
            with open(fake, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(fake, 0o755)
            with mock.patch.dict(os.environ, {"PATH": empty}), \
                    mock.patch.object(_debugfs, "_EXTRA_DIRS", ("/nonexistent", sbin)):
                self.assertEqual(_debugfs.find_debugfs(), fake)


class ImageArgTests(unittest.TestCase):
    def test_absolute_path_and_offset(self):
        arg = _debugfs.image_arg("-dash.img", 4096)
        self.assertEqual(arg, os.path.join(os.getcwd(), "-dash.img") + "?offset=4096")

    def test_question_mark_is_refused(self):
        with self.assertRaises(DumpError) as cm:
            _debugfs.image_arg("/tmp/we?ird.img", 0)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("?", str(cm.exception))


class ParseLsPTests(unittest.TestCase):
    def test_plain_entries(self):
        text = ls_p(*DOTS, ("100644", "a.txt"), ("040755", "sub"))
        self.assertEqual(_debugfs.parse_ls_p(text),
                         [(".", True), ("..", True), ("a.txt", False), ("sub", True)])

    def test_names_holding_line_break_characters_are_kept_whole(self):
        names = ["a\x1c_secret", "b\x0b_secret", "c _secret", "d\x85_secret",
                 "e\nsecret", "f\rsecret"]
        text = ls_p(*DOTS, *[("100644", n) for n in names])
        parsed = _debugfs.parse_ls_p(text)
        self.assertEqual([n for n, _ in parsed][2:], names)

    def test_garbage_is_not_a_listing(self):
        self.assertIsNone(_debugfs.parse_ls_p("/2/040755/0/0/./\n"))
        self.assertIsNone(_debugfs.parse_ls_p("something else\n"))
        self.assertIsNone(_debugfs.parse_ls_p(ls_p(*DOTS) + "junk\n"))

    def test_empty_text_has_no_entries(self):
        self.assertEqual(_debugfs.parse_ls_p("\n"), [])


class ListDirTests(unittest.TestCase):
    def fake_run(self, rc=0, out="", err="debugfs 1.47.0 (5-Feb-2023)\n"):
        return mock.patch.object(_debugfs, "run", return_value=(rc, out, err))

    def test_lists_without_dot_entries(self):
        with self.fake_run(out=ls_p(*DOTS, ("100644", "f"))):
            self.assertEqual(_debugfs.list_dir("d", 0, "/"), [("f", False)])

    def test_exit_zero_without_dot_entry_is_unreadable(self):
        # debugfs 1.47 exits 0 after some failures; an empty listing is not a directory.
        with self.fake_run(out="\n"):
            self.assertIsNone(_debugfs.list_dir("d", 0, "/"))
        with self.fake_run(out=ls_p(("100644", "f"))):
            self.assertIsNone(_debugfs.list_dir("d", 0, "/"))

    def test_error_text_on_stderr_is_unreadable_even_with_exit_zero(self):
        for err in ("debugfs: Bad magic number in super-block while trying to open x\n",
                    "/nope: File not found by ext2_lookup \n",
                    "ls: Filesystem not open\n"):
            with self.subTest(err=err):
                with self.fake_run(out=ls_p(*DOTS), err="debugfs 1.47.0 (5-Feb-2023)\n" + err):
                    self.assertIsNone(_debugfs.list_dir("d", 0, "/"))

    def test_nonzero_exit_is_unreadable(self):
        with self.fake_run(rc=1, out=ls_p(*DOTS)):
            self.assertIsNone(_debugfs.list_dir("d", 0, "/"))

    def test_quote_in_path_is_unreadable_without_running_debugfs(self):
        with mock.patch.object(_debugfs, "run") as run:
            self.assertIsNone(_debugfs.list_dir("d", 0, '/a"b'))
        run.assert_not_called()

    def test_real_filesystem_and_non_ext4(self):
        require_tool("debugfs")
        require_tool("mke2fs")
        with tempfile.TemporaryDirectory() as tmp:
            info = make_mbr_dump(tmp)
            names = sorted(n for n, _ in _debugfs.list_dir(info.path, info.partitions[0][0], "/"))
            self.assertEqual(names, ["boot", "etc", "lib", "lost+found"])
            self.assertIsNone(_debugfs.list_dir(info.path, info.partitions[1][0], "/"))
            self.assertIsNone(_debugfs.list_dir(info.path, info.partitions[0][0], "/nope"))
            self.assertIsNone(_debugfs.list_dir(info.path, info.partitions[0][0],
                                                "/etc/wpa_supplicant.conf"))

    def test_real_names_with_line_break_characters(self):
        require_tool("debugfs")
        require_tool("mke2fs")
        with tempfile.TemporaryDirectory() as tmp:
            info = make_mbr_dump(tmp)
            files = {"/a\x1cb_secret": b"x", "/nl\nsecret": b"y", "/u secret": b"z"}
            set_partition1_files(info, files)
            got = {n for n, _ in _debugfs.list_dir(info.path, info.partitions[0][0], "/")}
            self.assertTrue({"a\x1cb_secret", "nl\nsecret", "u secret"} <= got)


if __name__ == "__main__":
    unittest.main()
