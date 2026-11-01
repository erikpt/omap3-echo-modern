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
- Work on branch `claude/gen1-alexa-custom-image-6hgv1q`. Do not push elsewhere.
- Save tokens: summarise large outputs, cap with `head`, never dump whole images or strings.
- Without the device, only the DTS compile and image build can be verified. Do not claim
  boot, Wi-Fi, audio or mic behaviour works until it has been seen on hardware.
