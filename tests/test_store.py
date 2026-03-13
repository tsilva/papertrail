import tempfile
import unittest
from pathlib import Path

from papertrail.app import App, AppPaths
from papertrail.config import Config
from papertrail.console import PapertrailConsole
from papertrail.hashing import HashCache
from papertrail.models import DocumentMetadata
from papertrail.store import DocumentStore


class DocumentStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.processed = root / "processed"
        self.export = root / "export"
        self.cache = root / "cache"
        self.processed.mkdir()
        self.export.mkdir()
        self.cache.mkdir()
        profile = Config(
            {
                "profile": {"name": "test", "description": ""},
                "paths": {
                    "raw": [],
                    "processed": str(self.processed),
                    "export": str(self.export),
                },
                "openrouter": {"model_id": "test-model"},
                "nif_api": {"enabled": False},
            }
        )
        self.app = App(
            profile=profile,
            profile_name="test",
            paths=AppPaths(
                raw=[],
                processed=self.processed,
                export=self.export,
                cache=self.cache,
                profiles=root,
            ),
            model_id="test-model",
            openai_client=None,
            nif_cache=None,
            hash_cache=HashCache(self.cache / "hash_cache.yaml"),
            console=PapertrailConsole(),
            api_accessible=False,
        )
        self.store = DocumentStore(self.app)

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
            self.store.find_companion(json_path, {"source_extension": ".xlsx"}),
            xlsx_path,
        )

    def test_build_indexes_collects_hashes_and_issuers(self):
        doc_path = self.processed / "doc.pdf"
        doc_path.write_text("pdf")
        self.store.save_document(doc_path, self._metadata())
        content_idx, file_idx, text_idx, issuers = self.store.build_indexes(self.processed)
        self.assertIn("abc12345", content_idx)
        self.assertIn("deadbeef", file_idx)
        self.assertIn("vendor", issuers)
        self.assertEqual(text_idx, {})

    def test_unique_dates_returns_sorted_months(self):
        jan_doc = self.processed / "jan.pdf"
        feb_doc = self.processed / "feb.pdf"
        jan_doc.write_text("pdf")
        feb_doc.write_text("pdf")
        jan_meta = self._metadata(hash_content="jan12345", hash_file="jan12345")
        feb_meta = self._metadata(hash_content="feb12345", hash_file="feb12345")
        feb_meta.date_issued = "2026-02-15"
        self.store.save_document(jan_doc, jan_meta)
        self.store.save_document(feb_doc, feb_meta)
        self.assertEqual(self.store.unique_dates(self.processed), ["2026-02", "2026-01"])

    def test_repair_filenames_renames_companion_and_sidecar(self):
        old_doc = self.processed / "old-name.pdf"
        old_doc.write_text("pdf")
        metadata = self._metadata(hash_content="facefeed", hash_file="deadbeef")
        self.store.save_document(old_doc, metadata)
        stats = self.store.repair_filenames(self.processed)
        self.assertEqual(stats["validated"], 1)
        self.assertEqual(stats["renamed"], 1)
        renamed = list(self.processed.glob("*.pdf"))
        self.assertEqual(len(renamed), 1)
        self.assertIn("deadbeef", renamed[0].name)
        self.assertTrue(renamed[0].with_suffix(".json").exists())


if __name__ == "__main__":
    unittest.main()
