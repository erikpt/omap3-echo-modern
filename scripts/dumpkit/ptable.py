"""Read MBR (with extended/logical partitions) and GPT partition tables from a dump."""
import os
import struct
import uuid
from dataclasses import dataclass

from .errors import NoPartitionTable

SECTOR = 512
_EXTENDED_TYPES = (0x05, 0x0F, 0x85)
_GPT_MAX_ENTRIES = 4096
_GPT_MAX_ENTRY_SIZE = 4096


@dataclass(frozen=True)
class Partition:
    index: int  # 1-based; MBR logical partitions are numbered from 5
    start: int  # bytes
    size: int  # bytes
    type_id: str  # MBR: two-digit lowercase hex; GPT: lowercase GUID
    name: str = ""  # GPT only


def _read_at(f, offset, length):
    f.seek(offset)
    return f.read(length)


def _mbr_entries(sector):
    """Return [(type, start_lba, sectors)] for the four table slots (None if empty)."""
    entries = []
    for slot in range(4):
        raw = sector[446 + 16 * slot:462 + 16 * slot]
        ptype, start_lba, sectors = struct.unpack_from("<xxxxBxxxII", raw)
        entries.append(None if ptype == 0 or sectors == 0 else (ptype, start_lba, sectors))
    return entries


def _read_gpt(f):
    header = _read_at(f, SECTOR, 92)
    if len(header) < 92 or header[:8] != b"EFI PART":
        return None
    entries_lba, count, entry_size = struct.unpack_from("<QII", header, 72)
    if not (0 < count <= _GPT_MAX_ENTRIES and 128 <= entry_size <= _GPT_MAX_ENTRY_SIZE):
        return None
    table = _read_at(f, entries_lba * SECTOR, count * entry_size)
    if len(table) < count * entry_size:
        return None
    parts = []
    for slot in range(count):
        raw = table[slot * entry_size:(slot + 1) * entry_size]
        type_guid = raw[:16]
        if type_guid == bytes(16):
            continue
        first, last = struct.unpack_from("<QQ", raw, 32)
        if last < first:
            continue
        name = raw[56:128].decode("utf-16-le", errors="replace").rstrip("\0")
        parts.append(Partition(
            index=slot + 1,
            start=first * SECTOR,
            size=(last - first + 1) * SECTOR,
            type_id=str(uuid.UUID(bytes_le=type_guid)),
            name=name,
        ))
    return parts


def _read_mbr(f, file_size):
    sector = _read_at(f, 0, SECTOR)
    if len(sector) < SECTOR or sector[510:512] != b"\x55\xaa":
        return None
    entries = _mbr_entries(sector)
    # A protective MBR without a valid GPT is not a usable table.
    if any(e and e[0] == 0xEE for e in entries):
        return None
    parts = []
    extended_bases = []
    for slot, e in enumerate(entries):
        if e is None:
            continue
        ptype, start_lba, sectors = e
        if ptype in _EXTENDED_TYPES:
            extended_bases.append(start_lba * SECTOR)
            continue
        parts.append(Partition(slot + 1, start_lba * SECTOR, sectors * SECTOR, "%02x" % ptype))

    next_index = 5
    for base in extended_bases:
        ebr_offset = base
        seen = set()
        while ebr_offset not in seen and ebr_offset + SECTOR <= file_size:
            seen.add(ebr_offset)
            ebr = _read_at(f, ebr_offset, SECTOR)
            if len(ebr) < SECTOR or ebr[510:512] != b"\x55\xaa":
                break
            link = None
            for e in _mbr_entries(ebr)[:2]:
                if e is None:
                    continue
                ptype, start_lba, sectors = e
                if ptype in _EXTENDED_TYPES:
                    link = start_lba
                else:
                    # Logical data start is relative to this EBR.
                    parts.append(Partition(
                        next_index, ebr_offset + start_lba * SECTOR, sectors * SECTOR,
                        "%02x" % ptype))
                    next_index += 1
            if link is None:
                break
            # The next EBR is relative to the start of the extended partition.
            ebr_offset = base + link * SECTOR
    return parts


def read_partitions(path):
    """Return the partitions of the dump at `path`; prefers GPT over a protective MBR."""
    with open(path, "rb") as f:
        file_size = os.fstat(f.fileno()).st_size
        parts = _read_gpt(f)
        if parts is None:
            parts = _read_mbr(f, file_size)
    if parts is None:
        raise NoPartitionTable("no valid MBR or GPT partition table in " + path)
    return parts
