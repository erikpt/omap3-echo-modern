# Echo Gen 1 Custom Image: Design

Date: 2026-11-01
Status: Draft, awaiting review

## 1. Purpose

Replace Amazon's firmware on the first-generation Amazon Echo (TI DM3725 / OMAP3630) with a
custom, reproducible Linux image that turns the device into:

1. a **local voice satellite** (Home Assistant / Wyoming protocol), and
2. a **network audio player** (e.g. Snapcast or MPD).

Both share one audio stack. Wake-word placement (on the device or on the Home Assistant
server) is **deliberately undecided** until microphone bring-up is proven. The audio pipeline
must let either option slot in.

## 2. Non-goals

- Keeping Alexa or any Amazon software running.
- Redistributing any Amazon binary (firmware, Wi-Fi blobs, bootloaders).
- A RAM upgrade (stretch goal only, see section 9).
- Supporting any other Echo model.

## 3. Constraints and assumptions

| Item | Value | Status |
|---|---|---|
| SoC | TI DM3725CUS100 (non-PoP package), Cortex-A8, lot 65ZE9J9 | Verified (DTS, teardown, board photo) |
| Board | PCB 30-000731 REV 06, PCBA 31-000832 | Verified (board photo) |
| Test points near SoC | TP120, TP121, TP126, TP127 along the SoC's bottom edge; function unknown, possible UART candidates | Photo only; **unverified** |
| Unidentified parts | U17 (5-pin, marked `LCRB`), Y2 crystal (marked `D196J`) | Not identified |
| RAM | Micron MT46H64M32LFBQ-48 WT:C, 2 Gbit (256 MB) x32 LPDDR1, 90-ball | Verified (marking `D9RWH` decoded with Micron FBGA decoder) |
| RAM exposed by DTS | 198 MB (`0xc600000`) | Verified (DTS); the ~58 MB gap is believed to be a software carve-out, **unverified** |
| Storage | SanDisk iNAND SDIN7DP2-4G, 4 GB eMMC (U16) | Verified (photo, teardown) |
| PMIC | TPS65910 | Verified (DTS, teardown) |
| Playback path | McBSP2, TLV320DAC3203 codec (`ti,tlv320aic32x4` driver), class-D amp via GPIO enable | Verified (DTS); amp part per teardown |
| Capture path | Four TLV320ADC3101 stereo ADCs, seven microphones on the top board | From teardown; bus, address and audio wiring **unknown** |
| LEDs | Four LP5523-class drivers on I2C2 | Verified (DTS) |
| Inputs | Mute and help buttons, rotary encoder | Verified (DTS) |
| Wi-Fi | Atheros ath6kl, QCA6234 module on SDIO (mmc3) | Verified (DTS, teardown) |
| Boot chain | Unknown. No documented UART pinout found. No U-Boot port found. | **Unknown** |

Mainline Linux has carried `omap3-echo.dts` since 5.6. Mic and speaker audio were not
implemented at that time.

The eMMC dump (hardware arriving in about a week) is expected to resolve the boot chain,
partition layout, Wi-Fi firmware and the ADC wiring.

## 4. Architecture

### 4.1 Repository layout

Buildroot via `BR2_EXTERNAL`. Buildroot is pinned as a git submodule at an LTS tag and is
never forked or patched.

```
omap3-echo-modern/
├── buildroot/                  # submodule, pinned to an LTS tag
├── external/
│   ├── Config.in / external.mk / external.desc
│   ├── configs/echo_gen1_defconfig
│   ├── board/amazon/echo-gen1/
│   │   ├── linux.fragment
│   │   ├── dts/omap3-echo.dts
│   │   ├── patches/linux/
│   │   ├── genimage.cfg
│   │   ├── post-build.sh
│   │   └── rootfs-overlay/
│   └── package/
├── scripts/                    # build, flash, eMMC dump and blob extraction helpers
├── docs/
└── .github/workflows/
```

Output of `make echo_gen1_defconfig && make`: zImage, DTB, a read-only squashfs root with a
writable overlay, and a flashable image.

### 4.2 Reproducibility

Pinned Buildroot, pinned kernel, and Buildroot's own Cortex-A8 toolchain. No host
dependencies beyond Buildroot's documented requirements.

### 4.3 Blob policy

No Amazon-derived file is committed. A local extraction script reads the user's own eMMC
dump and copies the needed files (Wi-Fi firmware, any others found) into a git-ignored
directory. Checksums of the expected files are committed. The build fails with a clear
message if a required blob is missing.

