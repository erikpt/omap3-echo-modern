import configparser
import contextlib
import fnmatch
import os
import re
import subprocess
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTERNAL = os.path.join(ROOT, "external")
BOARD = os.path.join(EXTERNAL, "board", "amazon", "echo-gen1")
DEFCONFIG = os.path.join(EXTERNAL, "configs", "echo_gen1_defconfig")
README = os.path.join(BOARD, "README.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _readme_tag():
    m = re.search(r"^Buildroot tag: `?(\d{4}\.\d{2}(?:\.\d+)?)`?\s*$", read(README), re.MULTILINE)
    return m.group(1) if m else None


def _submodule_checked_out():
    # CI may not check out submodules.
    return os.path.exists(os.path.join(ROOT, "buildroot", ".git"))


def _submodule_tag():
    """(tag, stderr) of the checked-out Buildroot submodule; tag is None if git cannot
    name one (shallow or tagless checkout)."""
    r = subprocess.run(
        ["git", "-C", os.path.join(ROOT, "buildroot"), "describe", "--tags", "--exact-match"],
        capture_output=True, text=True)
    return (r.stdout.strip() if r.returncode == 0 else None), r.stderr


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

    def _run_pin_test_with(self, tag_result, readme_text=None):
        """Run test_gitmodules_pins_buildroot with a checked-out submodule whose tag lookup
        returns `tag_result`; return the unittest result."""
        result = unittest.TestResult()
        case = SkeletonTests("test_gitmodules_pins_buildroot")
        patches = [mock.patch("tests.test_skeleton._submodule_checked_out", return_value=True),
                   mock.patch("tests.test_skeleton._submodule_tag", return_value=tag_result)]
        if readme_text is not None:
            real_read = read
            patches.append(mock.patch(
                "tests.test_skeleton.read",
                side_effect=lambda p: readme_text if p == README else real_read(p)))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            case.run(result)
        return result

    def test_tagless_submodule_skips_only_the_tag_comparison(self):
        result = self._run_pin_test_with((None, "fatal: No names found"))
        self.assertEqual(result.failures + result.errors, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("tag", result.skipped[0][1])

    def test_matching_tag_does_not_skip(self):
        result = self._run_pin_test_with((_readme_tag(), ""))
        self.assertEqual(result.failures + result.errors + result.skipped, [])

    def test_wrong_tag_still_fails(self):
        result = self._run_pin_test_with(("1999.01", ""))
        self.assertEqual(len(result.failures), 1)

    def test_tagless_submodule_still_checks_readme_unconditionally(self):
        result = self._run_pin_test_with((None, "fatal"), readme_text="no tag line here\n")
        self.assertEqual(len(result.failures), 1)
        self.assertIn("README records no", result.failures[0][1])

    def test_gitmodules_pins_buildroot(self):
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(ROOT, ".gitmodules"))
        self.assertIn('submodule "buildroot"', cfg.sections())
        self.assertEqual(cfg['submodule "buildroot"']["path"], "buildroot")
        m = re.search(r"^Buildroot tag: `?(\d{4}\.\d{2}(?:\.\d+)?)`?\s*$",
                      read(README), re.MULTILINE)
        self.assertIsNotNone(m, "README records no 'Buildroot tag: <tag>' line")
        tag = m.group(1)
        # Only compare with the checked-out submodule when it is present.
        if _submodule_checked_out():
            found, why = _submodule_tag()
            if found is None:
                # A shallow or tagless checkout cannot name its tag; skip just this check.
                with self.subTest("submodule tag"):
                    self.skipTest("buildroot submodule has no resolvable tag "
                                  "(shallow or tagless checkout): " + why.strip())
            else:
                self.assertEqual(found, tag)

    def test_no_binary_artifacts_tracked(self):
        r = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        bad = [p for p in r.stdout.splitlines()
               if any(fnmatch.fnmatch(os.path.basename(p), pat)
                      for pat in ("*.bin", "*.img", "*.dtb", "*.fw"))]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
