"""Error classes; each carries the process exit code the CLIs should use."""


class DumpError(Exception):
    exit_code = 1


class RangeErr(DumpError):
    exit_code = 2


class NoPartitionTable(DumpError):
    exit_code = 3


class ChecksumMismatch(DumpError):
    exit_code = 4


class OutputExists(DumpError):
    exit_code = 5
