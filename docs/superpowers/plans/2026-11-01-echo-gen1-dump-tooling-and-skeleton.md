# Echo Gen 1: Dump Tooling and Build Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tools that read an eMMC dump `.bin` (partition table, carving, triage report, blob extraction) and the Buildroot skeleton that compiles the board DTB, so the dump analysis and the first image build can start the day the dump arrives.

**Architecture:** A stdlib-only Python package `scripts/dumpkit/` (no pip dependencies) parses MBR and GPT itself, so it runs on Linux or macOS without `sfdisk`. Extracting files from an ext4 partition shells out to `debugfs` (e2fsprogs), which needs no root and no mount. Thin `.sh` wrappers keep the names the runbook uses. The Buildroot skeleton is a `BR2_EXTERNAL` tree plus a pinned Buildroot submodule.

**Tech Stack:** Python 3 (stdlib, `unittest`), e2fsprogs (`debugfs`, `mke2fs` for fixtures), bash wrappers, Buildroot (LTS tag), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-01-echo-gen1-custom-image-design.md` (sections 4.1 to 4.5, 7). Runbook: `docs/emmc-dump-analysis.md`.

## Global Constraints

- No Amazon-derived file in git. `dump/`, `blobs/`, `*.bin`, `*.img`, `output/`, `dl/` stay git-ignored (`.gitignore` already does this).
- Tools never modify the dump: it is opened read-only, and outputs go elsewhere.
- Tools never print the contents of secret-like files. Secret-like means a filename containing `secret`, `token`, `passwd`, `password`, `shadow`, `wpa_supplicant`, `private`, `credential`, `_key`, `id_rsa`, `id_ecdsa`, `id_ed25519`, `.pem`, `.key`, `.p12`, `.pfx`, `.keystore` (case-insensitive substring; no bare `key`, so `keymap.map` is not flagged).
- Triage output is capped: no list longer than 40 lines.
- Exit codes: 0 ok, 2 range error, 3 no partition table, 4 checksum mismatch, 5 output exists, 1 any other error.
- Buildroot is a git submodule at an LTS tag, never forked or patched. Kernel is a mainline LTS taken from that Buildroot release.
- Python code is stdlib only and does not import anything from the data it reads. Do not run the tools from inside a directory holding untrusted dump contents.
- Branches: `main` holds docs only (specs, plans, runbook, hardware notes). `firmware-develop` is where all code in this plan is built: work on a branch off `firmware-develop` and merge back; do not commit tooling or the Buildroot skeleton to `main`. `firmware-release` is for releases and does not exist yet; do not create it. `firmware-develop` starts as an empty commit, so the first step of execution is merging `main` into it to bring the docs along.
- Nothing here can verify boot, Wi-Fi, audio or mics. Only tests and the DTB build are verified.

## Review Focus

- Truncated dump (shorter than the partition table claims): carve and extract fail with exit 2, leave no partial output file. Pinned by Task 2.
- Output path already exists: refuse with exit 5 unless `--force`. Never write to the dump path. Pinned by Task 2.
- Manifest with blank lines, `#` comments, and paths containing spaces. Pinned by Task 4.
- Checksum mismatch: no file left in `outdir`, exit 4, message names the file. Pinned by Task 4.
- Raw image with no partition table: triage still writes a report saying so, and does not crash. Pinned by Task 3.
- A secret-like file present in a partition: triage lists its name but never its contents. Pinned by Task 3.

## File Structure

```
scripts/
├── dump_tool.py            # CLI: partitions | carve | triage | extract-blobs
├── dumpkit/
│   ├── __init__.py
│   ├── errors.py           # exception classes mapped to exit codes
│   ├── ptable.py           # MBR (+extended) and GPT parsing
│   ├── carve.py            # streaming range copy
│   ├── triage.py           # report generator
│   └── blobs.py            # manifest parse + debugfs extraction + sha256 check
├── triage-dump.sh          # wrapper: dump_tool.py triage
└── extract-blobs.sh        # wrapper: dump_tool.py extract-blobs
tests/
├── fixtures.py             # builds synthetic dumps (no Amazon data)
└── test_*.py
external/ ...               # Task 6
.github/workflows/ci.yml    # Task 5
```

---

### Task 1: Errors, fixtures and test runner

**Files:**
- Create: `scripts/dumpkit/__init__.py`, `scripts/dumpkit/errors.py`, `tests/fixtures.py`, `tests/test_fixtures.py`

