# Amazon Echo (first generation) board files

BR2_EXTERNAL tree `ECHO`. Board: TI DM3725 (OMAP3630), Cortex-A8, 256 MB RAM.
Status: skeleton only. This builds a configuration and (see below) the board
DTB. Nothing here has been booted on hardware, and nothing is claimed about
boot, Wi-Fi or audio working.

## Pins

Buildroot tag: `2026.02.3`

Buildroot is the git submodule `buildroot/`, checked out at that tag. It is
never forked or patched. Bump it by checking out a newer LTS tag in the
submodule and updating this file together with the test in
`tests/test_skeleton.py`.

- Buildroot's default kernel for its generic Linux package in this release
  (`BR2_LINUX_KERNEL_LATEST_VERSION`) is 6.19.14. 6.19 is not an LTS series,
  so it is not used.
- This tree sets `BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="6.18.33"`. 6.18 is the
  2025 mainline LTS series, and 6.18.33 is the 6.18.x version this Buildroot
  release itself ships kernel and header hashes for (`linux/from-6.17/linux.hash`),
  so the tarball is hash-checked. Kernel headers follow the kernel
  (`BR2_KERNEL_HEADERS_AS_KERNEL`, series 6.18).
- Toolchain: Buildroot internal, musl, hard-float EABI, NEON.

## Board DTS

The board DTS is mainline's `omap3-echo.dts`, used from the pinned kernel tree
and not copied here. In 6.18.33 it is
`arch/arm/boot/dts/ti/omap/omap3-echo.dts` (checked on kernel.org's stable
tree at tag `v6.18.33`; the pre-6.5 location `arch/arm/boot/dts/omap3-echo.dts`
does not exist there). The defconfig therefore sets
`BR2_LINUX_KERNEL_INTREE_DTS_NAME="ti/omap/omap3-echo"`. That file has
`compatible = "amazon,omap3-echo", "ti,omap3630", "ti,omap3"` and a
`memory@80000000` node with `reg = <0x80000000 0xc600000>` (198 MB), the two
things `scripts/check-dtb.sh` checks.

Not done: comparing the mainline DTS with the DTS taken from the stock
firmware. That belongs with the dump analysis.

## Kernel fragment

`linux.fragment` is merged over `omap2plus_defconfig`. It enables squashfs
(with xz), overlayfs, the ath6kl Wi-Fi driver with SDIO support, the
TLV320AIC32x4 (I2C) and TLV320ADC3xxx codecs, and zram. Wi-Fi, codecs and zram
are modules.

Symbols were checked against the Kconfig files of kernel `v6.18.33`
(`fs/squashfs`, `fs/overlayfs`, `drivers/net/wireless/ath/ath6kl`,
`sound/soc/codecs`, `drivers/block/zram`): `SQUASHFS`, `SQUASHFS_XZ`,
`OVERLAY_FS`, `ATH6KL`, `ATH6KL_SDIO`, `SND_SOC_TLV320AIC32X4_I2C`,
`SND_SOC_TLV320ADC3XXX` and `ZRAM` exist. `WLAN`, `WLAN_VENDOR_ATH` and
`CFG80211` are the parents that make ath6kl visible; they were not read from
the Kconfig and are the least verified lines. The merged `.config` is not
inspected until the kernel is configured (the DTB check job does this step).

## Build and check

    git submodule update --init
    make -C buildroot BR2_EXTERNAL=$PWD/external O=$PWD/output echo_gen1_defconfig
    make -C output host-dtc linux-configure
    make -C output/build/linux-6.18.33 ARCH=arm \
        CROSS_COMPILE=$PWD/output/host/bin/arm-buildroot-linux-musleabihf- \
        ti/omap/omap3-echo.dtb
    scripts/check-dtb.sh output

Host tools needed beyond Buildroot's own list: `flex` and `bison` (the kernel's
bundled dtc is built from source). Buildroot 2026.02.3 has no `linux-dtbs` make target, so the DTB is built by
running the kernel's own DTB rule in the build directory after
`linux-configure`.

Not here yet, because they depend on the eMMC dump: bootloader, image layout
(genimage), flash scripts, firmware blobs and any userspace service.
