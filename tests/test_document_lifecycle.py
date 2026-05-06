import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from papertrail.engine import DocumentEngine
from papertrail.models import DocumentMetadata, DocumentMetadataRaw
from papertrail.qr.models import QRExtractedMetadata
from papertrail.repository import DocumentRepository

from tests.support import create_millennium_statement, create_pdf, create_png, make_test_runtime


class DocumentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.runtime = make_test_runtime(self.root)
        self.engine = DocumentEngine(self.runtime)
        self.repository = DocumentRepository(self.runtime)
        self.raw = self.runtime.paths.raw[0]
        self.processed = self.runtime.paths.processed

    def tearDown(self):
        self.tmpdir.cleanup()

    def _metadata(self, **overrides) -> DocumentMetadata:
        data = {
            "class_confidence": 0.87,
            "class_reasoning": "classified in test",
            "date_issued": "2026-01-02",
            "document_type": "invoice",
            "issuing_party": "vendor",
            "total_amount": 120.0,
            "total_amount_currency": "EUR",
            "hash_content": "feedface",
            "document_type_raw": "Invoice",
            "document_title": "Cloud API",
            "issuing_party_raw": "Vendor, Inc.",
            "issuer_tax_number": None,
            "locale": "en-US",
            "qrcode": None,
            "sub_documents": None,
        }
        data.update(overrides)
        return DocumentMetadata(**data)

    def test_document_metadata_normalizes_blank_values_to_unknown(self):
        metadata = DocumentMetadata(
            class_confidence=0.1,
            class_reasoning="test",
            date_issued="",
            document_type="",
            issuing_party="",
            hash_content="abc12345",
        )
        self.assertEqual(metadata.date_issued, "$UNKNOWN$")
        self.assertEqual(metadata.document_type, "$UNKNOWN$")
        self.assertEqual(metadata.issuing_party, "$UNKNOWN$")

    def test_pdf_ingest_preserves_raw_fields_and_uses_hash_file_in_filename(self):
        pdf_path = self.raw / "invoice.pdf"
        create_pdf(pdf_path, ["Invoice body"])
        self.engine.classify_pdf_document = lambda *_args, **_kwargs: self._metadata(hash_content="content7777")

        result = self.engine.upsert(pdf_path, "ingest", processed_path=self.processed)

        self.assertEqual(result.processed, 1)
        saved_path = result.outputs[0]
        saved_metadata = self.repository.load_metadata(saved_path.with_suffix(".json"), validate=True)
        self.assertIn(saved_metadata.hash_file, saved_path.name)
        self.assertNotIn(saved_metadata.hash_content, saved_path.name)
        self.assertEqual(saved_metadata.document_type_raw, "Invoice")
        self.assertEqual(saved_metadata.issuing_party_raw, "Vendor, Inc.")

    def test_image_ingest_converts_to_pdf(self):
        image_path = self.raw / "receipt.png"
        create_png(image_path)
        self.engine.classify_pdf_document = lambda *_args, **_kwargs: self._metadata(
            document_type="receipt",
            document_type_raw="Receipt",
            document_title=None,
            issuing_party="merchant",
            issuing_party_raw="Merchant LLC",
            total_amount=42.0,
        )

        result = self.engine.upsert(image_path, "ingest", processed_path=self.processed)

        self.assertEqual(result.processed, 1)
        self.assertEqual(result.images_converted, 1)
        self.assertEqual(result.outputs[0].suffix.lower(), ".pdf")
        self.assertTrue(result.outputs[0].with_suffix(".json").exists())

    def test_bundle_pdf_is_split_into_multiple_documents(self):
        pdf_path = self.raw / "bundle.pdf"
        create_pdf(pdf_path, ["Pág. 1/1\nfirst", "Pág. 1/1\nsecond"])

        def fake_classify(source_path, content_hash, **_kwargs):
            return self._metadata(
                hash_content=content_hash,
                document_title=source_path.stem,
            )

        self.engine.classify_pdf_document = fake_classify

        result = self.engine.upsert(pdf_path, "ingest", processed_path=self.processed)

        self.assertEqual(result.processed, 2)
        self.assertEqual(result.bundles_split, 1)
        self.assertEqual(result.split_pages, 2)
        self.assertEqual(len(result.outputs), 2)

    def test_xlsx_ingest_uses_xlsx_extension_in_filename(self):
        xlsx_path = self.raw / "statement.xlsx"
        create_millennium_statement(xlsx_path)

        result = self.engine.upsert(xlsx_path, "ingest", processed_path=self.processed)

        self.assertEqual(result.processed, 1)
        self.assertEqual(result.outputs[0].suffix.lower(), ".xlsx")
        saved_metadata = self.repository.load_metadata(result.outputs[0].with_suffix(".json"), validate=True)
        self.assertEqual(saved_metadata.source_extension, ".xlsx")
        self.assertIn(saved_metadata.hash_file, result.outputs[0].name)

    def test_qr_metadata_overrides_llm_values(self):
        pdf_path = self.raw / "qr-invoice.pdf"
        create_pdf(pdf_path, ["QR invoice"])
        raw_metadata = DocumentMetadataRaw(
            issue_date="2026-02-03",
            document_type="bank-note",
            document_type_raw="Bank Note",
            issuing_party="llm-party",
            issuing_party_raw="LLM Party",
            total_amount=10.0,
            total_amount_currency="USD",
            confidence=0.4,
            reasoning="llm guess",
            issuer_tax_number="TESTUNKNOWN",
            locale="en-US",
        )
        qr_metadata = QRExtractedMetadata(
            issue_date="2026-01-15",
            document_type="invoice",
            total_amount=12.34,
            total_amount_currency="EUR",
            issuer_tax_number="TESTOWNER",
            locale="pt-PT",
        )

        with patch("papertrail.engine._phase0_qr_extract", return_value=(qr_metadata, {"qr_type": "portuguese_invoice"}, [])):
            with patch("papertrail.engine._phase1_llm_extract", return_value=raw_metadata):
                metadata = self.engine.classify_pdf_document(pdf_path, "content1234")

        self.assertEqual(metadata.date_issued, "2026-01-15")
        self.assertEqual(metadata.document_type, "invoice")
        self.assertEqual(metadata.total_amount, 12.34)
        self.assertEqual(metadata.total_amount_currency, "EUR")
        self.assertEqual(metadata.issuer_tax_number, "TESTOWNER")
        self.assertEqual(metadata.qrcode, {"qr_type": "portuguese_invoice"})
        self.assertEqual(metadata.class_confidence, 1.0)

    def test_multi_qr_documents_are_saved_as_sub_documents(self):
        pdf_path = self.raw / "multi-qr.pdf"
        create_pdf(pdf_path, ["Aggregator"])
        raw_metadata = DocumentMetadataRaw(
            issue_date="2026-02-03",
            document_type="statement",
            document_type_raw="Statement",
            issuing_party="aggregator",
            issuing_party_raw="Aggregator SA",
            total_amount=None,
            total_amount_currency=None,
            confidence=0.8,
            reasoning="wrapper doc",
            issuer_tax_number=None,
            locale="pt-PT",
        )
        qr_results = [
            (
                QRExtractedMetadata(
                    issue_date="2026-01-10",
                    document_type="receipt",
                    total_amount=7.5,
                    total_amount_currency="EUR",
                    issuer_tax_number="TESTQRONE",
                    locale="pt-PT",
                ),
                {"raw_content": "A:TESTQRONE", "page_number": 0},
            ),
            (
                QRExtractedMetadata(
                    issue_date="2026-01-12",
                    document_type="receipt",
                    total_amount=8.5,
                    total_amount_currency="EUR",
                    issuer_tax_number="TESTQRTWO",
                    locale="pt-PT",
                ),
                {"raw_content": "A:TESTQRTWO", "page_number": 0},
            ),
        ]

        with patch("papertrail.engine._phase0_qr_extract", return_value=(None, None, qr_results)):
            with patch("papertrail.engine._phase1_llm_extract", return_value=raw_metadata):
                metadata = self.engine.classify_pdf_document(pdf_path, "content9999")

        self.assertIsNone(metadata.qrcode)
        self.assertEqual(len(metadata.sub_documents), 2)
        self.assertEqual(metadata.sub_documents[0]["document_type"], "receipt")

    def test_bank_movement_raw_type_is_normalized_to_bank_note(self):
        pdf_path = self.raw / "movement.pdf"
        create_pdf(pdf_path, ["Shared Toll movement"])
        raw_metadata = DocumentMetadataRaw(
            issue_date="2026-02-02",
            document_type="receipt",
            document_type_raw="Movimento",
            issuing_party="via verde",
            issuing_party_raw="SHAREDTOLL",
            total_amount=2.79,
            total_amount_currency="EUR",
            confidence=0.7,
            reasoning="movement screenshot",
            issuer_tax_number=None,
            locale="pt-PT",
        )

        with patch("papertrail.engine._phase0_qr_extract", return_value=(None, None, [])):
            with patch("papertrail.engine._phase1_llm_extract", return_value=raw_metadata):
                metadata = self.engine.classify_pdf_document(pdf_path, "contentmov1")

        self.assertEqual(metadata.document_type, "bank-note")
        self.assertEqual(metadata.document_type_raw, "Movimento")

    def test_bank_note_does_not_promote_known_counterparty_title_to_issuer(self):
        self.engine.repository.save_json(
            self.processed / "known-shared-toll.json",
            self._metadata(issuing_party="shared-toll", issuing_party_raw="Shared Toll").model_dump(),
        )
        pdf_path = self.raw / "movement.pdf"
        create_pdf(pdf_path, ["Shared Toll movement"])
        raw_metadata = DocumentMetadataRaw(
            issue_date="2026-04-22",
            document_type="bank-note",
            document_type_raw="Movimento",
            document_title="Shared Toll",
            issuing_party="$UNKNOWN$",
            issuing_party_raw="$UNKNOWN$",
            total_amount=None,
            total_amount_currency=None,
            confidence=0.7,
            reasoning="counterparty placed in title",
            issuer_tax_number=None,
            locale="pt-PT",
        )

        with patch("papertrail.engine._phase0_qr_extract", return_value=(None, None, [])):
            with patch("papertrail.engine._phase1_llm_extract", return_value=raw_metadata):
                metadata = self.engine.classify_pdf_document(pdf_path, "contentmov2")

        self.assertEqual(metadata.issuing_party, "$UNKNOWN$")
        self.assertEqual(metadata.document_title, "Shared Toll")

    def test_bank_note_does_not_recover_unknown_issuer_from_generic_title(self):
        pdf_path = self.raw / "movement.pdf"
        create_pdf(pdf_path, ["Transferencia pontual a debito"])
        raw_metadata = DocumentMetadataRaw(
            issue_date="2026-04-22",
            document_type="bank-note",
            document_type_raw="Movimento",
            document_title="Transferencia pontual a debito",
            issuing_party="$UNKNOWN$",
            issuing_party_raw="$UNKNOWN$",
            total_amount=None,
            total_amount_currency=None,
            confidence=0.7,
            reasoning="generic transaction title",
            issuer_tax_number=None,
            locale="pt-PT",
        )

        with patch("papertrail.engine._phase0_qr_extract", return_value=(None, None, [])):
            with patch("papertrail.engine._phase1_llm_extract", return_value=raw_metadata):
                metadata = self.engine.classify_pdf_document(pdf_path, "contentmov3")

        self.assertEqual(metadata.issuing_party, "$UNKNOWN$")


if __name__ == "__main__":
    unittest.main()
