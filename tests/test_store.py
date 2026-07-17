import tempfile
import unittest
from pathlib import Path

from papertrail.models import DocumentMetadata
from papertrail.repository import DocumentRepository, document_sidecar_paths
from tests.support import make_test_runtime


class DocumentRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.runtime = make_test_runtime(root)
        self.repository = DocumentRepository(self.runtime)
        self.processed = self.runtime.paths.processed

    def tearDown(self):
        self.tmpdir.cleanup()

    def _metadata(self, hash_content="abc12345", hash_file="deadbeef"):
        return DocumentMetadata(
            class_confidence=1.0,
            class_reasoning="test",
            date_created="2026-01-01",
            date_issued="2026-01-01",
            date_updated="2026-01-01",
            document_type="invoice",
            issuing_party="vendor",
            hash_content=hash_content,
            hash_file=hash_file,
            document_type_raw="Invoice",
            issuing_party_raw="Vendor, Inc.",
        )

    def test_find_companion_respects_source_extension(self):
        json_path = self.processed / "sample.json"
        xlsx_path = self.processed / "sample.xlsx"
        json_path.write_text("{}")
        xlsx_path.write_text("xlsx")
        self.assertEqual(
            self.repository.find_companion(json_path, {"source_extension": ".xlsx"}),
            xlsx_path,
        )

    def test_build_indexes_collects_hashes_and_issuers(self):
        doc_path = self.processed / "doc.pdf"
        doc_path.write_text("pdf")
        self.repository.save_document(doc_path, self._metadata())
        content_idx, file_idx, text_idx, issuers = self.repository.build_indexes(self.processed)
        self.assertIn("abc12345", content_idx)
        self.assertIn("deadbeef", file_idx)
        self.assertIn("vendor", issuers)
        self.assertEqual(text_idx, {})

    def test_document_sidecar_paths_excludes_repository_internal_state(self):
        visible = self.processed / "visible.json"
        visible.write_text("{}")
        (self.processed / "statement.reconciliation.json").write_text("{}")
        (self.processed / "statement.reconciliation.groundtruth.json").write_text("{}")
        logs = self.processed / "logs"
        logs.mkdir()
        (logs / "failure.json").write_text("{}")
        dupes = self.processed / "_dupes"
        dupes.mkdir()
        (dupes / "duplicate.json").write_text("{}")

        self.assertEqual(document_sidecar_paths(self.processed), [visible])

    def test_unique_dates_returns_sorted_months(self):
        jan_doc = self.processed / "jan.pdf"
        feb_doc = self.processed / "feb.pdf"
        jan_doc.write_text("pdf")
        feb_doc.write_text("pdf")
        jan_meta = self._metadata(hash_content="jan12345", hash_file="jan12345")
        feb_meta = self._metadata(hash_content="feb12345", hash_file="feb12345")
        feb_meta.date_issued = "2026-02-15"
        self.repository.save_document(jan_doc, jan_meta)
        self.repository.save_document(feb_doc, feb_meta)
        self.assertEqual(self.repository.unique_dates(self.processed), ["2026-02", "2026-01"])

    def test_repair_filenames_renames_companion_and_sidecar(self):
        old_doc = self.processed / "old-name.pdf"
        old_doc.write_text("pdf")
        metadata = self._metadata(hash_content="facefeed", hash_file="deadbeef")
        self.repository.save_document(old_doc, metadata)
        stats = self.repository.repair_filenames(self.processed)
        self.assertEqual(stats["validated"], 1)
        self.assertEqual(stats["renamed"], 1)
        renamed = list(self.processed.glob("*.pdf"))
        self.assertEqual(len(renamed), 1)
        self.assertIn("deadbeef", renamed[0].name)
        self.assertTrue(renamed[0].with_suffix(".json").exists())


if __name__ == "__main__":
    unittest.main()
