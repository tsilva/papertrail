import importlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from papertrail.archive_extract import extract_archives
from papertrail.commands import pipeline
from papertrail.config import ConfigError, GmailSettings, get_gmail_config_paths
from papertrail.gmail import GmailDownloader
from papertrail.hashing import hash_file_fast
from papertrail.models import DocumentMetadata
from papertrail.pdf import get_page_count
from papertrail.pdf_merge import merge_all_pdfs
from papertrail.repository import DocumentRepository
from papertrail.runtime import runtime_from_profile
from tests.support import create_millennium_statement, create_pdf, make_test_runtime
from tools import browse, review, shared


class AdapterSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.runtime = make_test_runtime(self.root)
        self.repository = DocumentRepository(self.runtime)

    def tearDown(self):
        shared._load_profile.cache_clear()
        self.tmpdir.cleanup()

    def _metadata(self, hash_content: str, hash_file: str, **overrides) -> DocumentMetadata:
        data = {
            "class_confidence": 1.0,
            "class_reasoning": "test",
            "date_created": "2026-01-02",
            "date_issued": "2026-01-02",
            "date_updated": "2026-01-02",
            "document_type": "invoice",
            "document_type_raw": "Invoice",
            "document_title": "Subscription",
            "issuing_party": "vendor",
            "issuing_party_raw": "Vendor, Inc.",
            "total_amount": 12.34,
            "total_amount_currency": "EUR",
            "hash_content": hash_content,
            "hash_file": hash_file,
        }
        data.update(overrides)
        return DocumentMetadata(**data)

    def test_extract_archives_unpacks_zip_once(self):
        raw_dir = self.runtime.paths.raw[0]
        archive_path = raw_dir / "docs.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("invoice.txt", "hello")
            archive.writestr("nested/receipt.txt", "world")

        first = extract_archives(raw_dir)
        second = extract_archives(raw_dir)

        output_dir = raw_dir / "docs_archive"
        self.assertEqual(first[str(archive_path)], 2)
        self.assertEqual(second[str(archive_path)], 0)
        self.assertTrue((output_dir / "invoice.txt").exists())
        self.assertTrue((output_dir / "nested" / "receipt.txt").exists())

    def test_extract_archives_uses_available_7zip_variant(self):
        raw_dir = self.runtime.paths.raw[0]
        archive_path = raw_dir / "docs.7z"
        archive_path.write_bytes(b"placeholder")

        with patch("papertrail.archive_extract.unarch_extract_archives", return_value={str(archive_path): 1}) as mock:
            results = extract_archives(raw_dir)

        mock.assert_called_once_with(
            str(raw_dir),
            output_dir=str(raw_dir),
            passwords=None,
            show_progress=False,
            output_suffix="_archive",
            skip_existing=True,
        )
        self.assertEqual(results[str(archive_path)], 1)

    def test_merge_all_pdfs_creates_all_and_prefix_outputs(self):
        export_dir = self.runtime.paths.export
        create_pdf(export_dir / "cmp_one.pdf", ["page 1"])
        create_pdf(export_dir / "cmp_two.pdf", ["page 2", "page 3"])
        create_pdf(export_dir / "div_misc.pdf", ["page 4"])

        outputs = merge_all_pdfs(export_dir)

        self.assertEqual(set(outputs), {"all", "cmp", "div"})
        self.assertEqual(get_page_count(export_dir / "merged_all.pdf"), 4)
        self.assertEqual(get_page_count(export_dir / "merged_cmp.pdf"), 3)
        self.assertEqual(get_page_count(export_dir / "merged_div.pdf"), 1)

    def test_pipeline_composes_commands_through_single_layer(self):
        raw_dir = self.runtime.paths.raw[0]
        processed_path = self.runtime.paths.processed
        export_dir = self.runtime.paths.export

        with (
            patch("papertrail.commands.extract_mbox_attachments", return_value={"mbox_files": 0, "attachments_extracted": 0, "errors": []}) as mbox_mock,
            patch("papertrail.commands.extract_archives", return_value={}) as archive_mock,
            patch("papertrail.commands.extract", return_value={"new": 0, "duplicates": 0, "failed": 0, "batch_duplicates": 0}) as extract_mock,
            patch("papertrail.commands.sync", return_value={"targets": 0, "new": 0, "changed": 0}) as sync_mock,
            patch("papertrail.commands.rename", return_value={"validated": 0, "renamed": 0, "orphans": 0}) as rename_mock,
            patch("papertrail.commands.export_excel", return_value={"exported": 0}) as export_excel_mock,
            patch("papertrail.commands.copy_matching", return_value={"copied": 0, "deduped": 0}) as copy_matching_mock,
            patch("papertrail.commands.discover_bank_statements", return_value=[]) as discover_mock,
            patch("papertrail.commands.merge_all_pdfs", return_value={}) as merge_mock,
            patch("papertrail.commands.validate_merged_pdf", return_value=True) as validate_mock,
        ):
            pipeline(self.runtime, months=1, export_date_arg="2026-01")

        mbox_mock.assert_called_once_with(str(raw_dir))
        archive_mock.assert_called_once_with(str(raw_dir), passwords=None)
        extract_mock.assert_called_once_with(self.runtime, processed_path, [raw_dir], quiet=False)
        sync_mock.assert_called_once_with(self.runtime, processed_path, quiet=False)
        rename_mock.assert_called_once_with(self.runtime, processed_path, quiet=True)
        export_excel_mock.assert_called_once_with(
            self.runtime,
            processed_path,
            str(processed_path / "processed_files.xlsx"),
            quiet=True,
        )
        copy_matching_mock.assert_called_once()
        discover_mock.assert_called_once_with(unittest.mock.ANY, export_dir / "2026-01")
        merge_mock.assert_not_called()
        validate_mock.assert_not_called()

    def test_gradio_loaders_use_repository_helpers_without_network(self):
        processed_pdf = self.runtime.paths.processed / "invoice.pdf"
        create_pdf(processed_pdf, ["invoice"])
        self.repository.save_document(processed_pdf, self._metadata("hash1111", "file1111"))

        export_statement = self.runtime.paths.export / "statement.xlsx"
        create_millennium_statement(export_statement)
        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(export_statement)
        self.repository.save_document(
            export_statement,
            classify_bank_statement(export_statement, statement_hash),
        )
        recon_path = export_statement.with_suffix(".reconciliation.json")
        recon_path.write_text(
            json.dumps(
                {
                    "source": export_statement.name,
                    "generated": "2026-01-31T12:00:00Z",
                    "summary": {
                        "total": 1,
                        "reconciled": 0,
                        "incomplete": 0,
                        "unmatched": 1,
                        "unmatched_files": 0,
                        "reconciliation_rate": 0,
                    },
                    "matches": [],
                    "unmatched": [
                        {
                            "row": 9,
                            "date": "2026-01-01",
                            "description": "TEST PURCHASE",
                            "amount": -12.34,
                            "currency": "EUR",
                            "transaction_category": "default-debit",
                        }
                    ],
                    "unmatched_files": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        browse_entries = browse._load_entries(str(self.runtime.paths.processed))
        review_data, status = review.load_export_folder(str(self.runtime.paths.export))

        self.assertEqual(len(browse_entries), 1)
        self.assertEqual(browse_entries[0]["metadata"]["hash_file"], "file1111")
        self.assertEqual(len(review_data["bank_statements"]), 1)
        self.assertIn("statement.xlsx", review_data["file_index"])
        self.assertIn("Loaded **1** bank statements", status)

    def test_review_marks_long_pdf_filenames_inline(self):
        export_dir = self.runtime.paths.export
        long_file = export_dir / (
            "CMP_2026-01-02 - invoice-receipt - millenniumbcp - "
            "man cta pacote m empresa - abcdef12.pdf"
        )
        html = review.render_single_bank_html(
            {
                "doc_path": str(export_dir / "statement.xlsx"),
                "metadata": {"bank_statement": {}},
                "reconciliation": {
                    "summary": {"total": 1, "reconciled": 1, "reconciliation_rate": 100},
                    "matches": [
                        {
                            "row": 1,
                            "date": "2026-01-02",
                            "description": "TEST",
                            "amount": -1,
                            "currency": "EUR",
                            "transaction_category": "default-debit",
                            "method": "exact",
                            "confidence": 1,
                            "files": [long_file.name],
                        }
                    ],
                    "unmatched": [],
                },
            },
            data={},
        )

        self.assertIn("filename-warning", html)
        self.assertIn(long_file.name, html)
        self.assertIn("&#9888;", html)

    def test_review_does_not_warn_about_long_non_pdf_filenames(self):
        export_dir = self.runtime.paths.export
        long_json = export_dir / (
            "BNC_2026-01-01 - bank-statement - millenniumbcp - "
            "TEST-ACCOUNT-ALPHA - abcdef12.reconciliation.groundtruth.json"
        )
        html = review.render_single_bank_html(
            {
                "doc_path": str(export_dir / "statement.xlsx"),
                "metadata": {"bank_statement": {}},
                "reconciliation": {
                    "summary": {"total": 1, "reconciled": 1, "reconciliation_rate": 100},
                    "matches": [
                        {
                            "row": 1,
                            "date": "2026-01-02",
                            "description": "TEST",
                            "amount": -1,
                            "currency": "EUR",
                            "transaction_category": "default-debit",
                            "method": "exact",
                            "confidence": 1,
                            "files": [long_json.name],
                        }
                    ],
                    "unmatched": [],
                },
            },
            data={},
        )

        self.assertNotIn("filename-warning", html)
        self.assertIn(long_json.name, html)

    def test_review_skips_non_metadata_json_payloads(self):
        export_dir = self.runtime.paths.export
        (export_dir / "list-payload.json").write_text('["not", "metadata"]\n', encoding="utf-8")
        create_millennium_statement(export_dir / "statement.xlsx")

        from papertrail.bank_statement import classify_bank_statement

        statement_path = export_dir / "statement.xlsx"
        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        with patch("tools.shared._load_profile", return_value=None):
            review_data, status = review.load_export_folder(str(export_dir))

        self.assertEqual(len(review_data["bank_statements"]), 1)
        self.assertIn("statement.xlsx", review_data["file_index"])
        self.assertIn("Loaded **1** bank statements", status)

    def test_tools_shared_uses_active_profile_env(self):
        shared._load_profile.cache_clear()

        with (
            patch.dict(os.environ, {"PAPERTRAIL_PROFILE": "work"}, clear=False),
            patch("tools.shared.load_profile", return_value=self.runtime.profile) as load_profile_mock,
        ):
            export_dir = shared.get_export_dir()

        self.assertEqual(export_dir, str(self.runtime.paths.export))
        load_profile_mock.assert_called_once_with("work")

    def test_tools_shared_reads_typed_profile_settings_with_env_overrides(self):
        self.runtime.profile.tools.preview_dpi = 144
        with (
            patch("tools.shared._load_profile", return_value=self.runtime.profile),
            patch.dict(
                os.environ,
                {
                    "PAPERTRAIL_PREVIEW_DPI": "invalid",
                    "PAPERTRAIL_LLM_HIGH_CONFIDENCE_THRESHOLD": "0.9",
                    "PAPERTRAIL_DEFAULT_CURRENCY": "USD",
                },
                clear=False,
            ),
        ):
            self.assertEqual(shared.profile_setting("tools", "preview_dpi", 150), 144)
            self.assertEqual(
                shared.profile_setting("tools", "llm_high_confidence_threshold", 0.8),
                0.9,
            )
            self.assertEqual(
                shared.profile_setting("reconciliation", "default_currency", "EUR"),
                "USD",
            )

    def test_gmail_uses_typed_settings_and_credential_paths_only(self):
        downloader = GmailDownloader(
            credentials_path=self.root / "credentials.json",
            token_path=self.root / "token.json",
            output_dir=self.root / "gmail",
            settings={"api_page_size": 75},
        )

        self.assertIsInstance(downloader.settings, GmailSettings)
        self.assertEqual(downloader.settings.api_page_size, 75)
        self.assertEqual(downloader.settings.api_service, "gmail")

        with patch.dict(os.environ, {"PAPERTRAIL_HOME": str(self.root)}, clear=False):
            paths = get_gmail_config_paths()
        self.assertEqual(set(paths), {"credentials", "token"})

    def test_runtime_hard_fails_when_required_dependency_is_missing(self):
        real_import_module = importlib.import_module

        def fake_import_module(name, package=None):
            if name == "pikepdf":
                raise ModuleNotFoundError("No module named 'pikepdf'")
            return real_import_module(name, package)

        with (
            patch("papertrail.dependencies.import_module", side_effect=fake_import_module),
            patch("papertrail.dependencies.check_pyzbar_available", return_value=(True, "")),
        ):
            with self.assertRaisesRegex(ConfigError, "pikepdf"):
                runtime_from_profile(self.runtime.profile, enable_client=False, probe_api=False)

    def test_submodule_imports_do_not_eager_load_engine(self):
        saved = {
            name: sys.modules[name]
            for name in list(sys.modules)
            if name == "papertrail" or name.startswith("papertrail.")
        }
        try:
            for name in list(saved):
                sys.modules.pop(name, None)
            importlib.import_module("papertrail.models")
            importlib.import_module("papertrail.rules")
            importlib.import_module("papertrail.runtime")
            self.assertNotIn("papertrail.engine", sys.modules)
            papertrail = importlib.import_module("papertrail")
            self.assertIsNotNone(papertrail.Runtime)
            self.assertIn("papertrail.runtime", sys.modules)
            self.assertNotIn("papertrail.engine", sys.modules)
        finally:
            for name in list(sys.modules):
                if name == "papertrail" or name.startswith("papertrail."):
                    sys.modules.pop(name, None)
            sys.modules.update(saved)


if __name__ == "__main__":
    unittest.main()
