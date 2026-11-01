import hashlib
import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import dump_tool
from dumpkit import _debugfs, triage
from dumpkit.errors import DumpError
from tests.fixtures import make_gpt_dump, make_mbr_dump, require_tool, set_partition1_files

SECTIONS = [
    "# Dump triage",
    "## File",
    "## Partitions",
    "## Pattern hits",
    "## Command-line fragments",
    "## Secret-like files",
]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def section(report, heading):
    """Return the non-blank lines of one report section (without its heading)."""
    lines = report.splitlines()
    start = lines.index(heading) + 1
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return [l for l in lines[start:end] if l.strip()]


class TriageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.out = os.path.join(self.dir, "out", "nested")

    def run_triage(self, dump):
        path = triage.triage(dump, self.out)
        self.assertEqual(path, os.path.join(self.out, "triage.md"))
        with open(path, encoding="utf-8") as f:
            return f.read()

    def raw_image(self, name, pieces, size=64 * 1024):
        """Zero-filled file with (offset, bytes) pieces written in."""
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.truncate(size)
            for offset, data in pieces:
                f.seek(offset)
                f.write(data)
        return path

    def test_report_has_sections_in_order(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.dir)
        report = self.run_triage(info.path)
        positions = [report.index("\n" + s + "\n") if i else report.index(s)
                     for i, s in enumerate(SECTIONS)]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(positions), len(set(positions)))

    def test_file_section_has_sha256(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.dir)
        report = self.run_triage(info.path)
        text = "\n".join(section(report, "## File"))
        self.assertIn(sha256_of(info.path), text)
        self.assertIn(str(os.path.getsize(info.path)), text)
        self.assertRegex(text, r"(?i)multiple of 512: yes")

    def test_finds_cmdline_tokens(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.dir)
        report = self.run_triage(info.path)
        text = "\n".join(section(report, "## Command-line fragments"))
        for token in ("console=ttyO2,115200n8", "mem=198M", "root=/dev/mmcblk0p2"):
            self.assertIn(": " + token, text)

    def test_pattern_offsets_are_hex(self):
        path = self.raw_image("p.img", [(0x40, b"mem=1"), (0x80, b"mem=2")])
        report = self.run_triage(path)
        lines = section(report, "## Pattern hits")
        line = next(l for l in lines if "`mem=`" in l)
        self.assertRegex(line, r"count 2\b")
        self.assertRegex(line, r"0x40\b")
        self.assertRegex(line, r"0x80\b")
        zero = next(l for l in lines if "`x-loader`" in l)
        self.assertRegex(zero, r"count 0\b")

    def test_raw_image_without_table_still_reports(self):
        path = self.raw_image("raw.img", [(0x100, b"U-Boot 2013.01")])
        report = self.run_triage(path)
        self.assertIn("No partition table found.", section(report, "## Partitions"))
        self.assertEqual(section(report, "## Secret-like files"), ["No partitions to scan."])
        line = next(l for l in section(report, "## Pattern hits") if "U-Boot" in l)
        self.assertRegex(line, r"0x100\b")

    def test_secret_like_file_named_but_not_read(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.dir)
        report = self.run_triage(info.path)
        self.assertIn("wpa_supplicant.conf", "\n".join(section(report, "## Secret-like files")))
        self.assertNotIn("hunter2", report)
        self.assertNotIn("fw.bin", "\n".join(section(report, "## Secret-like files")))

    def test_lists_capped_at_40_lines(self):
        pieces = [(i * 64, b"mem=%03d\0" % i) for i in range(100)]
        path = self.raw_image("many.img", pieces, size=16 * 1024)
        report = self.run_triage(path)
        lines = section(report, "## Command-line fragments")
        hits = [l for l in lines if l.startswith("- ")]
        self.assertEqual(len(hits), 40)
        omitted = [l for l in lines if "omitted" in l]
        self.assertEqual(len(omitted), 1)
        self.assertIn("60", omitted[0])

    def test_fragment_excludes_adjacent_secret_bytes(self):
        path = self.raw_image("adj.img", [(0x250, b"psk=hunter2 root=x\n")])
        report = self.run_triage(path)
        self.assertNotIn("hunter2", report)
        self.assertIn("root=x", "\n".join(section(report, "## Command-line fragments")))

    def test_fragment_excludes_adjacent_secret_bytes_in_partitioned_image(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.dir)
        with open(info.path, "r+b") as f:
            f.seek(0x1200000)  # inside the unformatted second primary partition
            f.write(b"psk=hunter2 root=x\n")
        report = self.run_triage(info.path)
        self.assertNotIn("hunter2", report)
        self.assertIn("root=x", "\n".join(section(report, "## Command-line fragments")))

    def test_long_token_truncated_to_100_bytes(self):
        path = self.raw_image("long.img", [(0x40, b"root=" + b"a" * 300 + b"\n")])
        report = self.run_triage(path)
        frag = [l for l in section(report, "## Command-line fragments") if l.startswith("- ")]
        self.assertEqual(len(frag), 1)
        self.assertEqual(frag[0].split(": ", 1)[1], "root=" + "a" * 95)

    def test_dump_not_modified(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.dir)
        before = (os.stat(info.path).st_mtime_ns, sha256_of(info.path))
        self.run_triage(info.path)
        self.assertEqual((os.stat(info.path).st_mtime_ns, sha256_of(info.path)), before)

    def test_hits_straddling_chunk_boundary_counted_once(self):
        path = self.raw_image("chunk.img", [(13, b"U-Boot"), (8, b"mem=")], size=64)
        original = triage._CHUNK
        triage._CHUNK = 16
        self.addCleanup(setattr, triage, "_CHUNK", original)
        report = self.run_triage(path)
        lines = section(report, "## Pattern hits")
        uboot = next(l for l in lines if "`U-Boot`" in l)
        mem = next(l for l in lines if "`mem=`" in l)
        self.assertRegex(uboot, r"count 1, offsets 0xd$")
        self.assertRegex(mem, r"count 1, offsets 0x8$")

    def test_non_ext4_partitions_are_noted(self):
        require_tool("debugfs")
        info = make_gpt_dump(self.dir)
        report = self.run_triage(info.path)
        lines = section(report, "## Secret-like files")
        self.assertEqual(sum("not ext4/unreadable" in l for l in lines), 2)
        self.assertIn("boot", "\n".join(section(report, "## Partitions")))

    def test_empty_partition_list_reports_like_no_table(self):
        path = self.raw_image("empty.img", [])
        original = triage.read_partitions
        triage.read_partitions = lambda _p: []
        self.addCleanup(setattr, triage, "read_partitions", original)
        report = self.run_triage(path)
        self.assertEqual(section(report, "## Partitions"), ["No partition table found."])
        self.assertIn("No partitions to scan.", section(report, "## Secret-like files"))


