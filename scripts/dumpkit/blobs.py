"""Manifest-driven extraction of files from ext4 partitions of an eMMC dump.

Manifest format: one entry per line, four tab-separated fields: partition index (from 1),
path inside the filesystem, lowercase hex SHA-256, destination file name. Blank lines and
lines starting with `#` are ignored. The dump is only ever read (debugfs runs without -w).
Nothing reaches the output directory unless every checksum passes."""
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass

from . import _debugfs
from .errors import ChecksumMismatch, DumpError, RangeErr
from .ptable import read_partitions

_SHA256 = re.compile(r"[0-9a-f]{64}")
_DEBUGFS_TIMEOUT = 600


@dataclass(frozen=True)
class BlobEntry:
    partition: int
    fs_path: str
    sha256: str
    dest: str


def _problem(lineno, why):
    return ValueError("manifest line %d: %s" % (lineno, why))


def parse_manifest(text):
    """Parse manifest text into BlobEntry objects; a malformed line raises ValueError."""
    entries = []
    seen = {}
    for lineno, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise _problem(lineno, "expected 4 tab-separated fields, found %d" % len(fields))
        part, fs_path, sha, dest = fields
        if not re.fullmatch(r"[0-9]+", part) or int(part) < 1:
            raise _problem(lineno, "partition index must be an integer from 1")
        if not fs_path:
            raise _problem(lineno, "empty filesystem path")
        if '"' in fs_path:
            raise _problem(lineno, "filesystem path must not contain a double quote")
        if not _SHA256.fullmatch(sha):
            raise _problem(lineno, "sha256 must be 64 lowercase hex digits")
        if not dest or "/" in dest or "\\" in dest or dest in (".", ".."):
            raise _problem(lineno, "destination must be a bare file name")
        if any(ord(c) < 32 for c in fs_path + dest):
            raise _problem(lineno, "control characters are not allowed")
        if dest in seen:
            raise _problem(lineno, "destination %r already used on line %d" % (dest, seen[dest]))
        seen[dest] = lineno
        entries.append(BlobEntry(int(part), fs_path, sha, dest))
    return entries


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _debugfs_dump(dump, start, entry, workdir, tmpname):
    """Copy entry.fs_path out of the filesystem into workdir/tmpname; return that path."""
    where = "%s in partition %d" % (entry.fs_path, entry.partition)
    try:
        rc, _out, err = _debugfs.run('dump "%s" %s' % (entry.fs_path, tmpname), dump, start,
                                     cwd=workdir, timeout=_DEBUGFS_TIMEOUT)
    except _debugfs.DebugfsRunError as e:
        raise DumpError("%s for %s" % (e, where))
    out = os.path.join(workdir, tmpname)
    errors = _debugfs.error_lines(err)
    if (rc != 0 or errors or os.path.islink(out) or not os.path.isfile(out)):
        detail = errors[0].strip() if errors else "exit status %d" % rc
        raise DumpError("cannot extract %s: %s" % (where, detail))
    return out


def _refuse_protected(dests, outdir, dump, manifest_path):
    """DumpError if any destination is, or links to, the dump or the manifest.

    Only an existing file can be the same file; a destination that does not exist yet
    cannot clobber anything."""
    for dest in dests:
        final = os.path.join(outdir, dest)
        if not os.path.exists(final):
            continue
        for what, protected in (("dump", dump), ("manifest", manifest_path)):
            try:
                same = os.path.samefile(final, protected)
            except OSError:
                same = False
            if same:
                raise DumpError("%s: destination is the %s itself; refusing to replace it"
                                % (dest, what))


def _install(staged, outdir, dump, manifest_path):
    """Move staged [(path, dest)] into outdir; undo everything if any move fails."""
    _refuse_protected([dest for _, dest in staged], outdir, dump, manifest_path)
    for _, dest in staged:
        final = os.path.join(outdir, dest)
        if os.path.isdir(final) and not os.path.islink(final):
            raise DumpError("%s: a directory already exists at the destination" % dest)
    undo = []  # (final path, backup path or None)
    try:
        for n, (path, dest) in enumerate(staged):
            final = os.path.join(outdir, dest)
            backup = None
            if os.path.lexists(final):
                backup = os.path.join(os.path.dirname(path), "old-%d" % n)
                os.replace(final, backup)
            undo.append((final, backup))
            os.replace(path, final)
    except BaseException:
        for final, backup in reversed(undo):
            try:
                if os.path.lexists(final):
                    os.unlink(final)
                if backup is not None:
                    os.replace(backup, final)
            except OSError:
                pass
        raise


def _missing_dirs(path):
    """Directories that makedirs(path) would create, outermost first."""
    missing = []
    while path and not os.path.exists(path):
        missing.append(path)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return missing[::-1]


def extract_blobs(dump, manifest_path, outdir):
    """Extract every manifest entry from `dump` into `outdir`; return the output paths.

    Each file is staged under outdir, checked against its SHA-256 and only moved into
    place once all entries pass. On any failure outdir holds what it held before (or does
    not exist, if it did not exist before)."""
    dump = os.path.abspath(dump)  # also keeps a leading '-' from reading as an option
    _debugfs.image_arg(dump, 0)  # refuses a '?' in the path
    try:
        with open(manifest_path, encoding="utf-8") as f:
            entries = parse_manifest(f.read())
    except OSError as e:
        raise DumpError("cannot read manifest %s: %s" % (manifest_path, e.strerror or e))
    except UnicodeDecodeError:
        raise DumpError("manifest %s is not valid UTF-8" % manifest_path)

    try:
        parts = {p.index: p for p in read_partitions(dump)}
    except OSError as e:
        raise DumpError("cannot read dump %s: %s" % (dump, e.strerror or e))
    for entry in entries:
        if entry.partition not in parts:
            raise RangeErr("partition %d (for %s) not found in %s"
                           % (entry.partition, entry.dest, dump))

    if entries:
        _debugfs.find_debugfs()  # missing debugfs: fail before touching outdir
    outdir = os.path.abspath(outdir)
    _refuse_protected([e.dest for e in entries], outdir, dump, manifest_path)
    created = _missing_dirs(outdir)
    os.makedirs(outdir, exist_ok=True)
    stage = None
    try:
        stage = tempfile.mkdtemp(dir=outdir, prefix=".stage-")
        staged = []
        for n, entry in enumerate(entries):
            path = _debugfs_dump(dump, parts[entry.partition].start, entry, stage, "blob-%d" % n)
            actual = _sha256_file(path)
            if actual != entry.sha256:
                raise ChecksumMismatch("%s: expected sha256 %s, got %s"
                                       % (entry.dest, entry.sha256, actual))
            staged.append((path, entry.dest))
        _install(staged, outdir, dump, manifest_path)
    except BaseException:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
            stage = None
        for d in reversed(created):
            try:
                os.rmdir(d)
            except OSError:
                pass
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    return [os.path.join(outdir, e.dest) for e in entries]