### 4.4 CI

CI compiles the DTS against the pinned kernel and builds the image with the blob step
stubbed. This is the only verification possible without hardware.

### 4.5 Kernel and DTS

- Mainline LTS kernel, version pinned by the chosen Buildroot release (confirmed at plan
  time). `omap2plus_defconfig` trimmed by a config fragment. No vendor kernel.
- The board DTS starts from mainline `omap3-echo.dts` plus a small patch queue, kept close
  to upstream so fixes can be sent back.
- Known items in the DTS as supplied:
  - Memory node is 198 MB versus a 256 MB chip. Unchanged until the dump explains it.
  - No microphone path in the sound card.
  - Cosmetic: node name `lp552D@35`.

### 4.6 Userspace

Minimal: ALSA (no PipeWire), BusyBox init, SSH, a Wyoming satellite, and an audio player
(Snapcast or MPD). A read-only root with zram swap keeps most of the ~198 MB available.
If packaging the Python Wyoming satellite proves impractical on Buildroot, the fallback is
a postmarketOS/Alpine armv7 base (approach B). That switch is a decision for the user.

## 5. Milestones and acceptance checks

Each check needs the real device.

1. **Boot.** Kernel and rootfs reach a shell. Wi-Fi associates (ath6kl with extracted
   firmware). SSH works.
2. **Playback.** `aplay` produces sound from the speaker. Amp enable GPIO behaves.
3. **Controls.** Mute and help buttons and the rotary encoder produce input events. The LED
   ring can be driven.
4. **Capture.** Find the ADC3101 bus, address and audio wiring. Capture audio from the
   mic array with `arecord`. Highest risk.
5. **Services.** Wyoming satellite connects to Home Assistant. Audio player works. Wake word
   placement decided and implemented.

Milestones 1–3 follow the existing DTS closely. Milestone 4 has real unknowns.

## 6. Mic bring-up plan (milestone 4)

Discover where the four TLV320ADC3101 devices sit and how their audio reaches the SoC,
in this order:

1. The eMMC dump: Amazon's kernel, config, ALSA configuration and any board data.
2. `i2cdetect` and register probing on the running image.
3. Board tracing if needed.

Starting driver: mainline `tlv320adc3xxx`. Fallback: a small userspace tool that programs
the ADC registers over I2C.

## 7. Verification limits

Without the device, only these can be checked: the DTS compiles, the kernel and image
build, and scripts pass their own tests. Boot, Wi-Fi, audio, LEDs and mic behaviour cannot
be verified without hardware. No milestone is considered done until its acceptance check
passes on the device.

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Boot chain locked or signed | Cannot boot a custom kernel | Dump first; read the chain; fall back to what the dump shows is possible |
| UART location unknown | Hard to debug early boot | Find via dump and board inspection; document in `docs/` |
| Mic path not described anywhere | Satellite half blocked | Section 6 plan; playback-only audio player is still deliverable |
| 198 MB RAM | Limits wake word and Python | Lean rootfs, zram, investigate the 58 MB carve-out, B as a fallback |
| Obsolete or hard-to-source parts | Repair difficulty | Not a build risk; noted only |

## 9. Open questions

- What is the boot chain (ROM, first-stage, bootloader) and is any stage unsigned?
- Where is the UART? Candidates: TP120/121/126/127. Resolve by tracing each to its SoC ball
  and by reading the console port from Amazon's kernel command line in the dump.
- What reserves the ~58 MB?
- Which bus, address and port do the ADC3101s use?
- Stretch: could the RAM be upgraded? Needs a larger pin-compatible part, routed extra
  address lines, and a replaceable first-stage bootloader. Not planned.
- Wake word: device or server? Decide after milestone 4.

## 10. Hardware notes to write

Maintain `docs/hardware.md` with each fact marked verified or inferred, using the table in
section 3 as the starting point.

## 11. Sources

- Phoronix: Linux 5.6 can boot the original Amazon Echo.
- Mainline commit adding `omap3-echo.dts` (patchwork thread 11309539).
- Institution of Electronics teardown of a defunct Echo.
- Micron FBGA decoder (`D9RWH` → `MT46H64M32LFBQ-48 WT:C`; Micron part page lists 2 Gb,
  x32, 1.8 V, 90-ball, obsolete).
