"""Stream a byte range out of a dump into a new file."""
import os

from .errors import DumpError, OutputExists, RangeErr

CHUNK = 1024 * 1024


def carve(src, offset, length, dst, force=False):
    """Copy `length` bytes at `offset` of `src` into `dst`.

    `src` is opened read-only. Output goes to `dst + ".part"` and is renamed
    into place only after the full range was copied.
    """
    if os.path.realpath(src) == os.path.realpath(dst):
        raise DumpError("refusing to write output over the source dump: " + dst)
    if offset < 0 or length < 0:
        raise RangeErr("offset and length must not be negative")
    if os.path.exists(dst) and not force:
        raise OutputExists("output exists (use force to overwrite): " + dst)
    part = dst + ".part"
    with open(src, "rb") as fin:
        size = os.fstat(fin.fileno()).st_size
        if offset + length > size:
            raise RangeErr("range 0x%x+0x%x exceeds source size 0x%x" % (offset, length, size))
        try:
            with open(part, "wb") as fout:
                fin.seek(offset)
                remaining = length
                while remaining:
                    chunk = fin.read(min(CHUNK, remaining))
                    if not chunk:
                        raise RangeErr("source ended early while carving")
                    fout.write(chunk)
                    remaining -= len(chunk)
            os.replace(part, dst)
        except BaseException:
            if os.path.exists(part):
                os.unlink(part)
            raise
