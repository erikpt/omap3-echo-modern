import os
import tempfile
import unittest

from dumpkit import carve
from dumpkit.errors import DumpError, OutputExists, RangeErr
from tests.fixtures import make_mbr_dump


def _read(path):
    with open(path, "rb") as f:
        return f.read()


class CarveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.info = make_mbr_dump(self.dir, with_ext4=False)
        self.dst = os.path.join(self.dir, "out.bin")

    def test_carve_matches_source_slice(self):
        start, size = self.info.partitions[1]
        carve.carve(self.info.path, start, size, self.dst)
        self.assertEqual(_read(self.dst), _read(self.info.path)[start:start + size])
        self.assertFalse(os.path.exists(self.dst + ".part"))

    def test_truncated_dump_raises_range_err_and_writes_nothing(self):
        start, size = self.info.partitions[-1]
        with open(self.info.path, "r+b") as f:
            f.truncate(start + size - 1024)
        with self.assertRaises(RangeErr):
            carve.carve(self.info.path, start, size, self.dst)
        self.assertFalse(os.path.exists(self.dst))
        self.assertFalse(os.path.exists(self.dst + ".part"))

    def test_existing_output_refused(self):
        with open(self.dst, "wb") as f:
            f.write(b"original")
        with self.assertRaises(OutputExists):
            carve.carve(self.info.path, 0, 512, self.dst)
        self.assertEqual(_read(self.dst), b"original")
        self.assertFalse(os.path.exists(self.dst + ".part"))

    def test_force_overwrites(self):
        with open(self.dst, "wb") as f:
            f.write(b"original")
        carve.carve(self.info.path, 0, 512, self.dst, force=True)
        self.assertEqual(_read(self.dst), _read(self.info.path)[:512])

    def test_dst_equal_src_refused(self):
        before = _read(self.info.path)
        for force in (False, True):
            with self.assertRaises(DumpError):
                carve.carve(self.info.path, 0, 512, self.info.path, force=force)
        self.assertEqual(_read(self.info.path), before)

    def test_dst_symlink_to_src_refused(self):
        link = os.path.join(self.dir, "link.bin")
        os.symlink(self.info.path, link)
        before = _read(self.info.path)
        with self.assertRaises(DumpError):
            carve.carve(self.info.path, 0, 512, link, force=True)
        self.assertEqual(_read(self.info.path), before)

    def test_part_path_equal_src_refused(self):
        src = os.path.join(self.dir, "x.part")
        with open(src, "wb") as f:
            f.write(b"dump-bytes" * 100)
        before = _read(src)
        dst = os.path.join(self.dir, "x")
        for force in (False, True):
            with self.assertRaises(DumpError):
                carve.carve(src, 0, 512, dst, force=force)
        self.assertEqual(_read(src), before)
        self.assertEqual(os.path.getsize(src), len(before))
        self.assertFalse(os.path.exists(dst))


if __name__ == "__main__":
    unittest.main()