CLEAN = "No secret-like file names found"


class TriageSecretScanTests(unittest.TestCase):
    def setUp(self):
        require_tool("debugfs")
        require_tool("mke2fs")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.out = os.path.join(self.dir, "out")
        self.info = make_mbr_dump(self.dir)

    def report(self, path=None):
        triage.triage(path or self.info.path, self.out)
        with open(os.path.join(self.out, "triage.md"), encoding="utf-8") as f:
            return f.read()

    def secret_section(self, report):
        return "\n".join(section(report, "## Secret-like files"))

    def test_missing_debugfs_raises_dump_error_not_a_report(self):
        with tempfile.TemporaryDirectory() as empty, \
                mock.patch.dict(os.environ, {"PATH": empty}), \
                mock.patch.object(_debugfs, "_EXTRA_DIRS", ()):
            with self.assertRaises(DumpError) as cm:
                triage.triage(self.info.path, self.out)
            self.assertIn("install e2fsprogs", str(cm.exception))
            err = io.StringIO()
            with redirect_stderr(err):
                code = dump_tool.main(["triage", self.info.path, self.out])
            self.assertEqual(code, 1)
            self.assertIn("install e2fsprogs", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.out, "triage.md")))

    def test_dump_named_triage_md_in_outdir_is_refused_and_untouched(self):
        dump = os.path.join(self.dir, "triage.md")
        with open(self.info.path, "rb") as src, open(dump, "wb") as dst:
            dst.write(src.read())
        os.chmod(dump, 0o640)
        before = sha256_of(dump)
        mode = os.stat(dump).st_mode
        with self.assertRaises(DumpError) as cm:
            triage.triage(dump, self.dir)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("triage.md", str(cm.exception))
        self.assertEqual(sha256_of(dump), before)
        self.assertEqual(os.stat(dump).st_mode, mode)

    def test_report_replaces_an_existing_report_atomically(self):
        self.report()
        os.chmod(os.path.join(self.out, "triage.md"), 0o600)
        first = self.report()
        self.assertEqual(first, self.report())
        self.assertEqual(os.listdir(self.out), ["triage.md"])

    def test_failed_report_write_leaves_no_partial_file_and_no_temp_file(self):
        with mock.patch.object(triage.os, "replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                triage.triage(self.info.path, self.out)
        self.assertEqual(os.listdir(self.out), [])

    def test_failed_report_write_keeps_the_previous_report(self):
        self.report()
        path = os.path.join(self.out, "triage.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("PREVIOUS\n")
        with mock.patch.object(triage.os, "replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                triage.triage(self.info.path, self.out)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "PREVIOUS\n")
        self.assertEqual(os.listdir(self.out), ["triage.md"])

    def test_truncated_dump_marks_partitions_and_warns(self):
        os.truncate(self.info.path, 5 * 1024 * 1024)  # inside partition 1 (1 MiB .. 9 MiB)
        rows = section(self.report(), "## Partitions")
        marked = [l for l in rows if l.endswith(" [past end of dump]")]
        self.assertEqual([l.split("|")[1].strip() for l in marked], ["1", "2"])
        self.assertIn("WARNING: dump appears truncated: 2 partition(s) end past end of file.",
                      rows)

    def test_partition_ending_exactly_at_end_of_file_is_not_flagged(self):
        os.truncate(self.info.path, 9 * 1024 * 1024)  # partition 1 ends exactly here
        rows = "\n".join(section(self.report(), "## Partitions"))
        row1 = next(l for l in rows.splitlines() if l.startswith("| 1 |"))
        self.assertNotIn("past end", row1)
        self.assertIn("1 partition(s) end past end of file", rows)

    def test_complete_dump_has_no_truncation_marks(self):
        report = self.report()
        self.assertNotIn("past end of dump", report)
        self.assertNotIn("truncated", report)

    def test_dump_path_with_question_mark_is_refused(self):
        odd = os.path.join(self.dir, "we?ird.img")
        with open(self.info.path, "rb") as src, open(odd, "wb") as dst:
            dst.write(src.read())
        with self.assertRaises(DumpError) as cm:
            triage.triage(odd, self.out)
        self.assertIn("?", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.out, "triage.md")))

    def test_all_partitions_unreadable_is_not_reported_clean(self):
        info = make_gpt_dump(self.dir)
        text = self.secret_section(self.report(info.path))
        self.assertNotIn(CLEAN, text)
        self.assertIn("no ext4 partition was readable", text)

    def test_clean_wording_only_when_scan_was_complete(self):
        set_partition1_files(self.info, {"/etc/motd": b"hi"})
        text = self.secret_section(self.report())
        self.assertIn(CLEAN, text)
        self.assertNotIn("scan incomplete", text)

    def test_directory_with_quote_in_name_counts_as_incomplete(self):
        set_partition1_files(self.info, {'/q"d/x_secret': b"s", "/ok/f": b"1"})
        text = self.secret_section(self.report())
        self.assertIn("scan incomplete: 1 director", text)
        self.assertNotIn(CLEAN, text)

    def test_unreadable_subdirectory_counts_as_incomplete(self):
        set_partition1_files(self.info, {"/a/b/c_secret": b"s", "/z/f": b"1"})
        real = _debugfs.list_dir

        def flaky(dump, offset, path):
            return None if path == "/a" else real(dump, offset, path)

        with mock.patch.object(_debugfs, "list_dir", side_effect=flaky):
            text = self.secret_section(self.report())
        self.assertIn("scan incomplete: 1 director", text)
        self.assertNotIn(CLEAN, text)

    def test_depth_cap_counts_as_incomplete(self):
        set_partition1_files(self.info, {"/a/b/c_secret": b"s"})
        with mock.patch.object(triage, "_MAX_WALK_DEPTH", 0):
            text = self.secret_section(self.report())
        self.assertIn("scan incomplete", text)
        self.assertNotIn(CLEAN, text)

    def test_entry_budget_counts_as_incomplete(self):
        set_partition1_files(self.info, {"/a/b/c_secret": b"s", "/d/e": b"1"})
        with mock.patch.object(triage, "_MAX_WALK_ENTRIES", 1):
            text = self.secret_section(self.report())
        self.assertIn("scan incomplete", text)
        self.assertNotIn(CLEAN, text)

    def test_incomplete_line_survives_the_40_line_cap(self):
        files = {"/d/f%02d_secret" % i: b"s" for i in range(50)}
        files['/q"d/x'] = b"1"
        set_partition1_files(self.info, files)
        text = self.secret_section(self.report())
        self.assertIn("omitted", text)
        self.assertIn("scan incomplete: 1 director", text)

    def test_widened_word_list_names_are_listed_and_keymaps_are_not(self):
        listed = ["password.txt", "ssh_host_rsa_key", "id_ed25519", "server.p12", "id_rsa",
                  "id_ecdsa", "my.pfx", "store.keystore", "private.dat", "aws_credentials",
                  "PASSWORD.TXT", "Wifi_Credential"]
        ignored = ["keymap.map", "keyboard.conf", "monkey.txt", "keys.txt"]
        files = {"/d/" + n: b"s" for n in listed + ignored}
        set_partition1_files(self.info, files)
        text = self.secret_section(self.report())
        for name in listed:
            with self.subTest(listed=name):
                self.assertIn("/d/" + name, text)
        for name in ignored:
            with self.subTest(ignored=name):
                self.assertNotIn(name, text)

    def test_secret_word_matching_is_case_insensitive_substring(self):
        for name in ("a_key", "X.PEM", "my.KEY", "Shadow-", "tOkEn", "wpa_supplicant.conf",
                     "passwd", "SECRET", "Id_Rsa.pub", "cert.P12", "k.PFX", "a.KeyStore"):
            with self.subTest(name=name):
                self.assertTrue(triage._is_secret_like(name))
        for name in ("key", "keymap.map", "keyboard.conf", "monkey", "hotkeys", "turkey.txt"):
            with self.subTest(name=name):
                self.assertFalse(triage._is_secret_like(name))

    def test_names_with_line_break_characters_are_found(self):
        files = {"/a\x1cb_secret": b"x", "/c\x0bd_secret": b"x",
                 "/e\u2028f_secret": b"x", "/g\nh_secret": b"x"}
        set_partition1_files(self.info, files)
        text = self.secret_section(self.report())
        self.assertNotIn(CLEAN, text)
        # Names are shown with control and line-break characters escaped.
        for frag in ("a\\x1cb_secret", "c\\x0bd_secret", "e\\u2028f_secret", "g\\nh_secret"):
            self.assertIn(frag, text)
        self.assertEqual(sum("_secret" in l for l in text.splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
