import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import dump_tool
from tests.fixtures import make_mbr_dump, require_tool

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
TOOL = os.path.join(SCRIPTS, "dump_tool.py")
TRIAGE_SH = os.path.join(SCRIPTS, "triage-dump.sh")
EXTRACT_SH = os.path.join(SCRIPTS, "extract-blobs.sh")
FW = "/lib/firmware/ath6k/fw.bin"
FW_SHA = hashlib.sha256(b"FIRMWARE").hexdigest()


def run(args, cwd=None):
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd)


def tool(*args, cwd=None):
    return run([sys.executable, TOOL, *args], cwd=cwd)


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def test_partitions_tsv_has_five_columns(self):
        info = make_mbr_dump(self.tmp, with_ext4=False)
        r = tool("partitions", info.path)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [line.split("\t") for line in r.stdout.splitlines()]
        self.assertEqual(len(rows), 3)
        for row, (start, size) in zip(rows, info.partitions):
            self.assertEqual(len(row), 5)
            self.assertEqual(row[1], str(start))
            self.assertEqual(row[2], str(size))

    def test_partitions_exit_code_3_on_blank_file(self):
        blank = os.path.join(self.tmp, "blank.img")
        with open(blank, "wb") as f:
            f.write(bytes(4096))
        r = tool("partitions", blank)
        self.assertEqual(r.returncode, 3)
        self.assertTrue(r.stderr.startswith("error: "), r.stderr)

    def test_partitions_empty_list_is_exit_3(self):
        err = io.StringIO()
        with mock.patch.object(dump_tool, "read_partitions", return_value=[]), \
                redirect_stderr(err):
            code = dump_tool.main(["partitions", "x.img"])
        self.assertEqual(code, 3)
        self.assertEqual(err.getvalue(), "error: no partitions found\n")

    def test_missing_dump_is_exit_1(self):
        r = tool("partitions", os.path.join(self.tmp, "nope.img"))
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("error: "), r.stderr)

    def test_carve_writes_range_and_accepts_hex(self):
        src = os.path.join(self.tmp, "src.bin")
        with open(src, "wb") as f:
            f.write(bytes(range(256)))
        out = os.path.join(self.tmp, "out.bin")
        r = tool("carve", src, "0x10", "16", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), bytes(range(16, 32)))

    def test_carve_exit_code_2_on_truncated_dump(self):
        src = os.path.join(self.tmp, "short.bin")
        with open(src, "wb") as f:
            f.write(bytes(100))
        r = tool("carve", src, "50", "100", os.path.join(self.tmp, "out.bin"))
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.startswith("error: "), r.stderr)

    def test_carve_exit_code_5_when_output_exists(self):
        src = os.path.join(self.tmp, "src.bin")
        with open(src, "wb") as f:
            f.write(bytes(100))
        out = os.path.join(self.tmp, "out.bin")
        with open(out, "wb") as f:
            f.write(b"keep")
        r = tool("carve", src, "0", "10", out)
        self.assertEqual(r.returncode, 5)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"keep")
        self.assertEqual(tool("carve", src, "0", "10", out, "--force").returncode, 0)

    def test_triage_wrapper_writes_report(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.tmp)
        outdir = os.path.join(self.tmp, "report")
        # Run from an unrelated directory: the wrapper must not depend on cwd.
        r = run(["bash", TRIAGE_SH, info.path, outdir], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = r.stdout.strip()
        self.assertEqual(report, os.path.join(outdir, "triage.md"))
        self.assertTrue(os.path.isfile(report))

    def _manifest(self, sha):
        path = os.path.join(self.tmp, "blobs.manifest")
        with open(path, "w", encoding="utf-8") as f:
            f.write("1\t%s\t%s\tfw.bin\n" % (FW, sha))
        return path

    def test_extract_wrapper_exit_code_4_on_bad_hash(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.tmp)
        outdir = os.path.join(self.tmp, "blobs")
        r = run(["bash", EXTRACT_SH, info.path, self._manifest("0" * 64), outdir], cwd=self.tmp)
        self.assertEqual(r.returncode, 4)
        self.assertTrue(r.stderr.startswith("error: "), r.stderr)
        self.assertFalse(os.path.exists(outdir))

    def test_extract_wrapper_extracts_matching_blob(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.tmp)
        outdir = os.path.join(self.tmp, "blobs")
        r = run(["bash", EXTRACT_SH, info.path, self._manifest(FW_SHA), outdir], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(outdir, "fw.bin"), "rb") as f:
            self.assertEqual(f.read(), b"FIRMWARE")


if __name__ == "__main__":
    unittest.main()
