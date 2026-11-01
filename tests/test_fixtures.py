import os
import subprocess
import tempfile
import unittest

from dumpkit import errors
from tests.fixtures import make_gpt_dump, make_mbr_dump, require_tool


class ErrorClassesTest(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(errors.DumpError.exit_code, 1)
        self.assertEqual(errors.RangeErr.exit_code, 2)
        self.assertEqual(errors.NoPartitionTable.exit_code, 3)
        self.assertEqual(errors.ChecksumMismatch.exit_code, 4)
        self.assertEqual(errors.OutputExists.exit_code, 5)
        for cls in (errors.RangeErr, errors.NoPartitionTable,
                    errors.ChecksumMismatch, errors.OutputExists):
            self.assertTrue(issubclass(cls, errors.DumpError))
        self.assertTrue(issubclass(errors.DumpError, Exception))


class FixturesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_mbr_fixture_size_and_signature(self):
        info = make_mbr_dump(self.dir)
        self.assertEqual(os.path.getsize(info.path) % 512, 0)
        with open(info.path, "rb") as f:
            sector0 = f.read(512)
        self.assertEqual(sector0[510:512], b"\x55\xaa")
        self.assertEqual(len(info.partitions), 3)

    def test_mbr_fixture_without_ext4(self):
        info = make_mbr_dump(self.dir, with_ext4=False)
        self.assertEqual(info.files, {})
        start, size = info.partitions[0]
        with open(info.path, "rb") as f:
            f.seek(start)
            self.assertEqual(f.read(size), bytes(size))

    def test_gpt_fixture_has_efi_part_signature(self):
        info = make_gpt_dump(self.dir)
        with open(info.path, "rb") as f:
            data = f.read(1024)
        self.assertEqual(data[512:520], b"EFI PART")
        self.assertEqual(len(info.partitions), 2)

    def test_ext4_partition_contains_files(self):
        require_tool("debugfs")
        info = make_mbr_dump(self.dir)
        start, _size = info.partitions[0]
        self.assertEqual(info.files["/boot/cmdline"],
                         b"console=ttyO2,115200n8 mem=198M root=/dev/mmcblk0p2")
        for path, content in info.files.items():
            out = subprocess.run(
                ["debugfs", "-R", "cat " + path, "%s?offset=%d" % (info.path, start)],
                capture_output=True, check=True).stdout
            self.assertEqual(out, content, path)


if __name__ == "__main__":
    unittest.main()
