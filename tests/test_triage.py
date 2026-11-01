import hashlib
import os
import re
import tempfile
import unittest

from dumpkit import triage
from tests.fixtures import make_gpt_dump, make_mbr_dump, require_tool

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

    def test_finds_cmdline_and_expands_it(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.dir)
        report = self.run_triage(info.path)
        text = "\n".join(section(report, "## Command-line fragments"))
        self.assertIn("console=ttyO2,115200n8 mem=198M root=/dev/mmcblk0p2", text)

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


if __name__ == "__main__":
    unittest.main()
