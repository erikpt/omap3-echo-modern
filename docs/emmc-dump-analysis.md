# eMMC dump analysis runbook

Audience: a local Claude Code instance with access to the Echo gen 1 eMMC dump.
Goal: answer the open questions in
`docs/superpowers/specs/2026-11-01-echo-gen1-custom-image-design.md` (section 9) and produce
the artifacts the build needs. Read that spec first, especially sections 3, 5, 8 and 9.

## 0. Rules

1. **Never modify the dump.** Work on a copy, or mount read-only. Record SHA-256 of the
   original before and after.
2. **Never commit dump contents.** No images, partitions, firmware, bootloaders, kernels,
   rootfs files or extracted Amazon binaries go in git. `.gitignore` covers `dump/` and
   `blobs/`; keep everything under those two directories.
3. **The dump contains private data**: Wi-Fi credentials, Amazon account tokens, device
   serial and keys, possibly certificates. Do not print them, paste them into notes, or
   commit them. In findings, redact (`<redacted>`). Do not read files that exist only to hold
   secrets (for example anything named like `*secret*`, `*token*`, `*.pem`, `*.key`,
   `wpa_supplicant.conf`) beyond confirming they exist and where.
4. **Save tokens.** Summarise with `head`, `grep -c`, `file`, `wc` and `ls`. Never `cat` or
   `strings` a whole image or large file into the conversation. Cap output (e.g. `| head -40`).
5. **Mark every fact** in notes as `verified` (seen directly in the dump, with the command or
   path that showed it) or `inferred`. Do not blur them.
6. Work on branch `stock-fw-analysis` (reverse engineering of the stock firmware happens
   there). Commit findings (text only) in small commits. Do not push to other branches. The
   dump tools (`scripts/`, once built) live on `firmware-develop`; merge that branch into
   `stock-fw-analysis` to use them, and do not commit tool changes from here.

## 1. Inputs to establish first

Ask the user, or check, before analysing:

- What the `.bin` actually is: the whole device (`/dev/mmcblk0`) only, or also `boot0`,
  `boot1` and RPMB. A plain read of `/dev/mmcblk0` **does not include the eMMC boot
  partitions**. On an eMMC with boot partitions, the first-stage bootloader may live in
  `boot0`/`boot1` only. If the dump is a single image of the user area, flag that boot0/boot1
  may be missing and tell the user how to dump them (separate reads of `mmcblk0boot0` and
  `mmcblk0boot1`, or a programmer that exposes them). Do not guess that the bootloader is absent.
- The expected size. The chip is SanDisk SDIN7DP2-4G, a 4 GB part; a user-area image should
  be close to 4 GB (a little under, 3.6 to 3.8 GiB is typical), not exactly 4 GiB. Note the actual size.
- Whether the dump was read with the chip in-circuit or via a reader, and whether any read
  errors occurred. Note sector-level errors if the user has them.

Record answers in `docs/findings/00-dump-provenance.md`.

## 2. Setup

```bash
mkdir -p dump blobs docs/findings
sha256sum dump/emmc.bin | tee dump/emmc.bin.sha256
cp --reflink=auto dump/emmc.bin dump/work.bin     # analyse the copy
chmod a-w dump/emmc.bin
```

Tools (install what is missing, none are product dependencies): `binwalk`, `file`, `fdisk` or
`sfdisk` or `parted`, `losetup`, `squashfs-tools` (`unsquashfs`), `e2fsprogs` (`debugfs`,
`e2ls`), `device-tree-compiler` (`dtc`), `u-boot-tools` (`dumpimage`, `mkimage`), `xxd`,
`strings`, `ubi-reader` if UBI appears, `cpio`, `lz4`, `lzop`, `xz`.

## 3. Procedure and what to record

Each step ends with a short findings file in `docs/findings/`. Keep each under about 80 lines.

### 3.1 Layout (`01-layout.md`)

- `file dump/work.bin`; `fdisk -l` / `sfdisk -d` / `parted print`. Is it MBR or GPT? Record
  partition table verbatim (offsets, sizes, types, names).
- Check for raw regions before the first partition: bytes at offset 0, any TI ROM boot
  header (the OMAP3 ROM looks for a boot image with a small header: a size and load address
  pair at the start of the first-stage image, and on HS devices a signed container). Run
  `xxd -l 512 dump/work.bin` and `binwalk dump/work.bin | head -60`.
