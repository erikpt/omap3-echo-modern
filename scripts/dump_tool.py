#!/usr/bin/env python3
"""Command-line entry point for the dumpkit tools. Never modifies the dump."""
import argparse
import os
import sys

# Python's -I (isolated) mode leaves the script's own directory off sys.path; add it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dumpkit.blobs import extract_blobs
from dumpkit.carve import carve
from dumpkit.errors import DumpError, NoPartitionTable
from dumpkit.ptable import read_partitions
from dumpkit.triage import triage


def _cmd_partitions(args):
    parts = read_partitions(args.dump)
    if not parts:
        raise NoPartitionTable("no partitions found")
    for p in parts:
        print("\t".join([str(p.index), str(p.start), str(p.size), str(p.type_id), p.name]))


def _cmd_carve(args):
    carve(args.dump, args.offset, args.length, args.out, force=args.force)


def _cmd_triage(args):
    print(triage(args.dump, args.outdir))


def _cmd_extract_blobs(args):
    for path in extract_blobs(args.dump, args.manifest, args.outdir):
        print(path)


def _int(text):
    try:
        return int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError("not an integer (decimal or 0x hex): " + text)


class _Parser(argparse.ArgumentParser):
    """Usage errors exit 1 (argparse's default 2 is this tool's 'range' error)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print("error: %s" % message, file=sys.stderr)
        sys.exit(1)


def _parser():
    ap = _Parser(prog="dump_tool.py", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("partitions", help="print partitions as TSV: index start size type_id name")
    p.add_argument("dump")
    p.set_defaults(func=_cmd_partitions)

    p = sub.add_parser("carve", help="copy a byte range of the dump into a new file")
    p.add_argument("dump")
    p.add_argument("offset", type=_int)
    p.add_argument("length", type=_int)
    p.add_argument("out")
    p.add_argument("--force", action="store_true", help="overwrite an existing output file")
    p.set_defaults(func=_cmd_carve)

    p = sub.add_parser("triage", help="write triage.md into outdir and print its path")
    p.add_argument("dump")
    p.add_argument("outdir")
    p.set_defaults(func=_cmd_triage)

    p = sub.add_parser("extract-blobs", help="extract and verify the files listed in a manifest")
    p.add_argument("dump")
    p.add_argument("manifest")
    p.add_argument("outdir")
    p.set_defaults(func=_cmd_extract_blobs)
    return ap


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        args.func(args)
    except DumpError as e:
        print("error: %s" % e, file=sys.stderr)
        return e.exit_code
    except (ValueError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
