import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _runbook():
    with open(os.path.join(ROOT, "docs", "emmc-dump-analysis.md"), encoding="utf-8") as f:
        return f.read()


class RunbookTests(unittest.TestCase):
    def test_no_stale_promises_about_the_extract_script(self):
        text = _runbook()
        self.assertNotIn("added in a later task", text)
        self.assertNotIn("Write `scripts/extract-blobs.sh`", text)

    def test_blob_extraction_section_matches_the_shipped_tools(self):
        text = _runbook()
        self.assertIn("scripts/extract-blobs.sh <dump> scripts/blobs.manifest blobs/", text)
        self.assertIn("firmware-develop", text.split("### 3.6")[1].split("### 3.7")[0])
        self.assertIn("symlink", text.split("### 3.6")[1].split("### 3.7")[0])

    def test_outputs_list_only_the_manifest(self):
        outputs = _runbook().split("## 4. Consolidated outputs")[1].split("## 5.")[0]
        self.assertIn("`scripts/blobs.manifest`", outputs)
        self.assertNotIn("`scripts/extract-blobs.sh` and", outputs)

    def test_setup_documents_triage_privacy_and_tool_paths(self):
        setup = _runbook().split("## 2. Setup")[1].split("## 3.")[0]
        self.assertIn("scripts/triage-dump.sh dump/work.bin dump/triage", setup)
        self.assertIn("SHA-256", setup)
        self.assertIn("redacted", setup)
        self.assertIn("brew --prefix e2fsprogs", setup)
        self.assertIn("/usr/sbin", setup)
        self.assertIn("shasum -a 256", setup)


if __name__ == "__main__":
    unittest.main()
