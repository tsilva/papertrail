import tempfile
import unittest
from pathlib import Path

from papertrail.app import App, AppPaths
from papertrail.config import Config
from papertrail.console import PapertrailConsole
from papertrail.hashing import HashCache
from papertrail.tasks.extraction import DocumentService


class DocumentServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        processed = root / "processed"
        export = root / "export"
        cache = root / "cache"
        processed.mkdir()
        export.mkdir()
        cache.mkdir()
        profile = Config(
            {
                "profile": {"name": "test", "description": ""},
                "paths": {
                    "raw": [],
                    "processed": str(processed),
                    "export": str(export),
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
                processed=processed,
                export=export,
                cache=cache,
                profiles=root,
            ),
            model_id="test-model",
            openai_client=None,
            nif_cache=None,
            hash_cache=HashCache(cache / "hash_cache.yaml"),
            console=PapertrailConsole(),
            api_accessible=False,
        )
        self.service = DocumentService(self.app)
        self.processed = processed

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_backfill_adds_derived_fields_without_llm(self):
        doc_path = self.processed / "statement.xlsx"
        doc_path.write_text("xlsx")
        metadata = {"date_issued": "2026-01-01", "document_type": "bank-statement", "issuing_party": "bank"}
        result = self.service.upsert_document(doc_path, "backfill", existing_metadata=metadata, dry_run=True)
        self.assertEqual(result.processed, 1)
        self.assertIn(doc_path, result.outputs)


if __name__ == "__main__":
    unittest.main()
