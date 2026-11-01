import configparser
import fnmatch
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTERNAL = os.path.join(ROOT, "external")
BOARD = os.path.join(EXTERNAL, "board", "amazon", "echo-gen1")
DEFCONFIG = os.path.join(EXTERNAL, "configs", "echo_gen1_defconfig")
README = os.path.join(BOARD, "README.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def defconfig_lines():
    return [line.strip() for line in read(DEFCONFIG).splitlines()]


class SkeletonTests(unittest.TestCase):
    def test_external_desc_name_is_echo(self):
        desc = read(os.path.join(EXTERNAL, "external.desc"))
        self.assertIn("name: ECHO", desc.splitlines())

    def test_defconfig_contains_required_lines(self):
        lines = defconfig_lines()
        for required in (
            "BR2_arm=y",
            "BR2_cortex_a8=y",
            "BR2_ARM_EABIHF=y",
            "BR2_TARGET_ROOTFS_SQUASHFS=y",
            "BR2_LINUX_KERNEL=y",
            "BR2_LINUX_KERNEL_USE_DEFCONFIG=y",
            'BR2_LINUX_KERNEL_DEFCONFIG="omap2plus"',
            "BR2_LINUX_KERNEL_DTS_SUPPORT=y",
        ):
            self.assertIn(required, lines)

    def test_defconfig_references_fragment_that_exists(self):
        m = None
        for line in defconfig_lines():
            m = re.match(r'BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES="(.*)"$', line)
            if m:
                break
        self.assertIsNotNone(m, "defconfig sets no kernel config fragment")
        for ref in m.group(1).split():
            prefix = "$(BR2_EXTERNAL_ECHO_PATH)/"
            self.assertTrue(ref.startswith(prefix), ref)
            self.assertTrue(os.path.isfile(os.path.join(EXTERNAL, ref[len(prefix):])), ref)

    def test_gitmodules_pins_buildroot(self):
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(ROOT, ".gitmodules"))
        self.assertIn('submodule "buildroot"', cfg.sections())
        self.assertEqual(cfg['submodule "buildroot"']["path"], "buildroot")
        m = re.search(r"^Buildroot tag: `?(\d{4}\.\d{2}(?:\.\d+)?)`?\s*$",
                      read(README), re.MULTILINE)
        self.assertIsNotNone(m, "README records no 'Buildroot tag: <tag>' line")
        tag = m.group(1)
        # Only compare with the checked-out submodule when it is present (CI
        # may not check out submodules).
        if os.path.exists(os.path.join(ROOT, "buildroot", ".git")):
            r = subprocess.run(
                ["git", "-C", os.path.join(ROOT, "buildroot"), "describe", "--tags",
                 "--exact-match"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), tag)

    def test_no_binary_artifacts_tracked(self):
        r = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        bad = [p for p in r.stdout.splitlines()
               if any(fnmatch.fnmatch(os.path.basename(p), pat)
                      for pat in ("*.bin", "*.img", "*.dtb", "*.fw"))]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