- For each partition: filesystem type (`file -s`), label, UUID, used versus total size.
- Loop-mount filesystems read-only: `losetup -r -P`, then `mount -o ro,noload` for ext4.
  For squashfs use `unsquashfs -l` first, extract only what you need.

### 3.2 Boot chain (`02-boot-chain.md`)

This answers: is any stage unsigned and replaceable? Look for, in order:

1. First-stage loader: a small image (tens of KB) at the start of the device or in boot0:
   x-loader / MLO / SPL. Strings of interest: `U-Boot`, `x-loader`, `Texas Instruments`,
   `SPL`, `Loading`, version strings. Record offset, size, version string.
2. Second-stage: U-Boot (`binwalk` often flags it; look for `U-Boot` and a uImage header,
   magic `27051956`). Record version, build date, and whether it has a console.
3. Kernel: uImage/zImage/FIT. Record kernel version string and compression. Check for a
   concatenated or appended device tree (FDT magic `d00dfeed`): extract any with
   `binwalk -e` or `dd`, decompile with `dtc -I dtb -O dts`. A DTB or board file from
   Amazon's own kernel is the single most useful artifact (see 3.5).
4. **Signing evidence.** Any of: a certificate or signature block after an image, a
   TI "CH" configuration header, a secure-boot/TOC structure, fields named `signature`,
   `rsa`, `x509`, `sha256` near an image header, a verified-boot library string
   (`dm-verity`, `veritysetup`, `avb`, `ima`). Report exactly what you saw and where.
   **Do not conclude "locked" or "unlocked" from absence of evidence.** Say what the dump
   shows and what it cannot show (eFuse state and the SoC's device type, GP versus HS, are
   not in the eMMC).
5. Boot arguments. Search for `console=`, `root=`, `rootfstype=`, `mem=`, `ro `,
   `androidboot`, `init=`, `earlycon`. These are the kernel command line (it may sit in U-Boot
   environment, a boot script, or a header). Record the full line verbatim minus secrets.

Answer explicitly in the file: which stage loads which, from where, and which are replaceable.

### 3.3 UART and console (`03-console.md`)

Answers the "where is the UART" question as far as software evidence can.

- From the command line: which `console=` device (`ttyO2` is UART3 on OMAP3, `ttyO0` UART1,
  `ttyO1` UART2, `ttyO3` UART4) and baud rate. Record.
- Whether the Amazon userspace starts a getty or console on a serial port (`inittab`,
  upstart/systemd units, `securetty`). Record, do not guess pinout from this.
- Whether kernel logging is silenced (`quiet`, `loglevel=`).
- Any strings naming a debug UART pin or GPIO.
- Output: the candidate UART (for example UART3) so the user can match it to the physical
  test points TP120/TP121/TP126/TP127 by continuity to the corresponding SoC balls
  (`uart3_tx_irtx`, `uart3_rx_irrx` for UART3). **Software cannot tell which test point it is.**

### 3.4 Memory map and the missing 58 MB (`04-memory.md`)

The DTS exposes 198 MB at `0x80000000`; the chip is 256 MB. Find what reserves the rest:

- `mem=` or `memmap=` on the kernel command line.
- A `reserved-memory` or `/memreserve/` entry in any Amazon DTB.
- Board-file or kernel config references to DSP, framebuffer, `CMA`, `ION`, `carveout`, or
  `cma=` sizes.
- Strings in U-Boot such as `mem`, `bi_dram`, `DRAM:`.
- Any known reserved region at the top of memory (e.g. a trusted-execution or crash-dump area).

Record the arithmetic: 256 MB minus 198 MB is 58 MB, address range `0x8c600000` to
`0x90000000`. State whether the kernel can use it or whether something owns it.

### 3.5 Audio and microphones (`05-audio.md`)

This is the highest-risk milestone. Find:

- The four TLV320ADC3101 devices: I2C bus number and address for each. Look in Amazon's
  DTB, board file strings (`tlv320adc3101`, `adc3101`, `adc3xxx`), kernel module names under
  `lib/modules`, and any userspace setup scripts or ALSA UCM/asound config.
- How their audio reaches the SoC: which McBSP (`mcbsp1`..`mcbsp5`), DAI format (I2S, TDM),
  slot count, clocking (who is master, which MCLK), and which GPIOs reset or enable them.
