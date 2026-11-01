"""Synthetic eMMC dump fixtures. All content is invented; nothing is vendor-derived."""
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid
import zlib
from dataclasses import dataclass, field

SECTOR = 512
MIB = 1024 * 1024

MBR_FILES = {
    "/lib/firmware/ath6k/fw.bin": b"FIRMWARE",
    "/etc/wpa_supplicant.conf": b"psk=hunter2",
    "/boot/cmdline": b"console=ttyO2,115200n8 mem=198M root=/dev/mmcblk0p2",
}


@dataclass
class FixtureInfo:
    path: str
    partitions: list = field(default_factory=list)  # (start byte, size bytes)
    files: dict = field(default_factory=dict)  # path inside ext4 -> contents


def require_tool(name):
    """Skip the calling test, naming the tool, if it is not installed.

    With DUMPKIT_REQUIRE_TOOLS=1 (set in CI) a missing tool fails the test instead, so a
    broken runner image cannot turn the suite green by skipping."""
    if shutil.which(name) is None:
        message = "required tool not found: " + name
        if os.environ.get("DUMPKIT_REQUIRE_TOOLS") == "1":
            raise AssertionError(message + " (DUMPKIT_REQUIRE_TOOLS=1)")
        raise unittest.SkipTest(message)


def set_partition1_files(info, files):
    """Replace the ext4 filesystem in partition 1 of an MBR fixture with one holding `files`."""
    start, size = info.partitions[0]
    img = info.path + ".p1.tmp"
    try:
        _build_ext4(img, size, files)
        with open(img, "rb") as src, open(info.path, "r+b") as dst:
            dst.seek(start)
            shutil.copyfileobj(src, dst)
    finally:
        if os.path.exists(img):
            os.unlink(img)
    info.files = dict(files)


def _build_ext4(img_path, size, files):
    with tempfile.TemporaryDirectory() as staging:
        for path, content in files.items():
            dest = os.path.join(staging, path.lstrip("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(content)
        subprocess.run(
            ["mke2fs", "-q", "-F", "-t", "ext4", "-d", staging, img_path, str(size // 1024) + "K"],
            check=True, capture_output=True,
        )


def _mbr_entry(ptype, start_lba, sectors):
    # Dummy CHS (0xFE 0xFF 0xFF) marks "use LBA".
    return struct.pack("<B3sB3sII", 0, b"\xfe\xff\xff", ptype, b"\xfe\xff\xff", start_lba, sectors)


def _mbr_sector(entries):
    table = b"".join(entries).ljust(64, b"\0")
    return bytes(446) + table + b"\x55\xaa"


def make_mbr_dump(dir, with_ext4=True):
    """Two primary partitions plus one logical partition inside an extended one.

    `partitions` lists the three data partitions in table order (primary 1,
    primary 2, logical); the extended container is not listed.
    """
    path = os.path.join(dir, "mbr.img")
    p1 = (1 * MIB, 8 * MIB)
    p2 = (9 * MIB, 4 * MIB)
    ext = (13 * MIB, 6 * MIB)  # EBR in its first sector, logical data after 1 MiB
    logical = (14 * MIB, 4 * MIB)
    total = 20 * MIB

    files = {}
    if with_ext4:
        require_tool("mke2fs")
        files = dict(MBR_FILES)
    with open(path, "wb") as f:
        f.truncate(total)
        f.seek(0)
        f.write(_mbr_sector([
            _mbr_entry(0x83, p1[0] // SECTOR, p1[1] // SECTOR),
            _mbr_entry(0x83, p2[0] // SECTOR, p2[1] // SECTOR),
            _mbr_entry(0x05, ext[0] // SECTOR, ext[1] // SECTOR),
        ]))
        # EBR: first entry offsets are relative to the EBR; no next link.
        f.seek(ext[0])
        f.write(_mbr_sector([_mbr_entry(0x83, (logical[0] - ext[0]) // SECTOR, logical[1] // SECTOR)]))
    if with_ext4:
        img = path + ".ext4.tmp"
        try:
            _build_ext4(img, p1[1], files)
            with open(img, "rb") as src, open(path, "r+b") as dst:
                dst.seek(p1[0])
                shutil.copyfileobj(src, dst)
        finally:
            if os.path.exists(img):
                os.unlink(img)
    return FixtureInfo(path, [p1, p2, logical], files)


_GPT_ENTRIES = 128
_GPT_ENTRY_SIZE = 128
_GPT_ENTRY_SECTORS = _GPT_ENTRIES * _GPT_ENTRY_SIZE // SECTOR  # 32
_LINUX_FS = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")
_DISK_GUID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _gpt_header(total_sectors, current, backup, entries_lba, entries_crc):
    first_usable = 2 + _GPT_ENTRY_SECTORS
    last_usable = total_sectors - 2 - _GPT_ENTRY_SECTORS
    fields = lambda crc: struct.pack(
        "<8sIIIIQQQQ16sQIII", b"EFI PART", 0x00010000, 92, crc, 0, current, backup,
        first_usable, last_usable, _DISK_GUID.bytes_le, entries_lba,
        _GPT_ENTRIES, _GPT_ENTRY_SIZE, entries_crc)
    hdr = fields(0)
    return fields(zlib.crc32(hdr)).ljust(SECTOR, b"\0")


def make_gpt_dump(dir):
    """Protective MBR, primary and backup GPT, partitions `boot` and `rootfs` (zero-filled)."""
    path = os.path.join(dir, "gpt.img")
    parts = [("boot", 1 * MIB, 4 * MIB), ("rootfs", 5 * MIB, 8 * MIB)]
    total = 16 * MIB
    total_sectors = total // SECTOR

    entries = bytearray(_GPT_ENTRIES * _GPT_ENTRY_SIZE)
    for i, (name, start, size) in enumerate(parts):
        entry = struct.pack(
            "<16s16sQQQ72s", _LINUX_FS.bytes_le, uuid.UUID(int=0xA0 + i).bytes_le,
            start // SECTOR, (start + size) // SECTOR - 1, 0, name.encode("utf-16-le"))
        entries[i * _GPT_ENTRY_SIZE:(i + 1) * _GPT_ENTRY_SIZE] = entry
    entries = bytes(entries)
    crc = zlib.crc32(entries)

    protective = _mbr_sector([_mbr_entry(0xEE, 1, min(total_sectors - 1, 0xFFFFFFFF))])
    backup_entries_lba = total_sectors - 1 - _GPT_ENTRY_SECTORS
    with open(path, "wb") as f:
        f.truncate(total)
        f.write(protective)
        f.write(_gpt_header(total_sectors, 1, total_sectors - 1, 2, crc))
        f.write(entries)
        f.seek(backup_entries_lba * SECTOR)
        f.write(entries)
        f.write(_gpt_header(total_sectors, total_sectors - 1, 1, backup_entries_lba, crc))
    return FixtureInfo(path, [(s, z) for _n, s, z in parts], {})
