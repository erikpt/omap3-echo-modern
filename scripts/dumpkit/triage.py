"""Triage report for an eMMC dump: file facts, partitions, boot-related byte patterns,
kernel command-line fragments and the names (never the contents) of secret-like files.

Command-line fragments are matched tokens only: a fragment starts exactly at a
console=/mem=/root= hit and ends at the first byte outside [A-Za-z0-9_./:,=+@%-]
(at most 100 bytes); no byte before the hit is ever reported. The scan is raw, not
file-aware, so a secret value placed directly after a searched key (for example
root=<secret>) would still be printed. That residual risk is accepted."""
import hashlib
import os

from . import _debugfs
from .errors import NoPartitionTable
from .ptable import read_partitions

PATTERNS = (
    b"console=", b"mem=", b"root=", b"rootfstype=",
    b"U-Boot", b"x-loader",
    b"\xd0\x0d\xfe\xed",  # flattened device tree magic
    b"\x27\x05\x19\x56",  # U-Boot legacy image magic
)
_CMDLINE_PATTERNS = (b"console=", b"mem=", b"root=")
_SECRET_WORDS = ("secret", "token", "passwd", "shadow", "wpa_supplicant", ".pem", ".key")

_CHUNK = 1024 * 1024
_MAX_LINES = 40
_MAX_OFFSETS = 5
_TOKEN_CHARS = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./:,=+@%-")
_MAX_FRAGMENT = 100
_MAX_STORED_CMDLINE_HITS = 20000
_MAX_WALK_ENTRIES = 200000
_MAX_WALK_DEPTH = 32


def _scan(path):
    """One streaming pass: size, SHA-256 and pattern hits (absolute offsets, ascending)."""
    overlap = max(len(p) for p in PATTERNS) - 1
    counts = {p: 0 for p in PATTERNS}
    first = {p: [] for p in PATTERNS}
    cmdline = []  # (offset, length) of console=/mem=/root= hits
    last = {p: -1 for p in PATTERNS}
    sha = hashlib.sha256()
    size = 0
    tail = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            sha.update(chunk)
            base = size - len(tail)
            size += len(chunk)
            buf = tail + chunk
            for pat in PATTERNS:
                idx = buf.find(pat)
                while idx != -1:
                    off = base + idx
                    # A hit lying wholly in the overlap was already seen in the last pass.
                    if off > last[pat]:
                        last[pat] = off
                        counts[pat] += 1
                        if len(first[pat]) < _MAX_OFFSETS:
                            first[pat].append(off)
                        if pat in _CMDLINE_PATTERNS and len(cmdline) < _MAX_STORED_CMDLINE_HITS:
                            cmdline.append(off)
                    idx = buf.find(pat, idx + 1)
            tail = buf[-overlap:] if overlap else b""
    return size, sha.hexdigest(), counts, first, sorted(cmdline)


def _printable(b):
    return 0x20 <= b <= 0x7E


def _fragments(path, hit_offsets):
    """Distinct command-line tokens as (offset, text), in file order.

    A token starts exactly at the hit and extends while bytes are in _TOKEN_CHARS,
    at most _MAX_FRAGMENT bytes. Nothing before the hit is ever included."""
    found = []
    seen = set()
    with open(path, "rb") as f:
        for off in hit_offsets:
            f.seek(off)
            data = f.read(_MAX_FRAGMENT)
            end = 0
            while end < len(data) and data[end] in _TOKEN_CHARS:
                end += 1
            text = data[:end].decode("ascii")
            if text not in seen:
                seen.add(text)
                found.append((off, text))
    return found


def _cap(lines):
    if len(lines) <= _MAX_LINES:
        return lines
    return lines[:_MAX_LINES] + ["... %d more omitted (list truncated at %d lines)"
                                 % (len(lines) - _MAX_LINES, _MAX_LINES)]


def _label(pat):
    if all(_printable(b) for b in pat):
        return pat.decode("ascii")
    return "hex " + pat.hex()


def _is_secret_like(name):
    low = name.lower()
    return any(word in low for word in _SECRET_WORDS)