- How the seven microphones map to ADC channels (ordering and any beamformer config).
- The DAC/speaker side for cross-check: codec address 0x18, `sys_clkout1` at 19.2 MHz,
  amp-enable GPIO (the DTS has gpio_129), reset gpio_74. Confirm or contradict the supplied DTS.
- ALSA mixer state or init scripts that set gains, routing or `amixer` controls. Record
  the settings, as they show the working configuration.
- Kernel version and which codec drivers Amazon's kernel includes. Compare against the
  mainline driver `tlv320adc3xxx`.

If Amazon's DTB exists, produce `docs/findings/amazon-board.dts` (decompiled, secrets-free)
and note differences from the supplied `omap3-echo.dts`. Whether that DTS file may be
committed is a user decision; **ask before committing it**.

### 3.6 Wi-Fi firmware (`06-wifi.md`) and blob extraction

- Locate the firmware directory used by the ath6kl driver (look under `lib/firmware`,
  `etc`, vendor-specific paths). List filenames, sizes, SHA-256.
- Identify which files the driver needs for QCA6234 (typically a firmware image, an OTP or
  board-data file, and possibly a calibration file). Check the kernel modules and driver
  strings (`ath6kl`, `fw-`, `bdata`, `otp`) for the exact filenames requested.
- Check for per-device calibration or MAC address storage (a partition or file). Record where
  it is; **do not extract or print MAC addresses or calibration of this specific unit into git.**
- Write `scripts/extract-blobs.sh`: reads the user's dump, copies exactly the required files
  into `blobs/` (git-ignored), and verifies each against SHA-256 values committed in
  `scripts/blobs.sha256`. It must fail clearly if the dump or a file is missing.
  The script and checksums are committed; the blobs are not.

### 3.7 Rootfs and userspace survey (`07-userspace.md`)

Only what helps the build, in short form:

- OS family and kernel version (`/etc/os-release`, `lib/modules/*`).
- Init system and how services start.
- Any useful init, udev, or ALSA setup scripts to reproduce (describe, do not copy wholesale).
- Partition roles: A/B slots, persistent data, logs, factory data, recovery.
- Where device-specific, unrecoverable data lives (calibration, serials, keys). These must
  be backed up byte-for-byte before the user writes anything to the device.

### 3.8 Backup plan (`08-backup.md`)

Before any write to the device, the user needs a restore path. Document:

- Which regions must be preserved (everything, but list the irreplaceable ones).
- How to restore (reader/programmer commands, offsets) to the extent the dump shows it.
- A checklist the user can follow so a bad flash is recoverable. If the dump is incomplete
  (no boot0/boot1), say that restore is **not guaranteed** until those are dumped.

## 4. Consolidated outputs

After the steps above:

1. `docs/hardware.md`: a table of facts, each row `verified` or `inferred` with its source
   (finding file and step). Start from section 3 of the spec.
2. `docs/findings/README.md`: a one-page summary answering each spec section 9 question:
   boot chain, UART, 58 MB, ADC wiring, plus a recommended next step for each.
3. A short list titled **Spec changes needed**: anything that contradicts or sharpens the
   spec (for example "bootloader is signed, so milestone 1 needs approach X"). Do not edit
   the spec yourself. The user and the cloud session review that list first.
4. `scripts/extract-blobs.sh` and `scripts/blobs.sha256` as described.

## 5. Decision gates (stop and report)

Stop and ask the user, with a one-paragraph summary, if you find:

- Evidence the boot chain is **signed or verified** at any stage (the whole approach may need
  to change).
- That the dump is **incomplete** (no boot0/boot1, truncated, or read errors).
- That the device's **unique secrets** sit in readable plaintext, so the user can decide how
  to handle the dump file itself.
- Anything suggesting a **write** to the device could brick it (for example calibration or
  boot data stored outside the main partitions).

## 6. What not to do

- Do not flash, write to, or otherwise modify any device or the original dump.
- Do not try to defeat any security mechanism you find; report it.
- Do not commit anything under `dump/` or `blobs/`.
- Do not change the Buildroot layout or the spec beyond the "Spec changes needed" list.

## 7. Handing back

Commit text findings (`docs/findings/`, `docs/hardware.md`) to `stock-fw-analysis` and push.
Tell the user the commit hash and paste the "Spec changes needed" list in your reply. The
cloud session will read the findings from that branch to update the spec and write the plan.
Once the user has reviewed the findings, the conclusions (the `docs/hardware.md` updates and
accepted spec changes) are promoted to `main`; raw findings stay on `stock-fw-analysis`.
