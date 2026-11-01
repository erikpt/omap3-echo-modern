# omap3-echo-modern

Goal: a custom Buildroot-based Linux image for the first-generation Amazon Echo
(TI DM3725), acting as a Home Assistant (Wyoming) voice satellite and a network audio
player. Wake-word placement is undecided until microphone bring-up works.

## Read first

- `docs/superpowers/specs/2026-11-01-echo-gen1-custom-image-design.md`: the approved-in-chat
  design (user review of the written spec is still pending). Facts there are marked
  verified or unverified. Keep that discipline.
- `docs/emmc-dump-analysis.md`: the runbook for analysing the eMMC dump. If you are asked
  to analyse a dump, follow it.

## Rules

- No Amazon binaries, firmware, bootloaders or dump contents in git. `dump/` and `blobs/`
  are git-ignored. Only checksums and extraction scripts are committed.
- Never print or commit secrets from the dump (Wi-Fi credentials, tokens, serials, keys,
  MAC addresses, calibration).
- Never modify the original dump. Never flash a device without the user's explicit go-ahead.
- Branches: `main` is docs only (reviewed conclusions). `firmware-develop` is where firmware
  and tooling code is built (branch off it, merge back). `stock-fw-analysis` is where the
  reverse engineering of the stock firmware and its raw findings (`docs/findings/`) go.
  `firmware-release` is for releases, created only when the user says it's time. Do not
  commit code to `main`.
- The dump tools in `scripts/` live on `firmware-develop`; merge that branch into
  `stock-fw-analysis` to use them. Do not commit tool changes from the analysis branch.
- Save tokens: summarise large outputs, cap with `head`, never dump whole images or strings.
- Without the device, only the DTS compile and image build can be verified. Do not claim
  boot, Wi-Fi, audio or mic behaviour works until it has been seen on hardware.
