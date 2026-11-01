import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_DTB = os.path.join(ROOT, "scripts", "check-dtb.sh")

GOOD_DTS = """/dts-v1/;
/ {
\tcompatible = "amazon,omap3-echo", "ti,omap3630", "ti,omap3";
\tmemory@80000000 {
\t\tdevice_type = "memory";
\t\treg = <0x80000000 0xc600000>;
\t};
};
"""
NO_MEMORY_DTS = GOOD_DTS.replace("memory@80000000", "chosen").replace(
    'device_type = "memory";', "")
WRONG_COMPAT_DTS = GOOD_DTS.replace("amazon,omap3-echo", "ti,omap3-beagle")


def run(*args):
    return subprocess.run(["bash", CHECK_DTB, *args], capture_output=True, text=True,
                          cwd=tempfile.gettempdir())


class CheckDtbTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def test_script_is_executable(self):
        self.assertTrue(os.access(CHECK_DTB, os.X_OK))

    def test_usage_error_without_argument(self):
        r = run()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage", r.stderr.lower())

    def test_missing_directory_fails(self):
        r = run(os.path.join(self.tmp, "nope"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a directory", r.stderr)

    def test_dir_without_dtb_fails_with_clear_message(self):
        os.makedirs(os.path.join(self.tmp, "build"))
        r = run(self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("omap3-echo.dtb not found", r.stderr)

    @unittest.skipUnless(shutil.which("dtc"), "dtc not installed")
    def test_good_dtb_passes_and_bad_dtbs_fail(self):
        def build(name, dts):
            d = os.path.join(self.tmp, name)
            os.makedirs(d)
            src = os.path.join(d, "x.dts")
            with open(src, "w") as f:
                f.write(dts)
            subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o",
                            os.path.join(d, "omap3-echo.dtb"), src],
                           check=True, capture_output=True)
            return d

        r = run(build("good", GOOD_DTS))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(build("nomem", NO_MEMORY_DTS))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("memory", r.stderr)
        r = run(build("compat", WRONG_COMPAT_DTS))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("amazon,omap3-echo", r.stderr)


if __name__ == "__main__":
    unittest.main()