def _display(name):
    """A file name made printable: control and line-break characters become escapes."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii")
                   for c in name)


def _secret_like_paths(dump, start):
    """(readable, paths, incomplete) for the ext4 filesystem at byte `start`.

    readable is False when the partition cannot be listed at all. incomplete counts the
    directories that were skipped or could not be read (unreadable, too deep, over the
    entry budget, or a name debugfs cannot be given), so the caller never calls a partial
    scan clean."""
    root = _debugfs.list_dir(dump, start, "/")
    if root is None:
        return False, [], 0
    hits = []
    incomplete = 0
    budget = _MAX_WALK_ENTRIES
    stack = [("", root, 0)]
    while stack:
        parent, entries, depth = stack.pop()
        for i, (name, is_dir) in enumerate(entries):
            if budget <= 0:
                # Unvisited entries here, and every directory still queued, are skipped.
                incomplete += 1 + len(stack)
                stack = []
                break
            budget -= 1
            path = parent + "/" + name
            if _is_secret_like(name):
                hits.append(path)
            if not is_dir:
                continue
            if depth >= _MAX_WALK_DEPTH or '"' in name or "\n" in name:
                incomplete += 1
                continue
            sub = _debugfs.list_dir(dump, start, path)
            if sub is None:
                incomplete += 1
            elif sub:
                stack.append((path, sub, depth + 1))
    return True, sorted(hits), incomplete


def _safe(text):
    """Make a string safe to write as UTF-8 even if it holds undecodable bytes."""
    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _partition_lines(parts):
    lines = ["| # | start | size (bytes) | type | name |", "|---|---|---|---|---|"]
    rows = ["| %d | 0x%x | %d | %s | %s |" % (p.index, p.start, p.size, p.type_id, p.name)
            for p in parts]
    return lines + _cap(rows)


def triage(dump, outdir):
    """Write `outdir/triage.md` for the dump at `dump` and return its path."""
    _debugfs.image_arg(dump, 0)  # refuse a dump path debugfs would misread, before any work
    try:
        parts = read_partitions(dump)
    except NoPartitionTable:
        parts = []
    if parts:
        _debugfs.find_debugfs()  # fail now, not with a report that looks like a clean scan
    size, digest, counts, first, cmdline_hits = _scan(dump)

    out = ["# Dump triage", "", "## File", "",
           "- Path: %s" % dump,
           "- Size: %d bytes" % size,
           "- SHA-256: %s" % digest,
           "- Size is a multiple of 512: %s" % ("yes" if size % 512 == 0 else "no"),
           "", "## Partitions", ""]
    out += _partition_lines(parts) if parts else ["No partition table found."]

    out += ["", "## Pattern hits", ""]
    for pat in PATTERNS:
        line = "- `%s`: count %d" % (_label(pat), counts[pat])
        if counts[pat]:
            offs = ", ".join("0x%x" % o for o in first[pat])
            more = counts[pat] - len(first[pat])
            line += ", offsets " + offs + (" (+%d more)" % more if more > 0 else "")
        out.append(line)

    out += ["", "## Command-line fragments", ""]
    fragments = _fragments(dump, cmdline_hits)
    if fragments:
        out += _cap(["- 0x%x: %s" % (off, text) for off, text in fragments])
    else:
        out.append("No command-line fragments found.")

    out += ["", "## Secret-like files", ""]
    if not parts:
        out.append("No partitions to scan.")
    else:
        lines = []
        readable_count = 0
        hit_count = 0
        incomplete = 0
        for p in parts:
            readable, paths, skipped = _secret_like_paths(dump, p.start)
            if not readable:
                lines.append("- partition %d: not ext4/unreadable" % p.index)
                continue
            readable_count += 1
            incomplete += skipped
            hit_count += len(paths)
            lines += ["- partition %d: %s" % (p.index, _display(path)) for path in paths]
        has_hits = hit_count > 0
        out += _cap(lines)
        # These verdict lines are added after the cap so a long list can never hide them.
        if incomplete:
            out.append("scan incomplete: %d director%s skipped or unreadable"
                       % (incomplete, "y" if incomplete == 1 else "ies"))
        if readable_count == 0:
            out.append("Scan not possible: no ext4 partition was readable.")
        elif incomplete:
            if not has_hits:
                out.append("The scan was incomplete, so having no hits does not mean the "
                           "partitions are clean.")
        elif not has_hits:
            out.append("No secret-like file names found in readable ext4 partitions.")

    os.makedirs(outdir, exist_ok=True)
    report = os.path.join(outdir, "triage.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write(_safe("\n".join(out)) + "\n")
    return report
