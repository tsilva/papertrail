import tempfile
import unittest
from pathlib import Path

from papertrail.engine import DocumentEngine

from tests.support import make_test_runtime


class DocumentEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.runtime = make_test_runtime(root)
        self.engine = DocumentEngine(self.runtime)
        self.processed = self.runtime.paths.processed

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_backfill_adds_derived_fields_without_llm(self):
        doc_path = self.processed / "statement.xlsx"
        doc_path.write_text("xlsx")
        metadata = {"date_issued": "2026-01-01", "document_type": "bank-statement", "issuing_party": "bank"}
        result = self.engine.upsert(doc_path, "backfill", existing_metadata=metadata, dry_run=True)
        self.assertEqual(result.processed, 1)
        self.assertIn(doc_path, result.outputs)


if __name__ == "__main__":
    unittest.main()