**Interfaces:**
- Produces: `errors.DumpError(Exception)` with `exit_code: int`; subclasses `RangeErr` (2), `NoPartitionTable` (3), `ChecksumMismatch` (4), `OutputExists` (5).
- Produces: `fixtures.make_mbr_dump(dir: str, with_ext4: bool = True) -> FixtureInfo` and `fixtures.make_gpt_dump(dir: str) -> FixtureInfo`. `FixtureInfo` is a dataclass: `path: str`, `partitions: list[tuple[int, int]]` (start byte, size bytes), `files: dict[str, bytes]` (path inside the ext4 partition to contents).
- The MBR fixture holds two primary partitions plus one logical partition in an extended partition; the first partition is ext4 containing `/lib/firmware/ath6k/fw.bin` (bytes `b"FIRMWARE"`), `/etc/wpa_supplicant.conf` (bytes `b"psk=hunter2"`) and `/boot/cmdline` (bytes `b"console=ttyO2,115200n8 mem=198M root=/dev/mmcblk0p2"`). The GPT fixture has two named partitions `boot` and `rootfs`.

- [ ] **Step 1: Write the failing test** in `tests/test_fixtures.py`: `test_mbr_fixture_size_and_signature` asserts the file is a multiple of 512 bytes and bytes 510 to 511 are `55 AA`; `test_gpt_fixture_has_efi_part_signature` asserts bytes 512 to 519 are `b"EFI PART"`; `test_ext4_partition_contains_files` asserts `debugfs -R "cat /boot/cmdline" "<path>?offset=<start>"` prints the cmdline bytes above.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_fixtures -v` from the repo root. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the errors and `fixtures.py`. Build the ext4 image with `mke2fs -t ext4 -d <staging dir> <img> 8M` (no root, no mount), embed it at the partition offset, and write MBR and GPT tables in pure Python (`struct`). GPT needs both headers and the protective MBR; CRCs via `zlib.crc32`.
- [ ] **Step 4: Run** the same command. Expected: PASS (3 tests). If `mke2fs` or `debugfs` is missing, tests skip with a message naming the tool.
- [ ] **Step 5: Commit** `git commit -m "test: synthetic dump fixtures and error classes"`

### Task 2: Partition table parsing and carving

**Files:**
- Create: `scripts/dumpkit/ptable.py`, `scripts/dumpkit/carve.py`, `tests/test_ptable.py`, `tests/test_carve.py`

**Interfaces:**
- Consumes: Task 1 fixtures and error classes.
- Produces: `ptable.Partition` frozen dataclass: `index: int` (1-based, logical partitions numbered from 5 on MBR), `start: int` and `size: int` in bytes, `type_id: str` (MBR hex like `"83"`, GPT type GUID lowercase), `name: str` (GPT name, empty on MBR).
- Produces: `ptable.read_partitions(path: str) -> list[Partition]`. Prefers GPT over a protective MBR. Walks the extended-partition EBR chain. Raises `NoPartitionTable` when neither signature is valid.
- Produces: `carve.carve(src: str, offset: int, length: int, dst: str, force: bool = False) -> None`. Opens `src` read-only, streams in 1 MiB chunks, writes to `dst + ".part"` then renames. Raises `RangeErr` if `offset + length` exceeds the source size (before writing anything) and `OutputExists` if `dst` exists and not `force`. Refuses a `dst` equal to `src`.

- [ ] **Step 1: Write the failing tests.** `test_ptable.py`: `test_mbr_primary_and_logical` expects partition indexes `[1, 2, 5]` with the fixture's starts and sizes; `test_gpt_names` expects names `["boot", "rootfs"]`; `test_blank_file_raises_no_partition_table` (a 4096-byte zero file raises `NoPartitionTable`). `test_carve.py`: `test_carve_matches_source_slice`; `test_truncated_dump_raises_range_err_and_writes_nothing` (fixture truncated by 1 KiB, carving the last partition raises `RangeErr` and neither `dst` nor `dst.part` exists); `test_existing_output_refused` (raises `OutputExists`, original content untouched); `test_force_overwrites`; `test_dst_equal_src_refused`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_ptable tests.test_carve -v`. Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** both modules to the interfaces above.
- [ ] **Step 4: Run** the same command. Expected: PASS (8 tests).
- [ ] **Step 5: Commit** `git commit -m "feat: partition table parsing and range carving"`

