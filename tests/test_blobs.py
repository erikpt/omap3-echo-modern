import hashlib
import os
import shutil
import tempfile
import unittest
from unittest import mock

from dumpkit import _debugfs, blobs
from dumpkit.errors import ChecksumMismatch, DumpError, RangeErr
from tests.fixtures import _build_ext4, make_mbr_dump, require_tool

FW = "/lib/firmware/ath6k/fw.bin"
FW_SHA = hashlib.sha256(b"FIRMWARE").hexdigest()
CONF_SHA = hashlib.sha256(b"psk=hunter2").hexdigest()
WRONG = "0" * 64


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _file_digest(path):
    return hashlib.sha256(_read(path)).hexdigest()


class ParseManifestTests(unittest.TestCase):
    def test_parse_manifest_ignores_comments_and_blanks(self):
        text = "# header\n\n1\t/a/b\t%s\tb.bin\n   \n# trailing\n" % FW_SHA
        self.assertEqual(blobs.parse_manifest(text),
                         [blobs.BlobEntry(1, "/a/b", FW_SHA, "b.bin")])

    def test_parse_manifest_keeps_spaces_in_path(self):
        entries = blobs.parse_manifest("2\t/lib/my fw/a b.bin\t%s\tout.bin\n" % FW_SHA)
        self.assertEqual(entries[0].fs_path, "/lib/my fw/a b.bin")
        self.assertEqual(entries[0].partition, 2)

    def test_parse_manifest_malformed_line_reports_line_number(self):
        bad_lines = [
            "x\t/p\t%s\td" % FW_SHA,          # partition not an integer
            "1\t/p\t%s" % FW_SHA,             # three fields
            "1\t/p\t%s\td\textra" % FW_SHA,   # five fields
            "1\t/p\tABC\td",                  # not a sha256
            "1\t/p\t%s\td" % FW_SHA.upper(),  # uppercase hex
            "0\t/p\t%s\td" % FW_SHA,          # partition index below 1
            "1\t\t%s\td" % FW_SHA,            # empty path
            '1\t/p"q\t%s\td' % FW_SHA,        # double quote cannot be passed to debugfs
        ]
        for bad in bad_lines:
            with self.subTest(line=bad):
                text = "# c\n1\t/ok\t%s\tok.bin\n%s\n" % (FW_SHA, bad)
                with self.assertRaisesRegex(ValueError, r"line 3\b"):
                    blobs.parse_manifest(text)

    def test_parse_manifest_dest_must_be_bare_file_name(self):
        for dest in ["", "a/b", "../x", "a\\b", ".", "..", "/abs"]:
            with self.subTest(dest=dest):
                with self.assertRaisesRegex(ValueError, r"line 2\b"):
                    blobs.parse_manifest("# c\n1\t/p\t%s\t%s\n" % (FW_SHA, dest))

    def test_parse_manifest_duplicate_dest_reports_line_number(self):
        text = "1\t/a\t%s\tsame.bin\n1\t/b\t%s\tsame.bin\n" % (FW_SHA, FW_SHA)
        with self.assertRaisesRegex(ValueError, r"line 2\b"):
            blobs.parse_manifest(text)


class ExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_tool("debugfs")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.info = make_mbr_dump(self.dir)
        self.outdir = os.path.join(self.dir, "out")
        os.mkdir(self.outdir)
        self.manifest = os.path.join(self.dir, "blobs.manifest")
        self.dump_before = _file_digest(self.info.path)

    def write_manifest(self, *rows):
        with open(self.manifest, "w", encoding="utf-8") as f:
            f.write("".join("%d\t%s\t%s\t%s\n" % r for r in rows))

    def tearDown(self):
        # The dump is never modified, whatever the test did.
        self.assertEqual(_file_digest(self.info.path), self.dump_before)

    def test_extract_writes_verified_file(self):
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        result = blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(result, [os.path.join(self.outdir, "fw.bin")])
        self.assertEqual(_read(result[0]), b"FIRMWARE")
        self.assertEqual(os.listdir(self.outdir), ["fw.bin"])

    def test_extract_multiple_entries_in_manifest_order(self):
        self.write_manifest((1, FW, FW_SHA, "fw.bin"),
                            (1, "/etc/wpa_supplicant.conf", CONF_SHA, "conf"))
        result = blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual([os.path.basename(p) for p in result], ["fw.bin", "conf"])
        self.assertEqual(sorted(os.listdir(self.outdir)), ["conf", "fw.bin"])

    def test_checksum_mismatch_leaves_outdir_empty(self):
        self.write_manifest((1, FW, WRONG, "fw.bin"))
        with self.assertRaises(ChecksumMismatch) as cm:
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(cm.exception.exit_code, 4)
        msg = str(cm.exception)
        self.assertIn("fw.bin", msg)
        self.assertIn(WRONG, msg)
        self.assertIn(FW_SHA, msg)
        self.assertEqual(os.listdir(self.outdir), [])

    def test_missing_partition_raises_range_err(self):
        self.write_manifest((9, FW, FW_SHA, "fw.bin"))
        with self.assertRaises(RangeErr):
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(os.listdir(self.outdir), [])

    def test_second_entry_failure_discards_first(self):
        self.write_manifest((1, FW, FW_SHA, "fw.bin"),
                            (1, "/etc/wpa_supplicant.conf", WRONG, "conf"))
        with self.assertRaises(ChecksumMismatch):
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(os.listdir(self.outdir), [])

    def test_missing_second_partition_extracts_nothing(self):
        self.write_manifest((1, FW, FW_SHA, "fw.bin"), (9, FW, FW_SHA, "other"))
        with self.assertRaises(RangeErr):
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(os.listdir(self.outdir), [])

    def test_failure_with_nonexistent_outdir_leaves_nothing_behind(self):
        outdir = os.path.join(self.dir, "new", "nested", "out")
        self.write_manifest((1, FW, FW_SHA, "fw.bin"), (1, FW, WRONG, "bad"))
        with self.assertRaises(ChecksumMismatch):
            blobs.extract_blobs(self.info.path, self.manifest, outdir)
        self.assertFalse(os.path.exists(outdir))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "new")))

    def test_success_creates_missing_outdir(self):
        outdir = os.path.join(self.dir, "fresh", "out")
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        blobs.extract_blobs(self.info.path, self.manifest, outdir)
        self.assertEqual(os.listdir(outdir), ["fw.bin"])

    def test_failure_preserves_preexisting_outdir_content(self):
        keep = os.path.join(self.outdir, "fw.bin")
        with open(keep, "wb") as f:
            f.write(b"OLD")
        self.write_manifest((1, FW, FW_SHA, "fw.bin"), (1, FW, WRONG, "bad"))
        with self.assertRaises(ChecksumMismatch):
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(os.listdir(self.outdir), ["fw.bin"])
        self.assertEqual(_read(keep), b"OLD")

    def test_success_replaces_existing_file(self):
        with open(os.path.join(self.outdir, "fw.bin"), "wb") as f:
            f.write(b"OLD")
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(_read(os.path.join(self.outdir, "fw.bin")), b"FIRMWARE")
        self.assertEqual(os.listdir(self.outdir), ["fw.bin"])

    def test_existing_directory_at_dest_is_refused_and_untouched(self):
        keep = os.path.join(self.outdir, "fw.bin", "keep")
        os.mkdir(os.path.dirname(keep))
        with open(keep, "wb") as f:
            f.write(b"KEEP")
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        with self.assertRaises(DumpError) as cm:
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("fw.bin", str(cm.exception))
        self.assertEqual(os.listdir(self.outdir), ["fw.bin"])
        self.assertEqual(os.listdir(os.path.join(self.outdir, "fw.bin")), ["keep"])
        self.assertEqual(_read(keep), b"KEEP")

    def test_install_failure_restores_replaced_files(self):
        old_a = os.path.join(self.outdir, "fw.bin")
        old_b = os.path.join(self.outdir, "conf")
        for path, data in ((old_a, b"OLD-A"), (old_b, b"OLD-B")):
            with open(path, "wb") as f:
                f.write(data)
        self.write_manifest((1, FW, FW_SHA, "fw.bin"),
                            (1, "/etc/wpa_supplicant.conf", CONF_SHA, "conf"))
        real_replace = os.replace

        def failing_replace(src, dst, *args, **kwargs):
            # Fail the final move of the second staged file (blob-1) into outdir.
            if os.path.basename(src) == "blob-1":
                raise OSError("injected failure")
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch("dumpkit.blobs.os.replace", side_effect=failing_replace):
            with self.assertRaises(OSError):
                blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(sorted(os.listdir(self.outdir)), ["conf", "fw.bin"])
        self.assertEqual(_read(old_a), b"OLD-A")
        self.assertEqual(_read(old_b), b"OLD-B")

    def test_file_not_found_raises_dump_error_naming_entry(self):
        self.write_manifest((1, "/no/such/file", FW_SHA, "x"))
        with self.assertRaises(DumpError) as cm:
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertNotIsInstance(cm.exception, (ChecksumMismatch, RangeErr))
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("/no/such/file", str(cm.exception))
        self.assertIn("partition 1", str(cm.exception))
        self.assertEqual(os.listdir(self.outdir), [])

    def test_non_ext4_partition_raises_dump_error(self):
        self.write_manifest((2, FW, FW_SHA, "x"))
        with self.assertRaises(DumpError) as cm:
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("partition 2", str(cm.exception))
        self.assertEqual(os.listdir(self.outdir), [])

    def test_fs_path_with_spaces(self):
        # Build a dump copy whose partition 1 holds a path containing spaces.
        copy = os.path.join(self.dir, "copy.img")
        shutil.copyfile(self.info.path, copy)
        start, size = self.info.partitions[0]
        img = os.path.join(self.dir, "sp.ext4")
        _build_ext4(img, size, {"/dir with space/a b.bin": b"SPACED"})
        with open(img, "rb") as src, open(copy, "r+b") as dst:
            dst.seek(start)
            shutil.copyfileobj(src, dst)
        sha = hashlib.sha256(b"SPACED").hexdigest()
        self.write_manifest((1, "/dir with space/a b.bin", sha, "spaced.bin"))
        result = blobs.extract_blobs(copy, self.manifest, self.outdir)
        self.assertEqual(_read(result[0]), b"SPACED")

    def test_zero_byte_file_with_matching_checksum(self):
        copy = os.path.join(self.dir, "copy.img")
        shutil.copyfile(self.info.path, copy)
        start, size = self.info.partitions[0]
        img = os.path.join(self.dir, "empty.ext4")
        _build_ext4(img, size, {"/empty": b""})
        with open(img, "rb") as src, open(copy, "r+b") as dst:
            dst.seek(start)
            shutil.copyfileobj(src, dst)
        self.write_manifest((1, "/empty", hashlib.sha256(b"").hexdigest(), "empty.bin"))
        result = blobs.extract_blobs(copy, self.manifest, self.outdir)
        self.assertEqual(_read(result[0]), b"")

    def test_dump_path_with_question_mark_is_refused(self):
        odd = os.path.join(self.dir, "we?ird.img")
        shutil.copyfile(self.info.path, odd)
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        with self.assertRaises(DumpError) as cm:
            blobs.extract_blobs(odd, self.manifest, self.outdir)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("?", str(cm.exception))
        self.assertEqual(os.listdir(self.outdir), [])

    def test_relative_dump_path_starting_with_dash(self):
        shutil.copyfile(self.info.path, os.path.join(self.dir, "-dash.img"))
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        old = os.getcwd()
        os.chdir(self.dir)
        try:
            result = blobs.extract_blobs("-dash.img", self.manifest, self.outdir)
        finally:
            os.chdir(old)
        self.assertEqual(_read(result[0]), b"FIRMWARE")

    def _clash(self, dump_name, manifest_name, dest):
        """Dump and manifest live in the output directory under the given names."""
        dump = os.path.join(self.outdir, dump_name)
        shutil.copyfile(self.info.path, dump)
        os.chmod(dump, 0o640)
        manifest = os.path.join(self.outdir, manifest_name)
        with open(manifest, "w", encoding="utf-8") as f:
            f.write("1\t%s\t%s\t%s\n" % (FW, FW_SHA, dest))
        return dump, manifest, _read(dump), _read(manifest)

    def test_dest_equal_to_the_dump_is_refused_and_dump_untouched(self):
        dump, manifest, dump_bytes, _m = self._clash("x.bin", "m.manifest", "x.bin")
        mode = os.stat(dump).st_mode
        with self.assertRaises(DumpError) as cm:
            blobs.extract_blobs(dump, manifest, self.outdir)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("x.bin", str(cm.exception))
        self.assertIn("dump", str(cm.exception))
        self.assertEqual(_read(dump), dump_bytes)
        self.assertEqual(os.stat(dump).st_mode, mode)
        self.assertEqual(sorted(os.listdir(self.outdir)), ["m.manifest", "x.bin"])

    def test_dest_equal_to_the_manifest_is_refused_and_manifest_untouched(self):
        dump, manifest, _d, manifest_bytes = self._clash("d.img", "blobs.manifest",
                                                         "blobs.manifest")
        with self.assertRaises(DumpError) as cm:
            blobs.extract_blobs(dump, manifest, self.outdir)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("manifest", str(cm.exception))
        self.assertEqual(_read(manifest), manifest_bytes)

    def test_dest_that_is_a_symlink_to_the_dump_is_refused(self):
        dump, manifest, dump_bytes, _m = self._clash("d.img", "m.manifest", "link.bin")
        os.symlink(dump, os.path.join(self.outdir, "link.bin"))
        with self.assertRaises(DumpError):
            blobs.extract_blobs(dump, manifest, self.outdir)
        self.assertEqual(_read(dump), dump_bytes)
        self.assertTrue(os.path.islink(os.path.join(self.outdir, "link.bin")))

    def test_missing_debugfs_is_a_clear_error_and_leaves_outdir_empty(self):
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        with tempfile.TemporaryDirectory() as empty, \
                mock.patch.dict(os.environ, {"PATH": empty}), \
                mock.patch.object(_debugfs, "_EXTRA_DIRS", ()):
            with self.assertRaises(DumpError) as cm:
                blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("install e2fsprogs", str(cm.exception))
        self.assertEqual(os.listdir(self.outdir), [])

    def test_uses_the_shared_debugfs_helper(self):
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        with mock.patch.object(_debugfs, "run", wraps=_debugfs.run) as run:
            blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual(run.call_count, 1)

    def test_staging_directory_is_removed(self):
        self.write_manifest((1, FW, FW_SHA, "fw.bin"))
        blobs.extract_blobs(self.info.path, self.manifest, self.outdir)
        self.assertEqual([n for n in os.listdir(self.outdir) if n.startswith(".stage-")], [])


if __name__ == "__main__":
    unittest.main()
