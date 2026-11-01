"""Shared, read-only wrapper around e2fsprogs `debugfs` for triage and blobs.

debugfs is always run without -w, with an argument list (never a shell). Its exit status
is not trusted on its own: debugfs 1.47 exits 0 after some failures, so callers also look
at stderr, and the directory lister insists on seeing the `.` entry of a real directory."""
import os
import re
import shutil
import subprocess

from .errors import DumpError

# Where debugfs commonly lives when it is not on PATH (non-root Debian, Homebrew keg-only).
_EXTRA_DIRS = ("/usr/sbin", "/sbin", "/opt/homebrew/opt/e2fsprogs/sbin",
               "/usr/local/opt/e2fsprogs/sbin")
_MISSING_HINT = ("debugfs not found (install e2fsprogs; on macOS add "
                 "$(brew --prefix e2fsprogs)/sbin to PATH)")
# One `ls -p` record: /inode/mode/uid/gid/name/size/ then a newline. A name cannot hold '/'.
_LS_RECORD = re.compile(r"/([0-9]+)/([0-9]+)/([0-9]+)/([0-9]+)/([^/]*)/([0-9]*)/\n")


class DebugfsRunError(DumpError):
    """debugfs could not be started or did not finish in time."""


def find_debugfs():
    """Path of the debugfs executable; DumpError (exit 1) with an install hint if absent."""
    found = shutil.which("debugfs")
    if found:
        return found
    for directory in _EXTRA_DIRS:
        candidate = os.path.join(directory, "debugfs")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise DumpError(_MISSING_HINT)


def image_arg(dump, offset):
    """The `<abs path>?offset=N` image argument; DumpError if '?' would confuse debugfs."""
    path = os.path.abspath(dump)  # also keeps a leading '-' from reading as an option
    if "?" in path:
        raise DumpError("dump path contains '?', which debugfs reads as offset syntax: " + path)
    return "%s?offset=%d" % (path, offset)


def run(args_after_R, dump, offset, *, cwd=None, timeout=60):
    """Run `debugfs -R <args_after_R> <dump>?offset=N`; return (returncode, stdout, stderr).

    stdout and stderr are str (stdout keeps undecodable bytes as surrogate escapes).
    Raises DumpError if debugfs is missing, DebugfsRunError if it cannot run or times out."""
    exe = find_debugfs()
    image = image_arg(dump, offset)
    try:
        proc = subprocess.run([exe, "-R", args_after_R, image], cwd=cwd,
                              capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DebugfsRunError("debugfs could not run: %s" % e)
    return (proc.returncode,
            proc.stdout.decode("utf-8", errors="surrogateescape"),
            proc.stderr.decode("utf-8", errors="replace"))


def error_lines(stderr):
    """stderr lines that are not blank and not the `debugfs <version>` banner."""
    return [l for l in stderr.split("\n") if l.strip() and not l.startswith("debugfs ")]


def parse_ls_p(text):
    """Parse `ls -p` output into [(name, is_dir)], `.` and `..` included.

    Returns None if any of the text is not a well-formed record, so a partly understood
    listing is never mistaken for a complete one. Records are matched as a whole (not by
    line) because a name may hold line-break characters such as \\x1c, \\x0b or U+2028
    (or even a newline); only a trailing run of newlines may follow the last record."""
    entries = []
    pos = 0
    while True:
        m = _LS_RECORD.match(text, pos)
        if m is None:
            break
        try:
            is_dir = (int(m.group(2), 8) & 0o170000) == 0o040000
        except ValueError:
            return None
        entries.append((m.group(5), is_dir))
        pos = m.end()
    if text[pos:].strip("\n"):
        return None
    return entries


def list_dir(dump, offset, path):
    """Entries of directory `path` as [(name, is_dir)] without `.`/`..`, or None.

    None means unreadable: debugfs failed, printed an error, could not be understood, or
    the listing lacks the `.` entry every real directory has (never trust exit status
    alone). Only the `ls -p` command is issued; a `"` in `path` cannot be quoted for
    debugfs and is refused as unreadable."""
    if '"' in path or "\n" in path:
        return None
    try:
        rc, out, err = run('ls -p "%s"' % path, dump, offset, timeout=60)
    except DebugfsRunError:
        return None
    if rc != 0 or error_lines(err):
        return None
    entries = parse_ls_p(out)
    if entries is None or ("." not in [n for n, _ in entries]):
        return None
    return [(n, d) for n, d in entries if n not in (".", "..")]
