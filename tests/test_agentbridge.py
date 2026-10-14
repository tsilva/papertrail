"""Verify each model boundary routes provider IDs through AgentBridge."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from papertrail.agentbridge import base_url
from papertrail.commands.reconcile import _phase2_llm_match
from papertrail.config import ProfileSettings
from papertrail.engine import _phase1_llm_extract


def test_gateway_environment_overrides_profile_endpoint():
    with patch.dict("os.environ", {"AGENTBRIDGE_BASE_URL": "http://127.0.0.1:8089/api/v1"}):
        assert base_url("http://127.0.0.1:8082/api/v1") == "http://127.0.0.1:8089/api/v1"


def make_runtime(client):
    return SimpleNamespace(
        profile=ProfileSettings.model_validate({"profile": {"name": "synthetic"}}),
        model_id="google/synthetic-model",
        openai_client=client,
    )


def test_classification_prefixes_provider_model_only_at_request():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                arguments=json.dumps(
                                    {
                                        "issue_date": "2026-10-12",
                                        "document_type": "invoice",
                                        "document_type_raw": "Invoice",
                                        "issuing_party": "synthetic-party",
                                        "issuing_party_raw": "Synthetic Party",
                                    }
                                )
                            ),
                        )
                    ]
                )
            )
        ],
    )
    runtime = make_runtime(client)
    repository = MagicMock()
    repository.registry.document_types.return_value = []
    repository.registry.issuing_parties.return_value = []
    with patch("papertrail.engine.render_pdf_to_images", return_value=[]):
        _phase1_llm_extract(Path("synthetic.pdf"), runtime, repository, ".", set(), {}, None)
    assert (
        client.chat.completions.create.call_args.kwargs["model"]
        == "openrouter/google/synthetic-model"
    )
    assert runtime.model_id == "google/synthetic-model"


def test_reconciliation_prefixes_provider_model_only_at_request():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"matches": []}'))],
    )
    runtime = make_runtime(client)
    transaction = SimpleNamespace(
        date_posting="2026-10-12",
        date_value=None,
        amount=-184.5,
        currency="EUR",
        description="SYNTHETIC PARTY",
    )
    with (
        patch(
            "papertrail.commands.reconcile._format_candidate_for_llm", return_value="[A] Invoice"
        ),
        patch("papertrail.commands.reconcile._date_window_days", return_value=7),
    ):
        assert _phase2_llm_match(runtime, [transaction], [SimpleNamespace()]) == []
    assert (
        client.chat.completions.create.call_args.kwargs["model"]
        == "openrouter/google/synthetic-model"
    )
    assert runtime.model_id == "google/synthetic-model"
