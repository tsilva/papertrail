import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
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

import main as cli_main  # noqa: E402


class CliTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_bare_command_skips_default_pipeline_when_api_is_unavailable(self):
        runtime = SimpleNamespace(api_accessible=False, console=MagicMock())

        with (
            patch("main.create_runtime", return_value=runtime),
            patch("main._run_pipeline") as pipeline_mock,
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
        runtime = SimpleNamespace(
            api_accessible=True,
            console=MagicMock(),
            profile=SimpleNamespace(workflow=SimpleNamespace(default_months=2)),
        )

        with (
            patch("main.create_runtime", return_value=runtime),
            patch("main._run_pipeline") as pipeline_mock,
        ):
            result = self.runner.invoke(cli_main.app, [])

        self.assertEqual(result.exit_code, 0)
        pipeline_mock.assert_called_once_with(
            runtime,
            months=2,
            export_date=None,
        )

    def test_pipeline_creates_runtime_once_with_root_options(self):
        runtime = SimpleNamespace(
            profile=SimpleNamespace(workflow=SimpleNamespace(default_months=3))
        )

        with (
            patch("main.create_runtime", return_value=runtime) as create_runtime_mock,
            patch("main.commands.pipeline") as pipeline_mock,
        ):
            result = self.runner.invoke(
                cli_main.app,
                ["--profile", "default", "--verbose", "pipeline"],
            )

        self.assertEqual(result.exit_code, 0)
        create_runtime_mock.assert_called_once_with(
            profile_name="default",
            verbose=True,
            enable_client=True,
            probe_api=True,
        )
        pipeline_mock.assert_called_once_with(
            runtime,
            months=3,
            export_date_arg=None,
        )

    def test_pipeline_creates_runtime_once_with_subcommand_options(self):
        runtime = SimpleNamespace(
            profile=SimpleNamespace(workflow=SimpleNamespace(default_months=3))
        )

        with (
            patch("main.create_runtime", return_value=runtime) as create_runtime_mock,
            patch("main.commands.pipeline"),
        ):
            result = self.runner.invoke(
                cli_main.app,
                ["pipeline", "--profile", "default", "--verbose"],
            )

        self.assertEqual(result.exit_code, 0)
        create_runtime_mock.assert_called_once_with(
            profile_name="default",
            verbose=True,
            enable_client=True,
            probe_api=True,
        )

    def test_review_command_dispatches_to_command_layer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir)
            runtime = SimpleNamespace(
                profile=SimpleNamespace(),
                paths=SimpleNamespace(
                    raw=[export_dir],
                    processed=export_dir,
                    export=export_dir,
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

    def test_review_accepts_profile_after_subcommand(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir)
            runtime = SimpleNamespace(
                profile=SimpleNamespace(),
                paths=SimpleNamespace(
                    raw=[export_dir],
                    processed=export_dir,
                    export=export_dir,
                ),
                console=MagicMock(),
            )

            with (
                patch("main.create_runtime", return_value=runtime) as create_runtime_mock,
                patch("main.commands.review") as review_mock,
            ):
                result = self.runner.invoke(cli_main.app, ["review", "--profile", "default"])

        self.assertEqual(result.exit_code, 0)
        create_runtime_mock.assert_called_once_with(
            profile_name="default",
            verbose=False,
            enable_client=True,
            probe_api=True,
        )
        review_mock.assert_called_once_with(runtime, export_dir)

    def test_review_accepts_profile_before_subcommand(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir)
            runtime = SimpleNamespace(
                profile=SimpleNamespace(),
                paths=SimpleNamespace(
                    raw=[export_dir],
                    processed=export_dir,
                    export=export_dir,
                ),
                console=MagicMock(),
            )

            with (
                patch("main.create_runtime", return_value=runtime) as create_runtime_mock,
                patch("main.commands.review") as review_mock,
            ):
                result = self.runner.invoke(cli_main.app, ["--profile", "default", "review"])

        self.assertEqual(result.exit_code, 0)
        create_runtime_mock.assert_called_once_with(
            profile_name="default",
            verbose=False,
            enable_client=True,
            probe_api=True,
        )
        review_mock.assert_called_once_with(runtime, export_dir)

    def test_regression_command_uses_offline_runtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_root = Path(tmpdir)
            month_dir = export_root / "2026-04"
            month_dir.mkdir()
            runtime = SimpleNamespace(
                profile=SimpleNamespace(),
                paths=SimpleNamespace(
                    raw=[export_root],
                    processed=export_root,
                    export=export_root,
                ),
                console=MagicMock(),
            )
            result_obj = SimpleNamespace(ok=True, checked=37, failures=[])

            with (
                patch("main.create_runtime", return_value=runtime) as create_runtime_mock,
                patch(
                    "papertrail.reconciliation_regression.verify_reconciliation_regression",
                    return_value=result_obj,
                ),
            ):
                result = self.runner.invoke(
                    cli_main.app,
                    ["regression", "--export-date", "2026-04", "--profile", "default"],
                )

        self.assertEqual(result.exit_code, 0)
        create_runtime_mock.assert_called_once_with(
            profile_name="default",
            verbose=False,
            enable_client=False,
            probe_api=False,
        )


if __name__ == "__main__":
    unittest.main()
