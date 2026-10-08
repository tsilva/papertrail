import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from papertrail.config import (
    DEFAULT_AGENTBRIDGE_API_KEY,
    DEFAULT_AGENTBRIDGE_BASE_URL,
    DEFAULT_AGENTBRIDGE_MODEL_ID,
    ProfileSettings,
    build_openai_client,
)
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


class AgentBridgeDefaultTests(unittest.TestCase):
    def test_typed_profile_defaults_to_agentbridge_codex(self):
        profile = ProfileSettings.model_validate({"profile": {"name": "test"}})

        self.assertEqual(profile.openrouter.base_url, DEFAULT_AGENTBRIDGE_BASE_URL)
        self.assertEqual(profile.openrouter.model_id, DEFAULT_AGENTBRIDGE_MODEL_ID)
        self.assertEqual(profile.openrouter.api_key, DEFAULT_AGENTBRIDGE_API_KEY)

    def test_example_profile_uses_typed_agentbridge_defaults(self):
        example_path = Path(__file__).parents[1] / "profile.yaml.example"
        example = yaml.safe_load(example_path.read_text(encoding="utf-8"))

        self.assertEqual(example["openrouter"]["base_url"], DEFAULT_AGENTBRIDGE_BASE_URL)
        self.assertEqual(example["openrouter"]["model_id"], DEFAULT_AGENTBRIDGE_MODEL_ID)
        self.assertEqual(example["openrouter"]["api_key"], DEFAULT_AGENTBRIDGE_API_KEY)

    def test_default_client_uses_agentbridge_placeholder_key(self):
        profile = ProfileSettings.model_validate({"profile": {"name": "test"}})

        with patch("papertrail.config.openai.OpenAI") as client_factory:
            client = build_openai_client(profile)

        self.assertIs(client, client_factory.return_value)
        client_factory.assert_called_once_with(
            api_key=DEFAULT_AGENTBRIDGE_API_KEY,
            base_url=DEFAULT_AGENTBRIDGE_BASE_URL,
        )


if __name__ == "__main__":
    unittest.main()