### Task 3: Triage report

**Files:**
- Create: `scripts/dumpkit/triage.py`, `tests/test_triage.py`

**Interfaces:**
- Consumes: `ptable.read_partitions`, Task 1 fixtures.
- Produces: `triage.PATTERNS: tuple[bytes, ...]` = `(b"console=", b"mem=", b"root=", b"rootfstype=", b"U-Boot", b"x-loader", b"\xd0\x0d\xfe\xed", b"\x27\x05\x19\x56")`.
- Produces: `triage.triage(dump: str, outdir: str) -> str`. Creates `outdir` if needed, writes `outdir/triage.md`, returns its path. Sections in order: `# Dump triage`, `## File` (size in bytes, SHA-256, whether size is a multiple of 512), `## Partitions` (table, or the line `No partition table found.`), `## Pattern hits` (per pattern: count and up to 5 offsets in hex), `## Command-line fragments` (any `console=`/`mem=`/`root=` hit expanded to the printable run of up to 200 bytes around it), `## Secret-like files` (names only; for each ext4 partition, listed via `debugfs -R "ls -lR"`, matched against the Global Constraints list). Streams the dump in chunks (overlapping by `len(longest pattern) - 1`), never reads it whole into memory. No list longer than 40 lines; a truncation line says how many were omitted.

- [ ] **Step 1: Write the failing tests.** `test_report_has_sections_in_order`; `test_file_section_has_sha256` (matches `hashlib.sha256` of the fixture); `test_finds_cmdline_and_expands_it` (report contains `console=ttyO2,115200n8`); `test_pattern_offsets_are_hex`; `test_raw_image_without_table_still_reports` (zero-filled 64 KiB file containing `U-Boot 2013.01` at offset 0x100: report contains `No partition table found.` and a `U-Boot` hit at `0x100`); `test_secret_like_file_named_but_not_read` (report contains `wpa_supplicant.conf` and does not contain `hunter2`); `test_lists_capped_at_40_lines` (dump with 100 `mem=` hits: section has at most 40 hit lines plus the truncation line); `test_dump_not_modified` (mtime and sha256 unchanged).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_triage -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `triage.py`. Use `debugfs` only for listing names, never `cat`, on files that match the secret-like list.
- [ ] **Step 4: Run** the same command. Expected: PASS (8 tests).
- [ ] **Step 5: Commit** `git commit -m "feat: dump triage report"`

### Task 4: Blob extraction from a manifest

**Files:**
- Create: `scripts/dumpkit/blobs.py`, `tests/test_blobs.py`

**Interfaces:**
- Consumes: `ptable.read_partitions`, errors, Task 1 fixtures.
- Produces: `blobs.BlobEntry` frozen dataclass: `partition: int`, `fs_path: str`, `sha256: str`, `dest: str`.
- Produces: `blobs.parse_manifest(text: str) -> list[BlobEntry]`. One entry per line, four tab-separated fields: partition index, path inside the filesystem, lowercase hex sha256, destination file name (no directories). Blank lines and lines starting with `#` are ignored. Paths may contain spaces. A malformed line raises `ValueError` naming the line number.
- Produces: `blobs.extract_blobs(dump: str, manifest_path: str, outdir: str) -> list[str]`. For each entry runs `debugfs -R "dump <fs_path> <tmpfile>" "<dump>?offset=<start>"`, checks the sha256 against the manifest, and moves the file into `outdir/<dest>`. All files are staged in a temp dir under `outdir` and moved only after every checksum passes, so a failure leaves nothing in `outdir`. Raises `ChecksumMismatch` (message names `dest` and both hashes) and `RangeErr` for a missing partition.
- Manifest file `scripts/blobs.manifest` is created later from the dump analysis (see Deferred stages). The runbook's `scripts/blobs.sha256` is replaced by the sha256 column of this manifest; update `docs/emmc-dump-analysis.md` section 3.6 to say so.

