import unittest
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


def _stub_module(name: str, **attrs):
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_stub_module("google")
_stub_module("google.auth")
_stub_module("google.auth.transport")
_stub_module("google.auth.transport.requests", Request=MagicMock())
_stub_module("google.oauth2")
_stub_module("google.oauth2.credentials", Credentials=MagicMock())
_stub_module("google_auth_oauthlib")
_stub_module("google_auth_oauthlib.flow", InstalledAppFlow=MagicMock())
_stub_module("googleapiclient")
_stub_module("googleapiclient.discovery", build=MagicMock())
_stub_module("googleapiclient.errors", HttpError=Exception)
_stub_module("mbox_extractor", extract_mbox=MagicMock())

import main as cli_main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_bare_command_skips_default_pipeline_when_api_is_unavailable(self):
        runtime = SimpleNamespace(api_accessible=False, console=MagicMock())

        with (
            patch("main.create_runtime", return_value=runtime),
            patch("main.pipeline_cmd") as pipeline_mock,
        ):
            result = self.runner.invoke(cli_main.app, [])

        self.assertEqual(result.exit_code, 0)
        pipeline_mock.assert_not_called()
        runtime.console.warning.assert_called_once_with(
            "Skipping default pipeline because the LLM API is unavailable.",
            indent=False,
        )
        runtime.console.detail.assert_called_once_with(
            "Run an offline subcommand explicitly, or retry once the API base URL is reachable.",
            indent=False,
        )
        self.assertIn("Usage:", result.stdout)

    def test_bare_command_runs_default_pipeline_when_api_is_available(self):
        runtime = SimpleNamespace(api_accessible=True, console=MagicMock())

        with (
            patch("main.create_runtime", return_value=runtime),
            patch("main.pipeline_cmd") as pipeline_mock,
        ):
            result = self.runner.invoke(cli_main.app, [])

        self.assertEqual(result.exit_code, 0)
        pipeline_mock.assert_called_once()

    def test_review_command_dispatches_to_command_layer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir)
            runtime = SimpleNamespace(
                profile=SimpleNamespace(
                    paths=SimpleNamespace(
                        raw=[str(export_dir)],
                        processed=str(export_dir),
                        export=str(export_dir),
                    )
                ),
                console=MagicMock(),
            )

            with (
                patch("main.create_runtime", return_value=runtime),
                patch("main.commands.review") as review_mock,
            ):
                result = self.runner.invoke(cli_main.app, ["review"])

        self.assertEqual(result.exit_code, 0)
        review_mock.assert_called_once_with(runtime, export_dir)


if __name__ == "__main__":
    unittest.main()
