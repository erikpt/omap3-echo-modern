# Echo gen 1 hardware notes

Every fact is marked **verified** (seen directly, with the source) or **inferred** (reasoned,
not seen) or **unknown**. Update this file as the eMMC dump and board probing answer the
open items. Source keys: DTS = the supplied `omap3-echo.dts`; teardown = Institution of
Electronics article on a defunct Echo; photo = the owner's board photos; decoder = Micron
FBGA decoder and Micron part page.

## Board

| Item | Value | Status | Source |
|---|---|---|---|
| PCB / PCBA | `30-000731` REV 06 / `31-000832` | verified | photo |
| Layout | Digital board (SoC, RAM, eMMC, Wi-Fi, PMIC), bottom board (power, audio), top board (mics, LEDs) | verified | teardown |

## Digital board

| Part | Detail | Status | Source |
|---|---|---|---|
| SoC | TI `DM3725CUS100`, Cortex-A8, non-PoP package, lot `65ZE9J9` | verified | photo, DTS |
| RAM | Micron `MT46H64M32LFBQ-48 WT:C`: 2 Gbit (256 MB), x32 LPDDR1, 1.8 V, 208 MHz, 90-ball VFBGA 8x13 mm, obsolete. Marking `D9RWH` | verified | photo, decoder |
| RAM seen by DTS | 198 MB at `0x80000000` (`0xc600000`) | verified | DTS |
| RAM gap | ~58 MB (`0x8c600000` to `0x90000000`) not exposed; believed to be a software carve-out | **unknown** | n/a |
| RAM on other units | Samsung `K4X2G323PD-8GD8` on the teardown unit, same class | verified | teardown |
| eMMC | SanDisk iNAND `SDIN7DP2-4G`, 4 GB, lot `6352DFAT70HK`, ref U16 | verified | photo, teardown |
| PMIC | TI `TPS65910` | verified | DTS, teardown |
| Wi-Fi / BT | Qualcomm Atheros `QCA6234X-AM2D`, ref U6, date code 1638; SDIO on mmc3; DTS node `atheros,ath6kl` | chip verified; firmware set and driver fit **unknown** | photo, DTS |
| Unidentified | U17 (5-pin, marked `LCRB`); Y2 crystal (marked `D196J`) | unknown | photo |

## Audio, LEDs, inputs

| Item | Detail | Status | Source |
|---|---|---|---|
| Codec | TLV320DAC3203 per teardown; DTS uses the `ti,tlv320aic32x4` driver at I2C3 `0x18`, clocked from `sys_clkout1` at 19.2 MHz | DTS verified; part-to-driver match inferred | DTS, teardown |
| Speaker amp | TPA3110D2 class-D per teardown; DTS models it as a GPIO-enabled amplifier (gpio_129) | DTS verified; part from teardown | DTS, teardown |
| Playback link | McBSP2, I2S, codec is clock master | verified | DTS |
| Mic ADCs | Four TLV320ADC3101 stereo ADCs on the top board, seven microphones | from teardown | teardown |
| Mic wiring | I2C bus and address, McBSP, TDM or I2S, clocking, resets, channel-to-mic order | **unknown** | n/a |
| LED drivers | Four LP5523-class drivers on I2C2 at `0x32` to `0x35`; teardown lists LP55231 and twelve LEDs | verified (DTS); part variant inferred | DTS, teardown |
| Buttons | Mute (gpio_70), help (gpio_72) | verified | DTS |
| Rotary encoder | gpio_69 and gpio_76 | verified | DTS |

## GPIO and pin notes (from the DTS, not yet checked against hardware)

| Function | GPIO |
|---|---|
| Mute button | gpio_70 (gpio3 6) |
| Help button | gpio_72 (gpio3 8) |
| Rotary encoder | gpio_69, gpio_76 |
| Codec reset | gpio_74 (gpio3 10) |
| Speaker amp enable | gpio_129 (gpio5 1) |
| LP5523 (q1) enable | gpio_109 (gpio4 13) |
| Wi-Fi SDIO reset | gpio1 21 |

## Debug access

| Item | Detail | Status |
|---|---|---|
| Documented UART pinout | none found | unknown |
| Candidate test points | TP120, TP121, TP126, TP127 along the SoC's bottom edge | candidates only, function unverified |
| Other pads | A pad array under the removable foot; the teardown guesses test or programming use. Gold edge pads on the digital board, likely contacts to the other boards | unverified |
| OMAP3 console UARTs | UART3 is usual for OMAP3 debug; the real port comes from Amazon's kernel command line in the dump | inferred |

## Boot chain

All **unknown** until the dump is analysed: first-stage loader, bootloader, whether any
stage is signed or verified, whether the OMAP ROM boots unsigned code from the eMMC, and
whether `boot0`/`boot1` hold the first stage. Prior art: mainline Linux has carried the board
DTS since 5.6 (so the kernel runs on the device); no U-Boot port or boot guide was found.

## Pending from the dump

1. Boot chain and signing evidence.
2. UART port from the kernel command line, then physical match by continuity.
3. Cause of the ~58 MB gap.
4. ADC3101 wiring.
5. Wi-Fi firmware files and any per-unit calibration storage.
6. Partition layout and a restore plan.

See `docs/emmc-dump-analysis.md`.
