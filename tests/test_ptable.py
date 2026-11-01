import os
import tempfile
import unittest

from dumpkit import ptable
from dumpkit.errors import NoPartitionTable
from tests.fixtures import make_gpt_dump, make_mbr_dump


class PtableTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def test_mbr_primary_and_logical(self):
        info = make_mbr_dump(self.dir, with_ext4=False)
        parts = ptable.read_partitions(info.path)
        self.assertEqual([p.index for p in parts], [1, 2, 5])
        self.assertEqual([(p.start, p.size) for p in parts], info.partitions)
        self.assertEqual([p.type_id for p in parts], ["83", "83", "83"])
        self.assertEqual([p.name for p in parts], ["", "", ""])

    def test_gpt_names(self):
        info = make_gpt_dump(self.dir)
        parts = ptable.read_partitions(info.path)
        self.assertEqual([p.name for p in parts], ["boot", "rootfs"])
        self.assertEqual([p.index for p in parts], [1, 2])
        self.assertEqual([(p.start, p.size) for p in parts], info.partitions)
        self.assertEqual(parts[0].type_id, "0fc63daf-8483-4772-8e79-3d69d8477de4")

    def test_blank_file_raises_no_partition_table(self):
        path = os.path.join(self.dir, "blank.img")
        with open(path, "wb") as f:
            f.write(bytes(4096))
        with self.assertRaises(NoPartitionTable):
            ptable.read_partitions(path)


if __name__ == "__main__":
    unittest.main()
