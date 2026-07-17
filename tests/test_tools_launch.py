import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import review, shared


class FakeBlocks:
    def __init__(self):
        self.launch_kwargs = None

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return "launched"


class ToolLaunchTests(unittest.TestCase):
    def test_port_auto_uses_random_available_port(self):
        with patch("tools.shared._pick_random_port", return_value=54321):
            kwargs = shared.launch_kwargs_from_cli(["--port", "auto"])

        self.assertEqual(kwargs, {"server_port": 54321})

    def test_numeric_port_is_forwarded_to_gradio(self):
        app = FakeBlocks()

        result = shared.launch_blocks(app, css="css", js="js", argv=["--port", "51234"])

        self.assertEqual(result, "launched")
        self.assertEqual(
            app.launch_kwargs,
            {"css": "css", "js": "js", "server_port": 51234},
        )

    def test_invalid_port_fails_argument_parsing(self):
        with self.assertRaises(SystemExit):
            shared.launch_kwargs_from_cli(["--port", "autoish"])

    def test_review_launch_passes_explicit_export_path_and_argv(self):
        app = FakeBlocks()
        export_path = Path("/tmp/papertrail-export")

        with patch("tools.review.build_ui", return_value=app) as build_ui_mock:
            result = review.launch(export_path=export_path, argv=[])

        self.assertEqual(result, "launched")
        build_ui_mock.assert_called_once_with(export_path)
        self.assertEqual(
            app.launch_kwargs,
            {
                "css": "\n".join([shared.FULLSCREEN_CSS, review._CSS]),
                "js": "\n".join([shared.FULLSCREEN_JS, review._JS]),
            },
        )

    def test_review_export_folder_options_accepts_base_or_month_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_base = Path(tmpdir) / "export"
            month_folder = export_base / "2026-04"
            month_folder.mkdir(parents=True)

            with self.subTest("base"):
                base, choices, default = review._export_folder_options(export_base)
                self.assertEqual(base, export_base)
                self.assertEqual(choices, ["2026-04"])
                self.assertEqual(default, "2026-04")

            with self.subTest("folder"):
                base, choices, default = review._export_folder_options(month_folder)
                self.assertEqual(base, export_base)
                self.assertEqual(choices, ["2026-04"])
                self.assertEqual(default, "2026-04")


if __name__ == "__main__":
    unittest.main()