- [ ] **Step 1: Write the failing tests.** `test_parse_manifest_ignores_comments_and_blanks`; `test_parse_manifest_keeps_spaces_in_path`; `test_parse_manifest_malformed_line_reports_line_number`; `test_extract_writes_verified_file` (extracts `/lib/firmware/ath6k/fw.bin` and the output equals `b"FIRMWARE"`); `test_checksum_mismatch_leaves_outdir_empty` (wrong hash raises `ChecksumMismatch`, `os.listdir(outdir) == []`); `test_missing_partition_raises_range_err`; `test_second_entry_failure_discards_first` (two entries, second wrong: outdir stays empty).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_blobs -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `blobs.py`. Use `subprocess.run` with an argument list (no shell). Quote the debugfs command string for spaces in `fs_path`.
- [ ] **Step 4: Run** the same command. Expected: PASS (7 tests).
- [ ] **Step 5: Commit** `git commit -m "feat: manifest-driven blob extraction"`

### Task 5: CLI, wrappers and CI

**Files:**
- Create: `scripts/dump_tool.py`, `scripts/triage-dump.sh`, `scripts/extract-blobs.sh`, `tests/test_cli.py`, `.github/workflows/ci.yml`
- Modify: `docs/emmc-dump-analysis.md` (sections 2 and 3.6: point at the tools), `CLAUDE.md` (one line on `scripts/`)

**Interfaces:**
- Consumes: Tasks 2 to 4.
- Produces CLI: `dump_tool.py partitions <dump>` (prints TSV `index  start  size  type_id  name`), `carve <dump> <offset> <length> <out> [--force]`, `triage <dump> <outdir>`, `extract-blobs <dump> <manifest> <outdir>`. Catches `DumpError`, prints `error: <message>` to stderr, exits with `exit_code`. `triage-dump.sh <dump> <outdir>` and `extract-blobs.sh <dump> <manifest> <outdir>` are wrappers that `cd` to the repo root and exec `python3 scripts/dump_tool.py`.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py` using `subprocess`: `test_partitions_tsv_has_five_columns`; `test_carve_exit_code_2_on_truncated_dump`; `test_carve_exit_code_5_when_output_exists`; `test_partitions_exit_code_3_on_blank_file`; `test_triage_wrapper_writes_report`; `test_extract_wrapper_exit_code_4_on_bad_hash`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_cli -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the CLI with `argparse` and the wrappers. Wrappers use `set -euo pipefail` and pass `"$@"` quoted.
- [ ] **Step 4: Add CI** `.github/workflows/ci.yml`: on push and pull request, ubuntu-latest, install `e2fsprogs shellcheck`, run `shellcheck scripts/*.sh` and `python3 -m unittest discover -s tests -v`. Run both locally first; expected: shellcheck clean and all tests pass.
- [ ] **Step 5: Run the whole suite** `python3 -m unittest discover -s tests -v`. Expected: PASS, about 32 tests, none skipped on a machine with e2fsprogs.
- [ ] **Step 6: Commit** `git commit -m "feat: dump_tool CLI, wrappers and CI"`

### Task 6: Buildroot skeleton and board DTB

**Files:**
- Create: `.gitmodules` (submodule `buildroot`), `external/external.desc`, `external/external.mk`, `external/Config.in`, `external/configs/echo_gen1_defconfig`, `external/board/amazon/echo-gen1/linux.fragment`, `external/board/amazon/echo-gen1/README.md`, `tests/test_skeleton.py`, `scripts/check-dtb.sh`
- Modify: `.github/workflows/ci.yml` (add a manual `workflow_dispatch` job that builds the DTB; not on every push)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: external tree name `ECHO` (`external.desc`: `name: ECHO`), so board paths are `$(BR2_EXTERNAL_ECHO_PATH)/board/amazon/echo-gen1/...`. `echo_gen1_defconfig` is the single target `make -C buildroot BR2_EXTERNAL=$PWD/external echo_gen1_defconfig`.
- Produces: `scripts/check-dtb.sh <buildroot-output-dir>` exits 0 only if `omap3-echo.dtb` exists under it and `dtc -I dtb -O dts` shows `amazon,omap3-echo` and a memory `reg` property.

- [ ] **Step 1: Pick the pins.** Run `git ls-remote --tags https://gitlab.com/buildroot.org/buildroot.git` and choose the newest **LTS** release tag (Buildroot LTS is the first release of each year). Record the tag and the kernel version that release selects in `external/board/amazon/echo-gen1/README.md`. Check that `omap3-echo.dts` exists in that kernel (path is `arch/arm/boot/dts/ti/omap/omap3-echo.dts` on recent kernels, `arch/arm/boot/dts/omap3-echo.dts` on older); the defconfig DTS name follows that layout. If the tag or kernel path cannot be verified, stop and report.
- [ ] **Step 2: Write the failing test** `tests/test_skeleton.py`: `test_external_desc_name_is_echo`; `test_defconfig_contains_required_lines` asserting these exact lines are present: `BR2_arm=y`, `BR2_cortex_a8=y`, `BR2_ARM_EABIHF=y`, `BR2_TARGET_ROOTFS_SQUASHFS=y`, `BR2_LINUX_KERNEL=y`, `BR2_LINUX_KERNEL_USE_DEFCONFIG=y`, `BR2_LINUX_KERNEL_DEFCONFIG="omap2plus"`, `BR2_LINUX_KERNEL_DTS_SUPPORT=y`; `test_defconfig_references_fragment_that_exists`; `test_gitmodules_pins_buildroot` (path `buildroot`, and the recorded tag appears in the README); `test_no_binary_artifacts_tracked` (`git ls-files` contains nothing matching `*.bin`, `*.img`, `*.dtb`, `*.fw`).
- [ ] **Step 3: Run** `python3 -m unittest tests.test_skeleton -v`. Expected: FAIL.
- [ ] **Step 4: Implement** the external tree and defconfig. The fragment starts with only the options needed for squashfs, overlayfs, ath6kl, the TLV320 codecs as modules, and zram. Add the submodule at the chosen tag (`git submodule add` then `git -C buildroot checkout <tag>`).
- [ ] **Step 5: Run** the unit test again. Expected: PASS.
- [ ] **Step 6: Verify the build.** Run `make -C buildroot BR2_EXTERNAL=$PWD/external O=$PWD/output echo_gen1_defconfig`, then `make -C buildroot O=$PWD/output host-dtc linux-configure` and the kernel's own rule for `ti/omap/omap3-echo.dtb` (Buildroot 2026.02.3 has no `linux-dtbs` target; the exact sequence is in `external/board/amazon/echo-gen1/README.md` and the `dtb` CI job; long: downloads a toolchain and the kernel, about 25 minutes and 8 GB for the DTB alone). Then `scripts/check-dtb.sh output`. Expected: exit 0. This step was run once for real during execution. If it cannot be run, say so and leave this step unchecked.
- [ ] **Step 7: Commit** `git commit -m "feat: Buildroot external tree, pinned submodule and board DTB check"`

---

## Deferred stages (blocked on the dump)

Write a second plan for each once the named inputs exist. Do not guess them.

| Stage | Blocked on | Input needed |
|---|---|---|
| Populate `scripts/blobs.manifest` | Dump analysis step 3.6 | Wi-Fi firmware file names, partition and paths, sha256 |
| Image layout (`genimage.cfg`) and flash scripts | Boot chain and partition findings | Where first-stage, bootloader, kernel and rootfs live; whether any stage is signed |
| Milestone 1: boot and Wi-Fi | Boot chain, UART port, firmware set | Findings 02, 03, 06 |
| DTS patch queue (reserved memory, ADC3101 nodes) | Findings 04 and 05 | Cause of the 58 MB gap, ADC bus and addresses |
| Milestones 2 and 3: playback, controls | Milestone 1 on real hardware | Working shell on the device |
| Milestone 4: capture | Findings 05 and a running device | ADC3101 wiring, driver choice |
| Milestone 5: Wyoming satellite and audio player | Milestone 4, wake-word decision | Package feasibility on Buildroot (fallback: approach B) |

Every deferred stage needs the real device for its acceptance check; none can be verified in CI.

## Self-review notes

- Spec coverage: 4.1 layout (Task 6 and deferred), 4.3 blob policy (Task 4 plus the git-ignored paths), 4.4 CI (Task 5, DTB job in Task 6), 4.5 kernel and DTS (Task 6 pin; patch queue deferred), 7 verification limits (Global Constraints). Sections 4.6 and 5 are deferred.
- Deviation from the spec: the spec shows a committed copy of `dts/omap3-echo.dts`. This plan uses the DTS from the pinned kernel tree instead, and adds a copy only when the patch queue needs one, to keep the board file close to upstream. The user's pasted DTS should be compared with the kernel's copy at Task 6 Step 1.
- Deviation from the runbook: `scripts/blobs.sha256` is replaced by the manifest's sha256 column (Task 4 updates the runbook).
